"""HTTP layer for the Dream11 FDE prototype.

This module owns nothing about agent reasoning. It does exactly three things:

1. Construct a kernel session the way `backend/scripts/run_pack_a.py` / `run_pack_b.py` do
   (same LLM wiring, same TraceRecorder, same budgets) and drive it on its own worker thread,
   so `POST /api/session` returns immediately while the run blocks on a real fan answer.
2. Copy every kernel event - the session's `on_event` channel *and* the trace recorder's
   stream - into one per-session journal that SSE replays from the beginning and then follows.
   A late subscriber therefore never misses a card.
3. Meter bytes. The counter lives here, on the server, per session (see
   `backend/metering/bytes.py`); `watch` and `quiet` differ in what the server actually loads.

Nothing here reads, prints or copies a key: `.env` is loaded exactly like the runners do, and
the LLM classes pick their own variables up from the environment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Hinglish transcripts and receipt bodies on a cp1252 console otherwise raise
# UnicodeEncodeError inside a worker thread, which surfaces as a phantom agent failure.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,  # noqa: E402
                              StreamingResponse)
from pydantic import BaseModel, Field  # noqa: E402

from backend.kernel.factory import make_llm as make_factory_llm  # noqa: E402
from backend.kernel.factory import pool_status  # noqa: E402
from backend.kernel.llm import GeminiLLM  # noqa: E402
from backend.kernel.openrouter import OpenRouterLLM  # noqa: E402
from backend.kernel.schemas import Budget, new_id  # noqa: E402
from backend.kernel.trace import TraceRecorder  # noqa: E402
from backend.app.facts import build_facts  # noqa: E402
from backend.app.kernel_map import kernel_map  # noqa: E402
from backend.app.custom import ProvidedASR, creator_custom, gap_custom  # noqa: E402
from backend.metering.bytes import ByteMeter  # noqa: E402
from backend.packs.creator.domain import Feed  # noqa: E402
from backend.packs.creator.session import CreatorSession, SessionConfig  # noqa: E402
from backend.packs.gap import boundary as B  # noqa: E402
from backend.packs.gap.domain import GapFixtures, HeartbeatRow  # noqa: E402
from backend.packs.gap.session import GapConfig, GapSession  # noqa: E402
from backend.packs.gap.tools import BYTES_PER_SECOND_KB, CLIP_SECONDS  # noqa: E402

FIXTURES = ROOT / "backend" / "fixtures"
OUT = ROOT / "backend" / "out"
DIST = ROOT / "frontend" / "dist"
OUT.mkdir(parents=True, exist_ok=True)

RUN_MODES = ("offline-ci", "cached-demo", "live-demo", "safe-demo")
INTERRUPT_KINDS = ("ad_start", "ad_end", "stall", "resume", "join", "play", "gap")
VIEWED = ("play", "join")

# The kernel records `episode_start` from inside the orchestrator, which for Pack B only opens
# *after* the value-of-information question has already been put to the fan. The HTTP contract
# wants an episode to open the stream, so the server publishes its own `episode_start` first and
# relabels the kernel's authoritative frame `episode_open` (same fields, real episode id).
TRACE_RENAME = {"episode_start": "episode_open", "user_answer": "answer"}
# `ask_user` arrives on both channels; the session's own event carries the whole PendingQuestion
# (text + options + reason + blocking), which is exactly the contract shape.
TRACE_DROP = {"ask_user"}


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #


def run_mode() -> str:
    mode = (os.getenv("RUN_MODE") or "cached-demo").strip().lower()
    return mode if mode in RUN_MODES else "cached-demo"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def make_llm(mode: Optional[str] = None, backend: Optional[str] = None,
             layer: Optional[str] = None):
    """Which transport this session uses.

    `layer` is the per-session override the UI sends: `live` (a pool of real models),
    `replay` (recorded turns, instant, spends nothing) or `off` (no transport at all, to show
    the deterministic fallback on its own). Without an override, RUN_MODE decides.
    """
    mode = (mode or run_mode()).strip().lower()
    backend = (backend or os.getenv("LLM_BACKEND") or "openrouter").strip().lower()
    if layer in ("live", "replay", "off"):
        return make_factory_llm(layer=layer, backend=backend)
    return make_factory_llm(layer="live" if mode == "live-demo" else "replay", backend=backend)


def make_asr(mode: Optional[str] = None, llm=None, layer: Optional[str] = None):
    """ASR has no OpenRouter equivalent, so a live run borrows the Gemini backend only."""
    from backend.adapters.asr import CachedASR, GeminiASR

    mode = (mode or run_mode()).strip().lower()
    want_live = layer == "live" or (layer is None and mode == "live-demo")
    if want_live and (os.getenv("ASR") or "live") == "live" and os.getenv("GEMINI_API_KEY"):
        return GeminiASR(GeminiLLM(live=True, cassette_mode="off"))
    return CachedASR()


def cassette_count() -> int:
    d = Path(os.getenv("LLM_CASSETTE_DIR") or (ROOT / "tests" / "cassettes"))
    try:
        return sum(1 for _ in d.glob("*.json"))
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# fixtures (loaded once; both packs read the same feed)
# --------------------------------------------------------------------------- #

_FIX_LOCK = threading.RLock()
_GAP: Optional[GapFixtures] = None
_FEED: Optional[Feed] = None
_ROWS: Optional[list[dict]] = None
_GOLD: Optional[dict] = None


def gap_fixtures() -> GapFixtures:
    global _GAP
    with _FIX_LOCK:
        if _GAP is None:
            _GAP = GapFixtures.load()
        return _GAP


def creator_feed() -> Feed:
    global _FEED
    with _FIX_LOCK:
        if _FEED is None:
            _FEED = gap_fixtures().feed
        return _FEED


def transcript_rows() -> list[dict]:
    """The gold/cached transcript rows. Audio bytes are read lazily, per run, never at boot."""
    global _ROWS
    with _FIX_LOCK:
        if _ROWS is None:
            _ROWS = json.loads((FIXTURES / "transcript_expected.json")
                               .read_text(encoding="utf-8"))
            _ROWS.sort(key=lambda r: r["t_start"])
        return _ROWS


def gold_labels() -> dict:
    global _GOLD
    with _FIX_LOCK:
        if _GOLD is None:
            _GOLD = json.loads((FIXTURES / "gold_labels.json").read_text(encoding="utf-8"))
        return _GOLD


# --------------------------------------------------------------------------- #
# journal: the thing SSE replays
# --------------------------------------------------------------------------- #


class Journal:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._cv = threading.Condition()
        self.closed = False

    def append(self, type_: str, data: dict) -> dict:
        frame = {"seq": len(self.frames) + 1, "t": round(time.time(), 4),
                 "type": type_, "data": data}
        with self._cv:
            self.frames.append(frame)
            self._cv.notify_all()
        return frame

    def after(self, cursor: int) -> list[dict]:
        """Frames appended after `cursor` (a snapshot; the caller polls)."""
        with self._cv:
            return self.frames[cursor:]

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #


class WebGapSession(GapSession):
    """GapSession plus the one thing the HTTP layer needs: clip quotes feed the byte meter.

    `offer_clip` is wrapped on the instance and the registry is rebuilt from the same toolbox,
    so the kernel's guardrails, clamp and capability scoping are untouched.
    """

    def __init__(self, llm, fixtures: GapFixtures, trace: TraceRecorder,
                 on_event: Callable[[dict], None], cfg: Optional[GapConfig] = None,
                 auto_answer: Optional[dict] = None, meter: Optional[ByteMeter] = None,
                 ask_timeout_s: float = 60.0):
        self._meter = meter or ByteMeter()
        super().__init__(llm, fixtures, trace, on_event, cfg=cfg, auto_answer=auto_answer)
        # A jury reading a boundary question needs longer than a scripted demo fan, and the
        # session's own config is the single place the wait is read from.
        self.cfg.ask_timeout_s = max(self.cfg.ask_timeout_s, ask_timeout_s)
        original = self.toolbox.offer_clip

        def offer_clip(args):
            out = original(args)
            quote = (out or {}).get("quote") or {}
            kb = quote.get("bytes_estimate_kb") or 0
            if out and out.get("offerable") and kb:
                self._meter.note_offered_clip(quote.get("event_id", ""), kb,
                                              why=quote.get("load_policy", ""))
            return out

        self.toolbox.offer_clip = offer_clip
        self.registry = self.toolbox.build_registry()
        self.orch.registry = self.registry


class WebCreatorSession(CreatorSession):
    def __init__(self, llm, feed: Feed, asr, transcript_rows: list[dict],
                 trace: TraceRecorder, on_event: Callable[[dict], None],
                 cfg: Optional[SessionConfig] = None, meta=None,
                 meter: Optional[ByteMeter] = None, ask_timeout_s: float = 60.0):
        self._meter = meter or ByteMeter()
        self._ask_timeout_s = ask_timeout_s
        super().__init__(llm, feed, asr, transcript_rows, trace, on_event, cfg=cfg, meta=meta)

    def _wait_for_answer(self, pq, timeout_s: float):
        return super()._wait_for_answer(pq, max(timeout_s, self._ask_timeout_s))


@dataclass
class WebSession:
    id: str
    pack: str
    scenario_id: str
    mode: str                      # watch | quiet : a server behaviour, not a css class
    run_mode: str
    session: Any
    llm: Any
    asr: Any = None
    trace: TraceRecorder = None
    journal: Journal = field(default_factory=Journal)
    meter: ByteMeter = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    thread: Optional[threading.Thread] = None
    episode_id: str = ""
    rows: list = field(default_factory=list)      # creator: the segments this session will run
    started_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    auto_answer: bool = False
    pending: dict = field(default_factory=dict)     # last ask_user, for option_index
    answers: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    llm_layer: str = ""       # "replay" | "live" | "off" - the transport this run asked for
    llm_note: str = ""        # why that layer, in words a reviewer can read

    # -- transport report --------------------------------------------------- #

    def llm_models(self) -> list[str]:
        """The pool this run was built with, if it has one."""
        status = getattr(self.llm, "route_status", None)
        if callable(status):
            try:
                return list(status().get("members") or [])
            except Exception:
                return []
        name = getattr(self.llm, "model", "") or getattr(self.llm, "backend", "")
        return [name] if name else []

    def served_models(self) -> list[str]:
        """Which members actually answered, in order. Empty means nothing answered."""
        served = getattr(self.llm, "served", None)
        if served is not None:
            try:
                return list(served)
            except Exception:
                return []
        model = getattr(self.llm, "model", "")
        mode = getattr(self.llm, "mode_used", "")
        return [model] if (model and mode in ("live", "cassette")) else []

    # -- journal plumbing -------------------------------------------------- #

    def publish(self, type_: str, data: dict) -> dict:
        self.last_seen = time.time()
        data = dict(data or {})
        data.setdefault("episode_id", self.episode_id)
        return self.journal.append(type_, data)

    def on_event(self, evt: dict) -> None:
        """The session's own event channel (cards, asks, status, surfacing)."""
        try:
            evt = dict(evt or {})
            t = evt.pop("type", "status")
            if t == "ask_user":
                q = evt.get("question") or {}
                self.pending = {"question": q, "options": list(q.get("options") or [])}
                evt.setdefault("blocking", bool(q.get("blocking", True)))
            elif t == "card":
                self.cards.append(evt.get("card") or {})
            elif t == "status":
                pass
            self.publish(t, evt)
        except Exception as exc:            # a broken listener must never kill a run
            self.errors.append({"where": "on_event", "message": str(exc)[:200]})

    def on_trace(self, ev: dict) -> None:
        """The trace recorder's stream: every decision, tool call, guardrail and commit."""
        try:
            ev = dict(ev or {})
            kind = ev.pop("kind", "note")
            if kind in TRACE_DROP:
                return
            if kind == "commit" and not ev.get("ok", True):
                for v in ev.get("violations") or []:
                    self.publish("guardrail", {"rule": v.get("rule", ""),
                                               "why": v.get("detail", ""),
                                               "offending": v.get("offending", ""),
                                               "source": "commit"})
            if kind == "supervisor_error" and not getattr(self, "_failure_note", False):
                # Say why, once, in words a reviewer can act on. Three different situations end
                # here and they must not be described with one sentence: a replay that has no
                # recorded turn for this step, a live pool whose free quota is spent, and a live
                # call that simply failed. Only the middle one is a "no capacity" story.
                # The truthful note REPLACES the optimistic layer note ("the live model pool
                # was used"): once the pool dies that sentence is false, and shipping it next
                # to a fallback card is exactly the kind of claim this project does not make.
                err = str(ev.get("error") or "").strip()
                low = err.lower()
                tail = "; the kernel answered from the feed instead of inventing a line"
                if "no cassette" in low or "live disabled" in low or "replay" in low:
                    self.llm_note = (
                        "this demo replays recorded model turns and none was recorded for this "
                        "step" + tail)
                elif "quota" in low or "rate limit" in low or "429" in low:
                    cap = ""
                    status = getattr(self.llm, "route_status", None)
                    if callable(status):
                        try:
                            cap = str(status().get("capacity") or "").strip()
                        except Exception:
                            cap = ""
                    # "no capacity" may only be printed when the pool actually says so: a 429 on
                    # one member while another is healthy is a rate limit, not an empty pool,
                    # and the old wording printed the contradiction "no capacity (available)".
                    if cap in ("quota_exhausted", "cooling_down"):
                        self.llm_note = (f"the model pool had no capacity for this step "
                                         f"({cap.replace('_', ' ')})" + tail)
                    else:
                        self.llm_note = ("a model call for this step was rate-limited"
                                         + tail)
                else:
                    self.llm_note = (
                        "the model call for this step failed"
                        + (f" ({err[:110]})" if err else "") + tail)
                self._failure_note = True
            type_ = TRACE_RENAME.get(kind, kind)
            if type_ in ("episode_open", "episode_end", "answer", "tool_call", "card"):
                eid = ev.get("episode_id")
                if eid:
                    self.episode_id = eid
            if type_ == "answer":
                self.pending = {k: v for k, v in self.pending.items() if k == "query"}
            ev.setdefault("why", ev.get("reason", ""))
            self.publish(type_, ev)
        except Exception as exc:
            self.errors.append({"where": "on_trace", "message": str(exc)[:200]})

    # -- views -------------------------------------------------------------- #

    def budget_used(self) -> dict:
        cfg = getattr(self.session, "cfg", None)
        budget = getattr(cfg, "budget", None)
        tool_calls = len(getattr(self.session.registry, "call_log", []) or [])
        used = {"llm_calls": getattr(self.llm, "calls", 0),
                "tool_calls": tool_calls,
                "trace_events": len(self.trace.events),
                "cards": len(self.cards),
                "bytes_kb": self.meter.kb(self.meter.total_bytes),
                "would_have_kb": self.meter.kb(self.meter.would_have_bytes),
                "clip_loads": self.meter.clip_loads,
                "refusals": self.meter.refusals,
                "ask_timeouts": sum(1 for e in self.journal.frames
                                    if e["type"] == "status" and "no answer" in str(e["data"]))}
        if budget is not None:
            used["budget"] = budget.model_dump()
        return used


# --------------------------------------------------------------------------- #
# session store
# --------------------------------------------------------------------------- #


class Store:
    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()
        self.max_sessions = _env_int("MAX_SESSIONS", 12)
        self.ttl_s = float(_env_int("SESSION_TTL_S", 1800))

    def put(self, web: WebSession) -> None:
        with self._lock:
            self._sessions[web.id] = web
            self._sweep_locked()

    def get(self, sid: str) -> Optional[WebSession]:
        with self._lock:
            web = self._sessions.get(sid)
            if web:
                web.last_seen = time.time()
            return web

    def sweep(self) -> list[str]:
        with self._lock:
            return self._sweep_locked()

    def _sweep_locked(self) -> list[str]:
        now = time.time()
        dropped = []
        for sid, web in list(self._sessions.items()):
            idle = now - (web.finished_at or web.last_seen)
            if idle > self.ttl_s or (len(self._sessions) >= self.max_sessions
                                     and web.finished_at is not None):
                self._sessions.pop(sid, None)
                dropped.append(sid)
        if len(self._sessions) > self.max_sessions:      # nothing finished yet: evict oldest
            for sid, _ in sorted(self._sessions.items(),
                                 key=lambda kv: kv[1].started_at)[:len(self._sessions)
                                                                 - self.max_sessions]:
                self._sessions.pop(sid, None)
                dropped.append(sid)
        return dropped

    def all(self) -> list[dict]:
        with self._lock:
            return [{"session_id": s.id, "pack": s.pack, "scenario": s.scenario_id,
                     "mode": s.mode, "run_mode": s.run_mode, "frames": len(s.journal.frames),
                     "cards": len(s.cards), "bytes_kb": s.meter.kb(s.meter.total_bytes),
                     "done": s.finished_at is not None} for s in self._sessions.values()]


STORE = Store()


# --------------------------------------------------------------------------- #
# building + running a session
# --------------------------------------------------------------------------- #


def _budget_llm(n: int) -> Budget:
    return Budget(max_llm_calls=n, max_tool_calls=12, max_ms=_env_int("BUDGET_MS", 240_000),
                  max_specialist_rounds=8)


def build_gap(web_id: str, scenario_id: str, mode: str, run_mode_: str,
              ask_timeout: float, auto_answer: Optional[dict],
              layer: Optional[str] = None,
              fixtures: Optional[GapFixtures] = None) -> WebSession:
    fixtures = fixtures or gap_fixtures()
    web = WebSession(id=web_id, pack="gap", scenario_id=scenario_id, mode=mode,
                     run_mode=run_mode_, llm=None, session=None)
    web.meter = ByteMeter(on_record=lambda f: web.publish("bytes", f))
    web.trace = TraceRecorder(OUT / f"http_{web_id}.jsonl", on_event=web.on_trace)
    llm = make_llm(run_mode_, layer=layer)
    web.llm = llm
    web.llm_layer = (layer or ("live" if run_mode_ == "live-demo" else "replay")).lower()
    sess = WebGapSession(llm, fixtures, web.trace, web.on_event,
                         cfg=GapConfig(budget=_budget_llm(_env_int("LLM_BUDGET", 14)),
                                       ask_timeout_s=ask_timeout),
                         auto_answer=auto_answer, meter=web.meter, ask_timeout_s=ask_timeout)
    sess.mode = mode
    web.session = sess
    return web


def build_creator(web_id: str, scenario_id: str, mode: str, run_mode_: str,
                  ask_timeout: float, auto_answer: Optional[dict],
                  layer: Optional[str] = None,
                  rows: Optional[list[dict]] = None) -> WebSession:
    feed = creator_feed()
    rows = rows if rows is not None else transcript_rows()
    web = WebSession(id=web_id, pack="creator", scenario_id=scenario_id, mode=mode,
                     run_mode=run_mode_, llm=None, session=None)
    web.meter = ByteMeter(on_record=lambda f: web.publish("bytes", f))
    web.trace = TraceRecorder(OUT / f"http_{web_id}.jsonl", on_event=web.on_trace)
    llm = make_llm(run_mode_, layer=layer)
    # Typed text is never passed off as transcription: the rows carry `provided_text` and the
    # ASR stand-in reports source="typed", which is what the badge prints.
    asr = (ProvidedASR(rows) if any(r.get("provided_text") for r in rows)
           else make_asr(run_mode_, layer=layer))
    web.llm, web.asr = llm, asr
    web.llm_layer = (layer or ("live" if run_mode_ == "live-demo" else "replay")).lower()
    web.rows = rows
    sess = WebCreatorSession(llm, feed, asr, rows, web.trace, web.on_event,
                             cfg=SessionConfig(budget=_budget_llm(_env_int("LLM_BUDGET", 12)),
                                               answer_wait_s=ask_timeout,
                                               auto_answer=auto_answer),
                             meter=web.meter, ask_timeout_s=ask_timeout)
    sess.mode = mode
    web.session = sess
    return web


def _load_audio(row: dict) -> dict:
    r = dict(row)
    wav = r.get("wav")
    if wav and "audio_bytes" not in r:
        p = FIXTURES / wav
        r["audio_bytes"] = p.read_bytes() if p.exists() else b""
    return r


def run_gap(web: WebSession) -> None:
    sc = web.session.fixtures.scenarios[web.scenario_id]
    web.publish("episode_start", {"episode_id": web.episode_id or new_id("ep"),
                                  "pack": "gap", "trigger": "fan_returned",
                                  "objective": ("reconstruct the missed interval with evidence "
                                                "and pick the safest useful recovery, never a "
                                                "ball at or after the fan's resume position"),
                                  "scenario": sc.id, "scenario_name": sc.name,
                                  "resume_cursor": sc.resume_cursor,
                                  "resume_over": web.session.feed.by_id[sc.resume_cursor].over_label,
                                  "player_mode": web.mode,
                                  "note": "published by the HTTP layer; the kernel's own "
                                          "episode_open frame carries the authoritative id"})
    b = B.estimate(web.session.feed, sc)
    web.publish("boundary", {"candidates": b.candidates,
                             "candidate_overs": b.labels(web.session.feed),
                             "confidence": b.confidence, "notes": b.notes,
                             "needs_fan_input": b.needs_fan_input,
                             "clean_last_position": b.clean_last_position,
                             "resume_over": web.session.feed.by_id[sc.resume_cursor].over_label})
    web.session.run_scenario(sc.id)


def run_creator(web: WebSession) -> None:
    wanted = None if web.scenario_id in ("ALL", "all", "", "custom_1") else {web.scenario_id}
    source = web.rows or transcript_rows()
    rows = [r for r in source if wanted is None or r["id"] in wanted]
    web.publish("episode_start", {"episode_id": web.episode_id or new_id("ep"),
                                  "pack": "creator", "trigger": "creator_stream",
                                  "objective": ("turn the creator's live speech into attributed, "
                                                "feed-verified insight without spoiling or shouting"),
                                  "segments": [r["id"] for r in rows],
                                  "player_mode": web.mode,
                                  "note": "published by the HTTP layer; the kernel's own "
                                          "episode_open frame carries the authoritative id"})
    for r in rows:
        web.session.advance(float(r["t_end"]))
        web.session.process(_load_audio(r))
    web.session.advance(240.0)          # closes the clock so open predictions settle
    q = web.pending.get("query")
    if q:
        web.session.run_query(q)


def start_run(web: WebSession) -> None:
    target = run_gap if web.pack == "gap" else run_creator

    def body() -> None:
        try:
            target(web)
        except Exception as exc:
            web.errors.append({"where": web.pack, "message": f"{type(exc).__name__}: {exc}"})
            web.publish("error", {"where": f"{web.pack}:{web.scenario_id}",
                                  "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
        finally:
            web.publish("done", {"cards": len(web.cards),
                                 "llm_calls": getattr(web.llm, "calls", 0),
                                 "llm_mode": getattr(web.llm, "mode_used", "unknown"),
                                 "trace_events": len(web.trace.events),
                                 "badges": web.session.badges(),
                                 "bytes": web.meter.snapshot_short()
                                 if hasattr(web.meter, "snapshot_short") else web.meter.snapshot(),
                                 "budget": web.budget_used(),
                                 "errors": web.errors[-3:]})
            web.finished_at = time.time()
            web.journal.close()

    web.thread = threading.Thread(target=body, daemon=True, name=f"run-{web.id}")
    web.thread.start()


# --------------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------------- #


class SessionRequest(BaseModel):
    pack: str = "gap"
    # No pack-neutral default exists: "S1" is a gap scenario id and is meaningless to the creator
    # pack, which would answer a bare POST with 400. Empty means "whatever this pack's own entry
    # point is", resolved per pack below.
    scenario: str = ""
    mode: str = "watch"
    run_mode: Optional[str] = None
    auto_answer: Optional[bool] = None
    query: Optional[str] = None            # creator pack only: a fan transcript query
    # Per-session transport: "live" (real models, a pool), "replay" (recorded) or "off".
    llm: Optional[str] = None
    # Custom situations the fixtures do not contain.
    resume_event_id: str = ""              # gap: any ball id, e.g. "evt_072"
    interruption: str = "ad"               # gap: ad | stall | app | late
    text: str = ""                         # creator: what the creator just said


class AnswerRequest(BaseModel):
    text: Optional[str] = None
    option_index: Optional[int] = None


class InterruptRequest(BaseModel):
    kind: str = "ad_start"
    event_id: str = ""
    note: str = ""
    t: Optional[float] = None


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #

app = FastAPI(title="Dream11 FDE prototype", version="1.0",
              description="Two agent packs behind one journal: SSE + server-side byte metering.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Last-Event-ID"])

PLACEHOLDER = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dream11 FDE - UI not built</title><style>
body{font:15px/1.5 system-ui;background:#0b1020;color:#e8ecff;margin:0;padding:28px}
code{background:#182042;padding:2px 6px;border-radius:4px}a{color:#8ab4ff}</style></head>
<body><h1>API is live - UI is not built yet</h1>
<p><code>frontend/dist</code> is missing, so this placeholder is standing in for the app.</p>
<p>Drive it over HTTP directly: <a href="/docs">/docs</a> |
<a href="/api/health">/api/health</a> | <a href="/api/manifest">/api/manifest</a></p>
<pre>curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/session -H 'content-type: application/json' \
     -d '{"pack":"gap","scenario":"S1","mode":"watch"}'
curl -N localhost:8000/api/session/&lt;sid&gt;/events</pre></body></html>"""


def _packs_manifest() -> list[dict]:
    gap = gap_fixtures()
    gold = gold_labels()
    scenarios = [{"id": s.id, "name": s.name, "blurb": s.blurb, "expected": s.expected,
                  "interruption_type": s.interruption_type,
                  "resume_over": gap.feed.by_id[s.resume_cursor].over_label,
                  "asks": s.expected.get("asks")} for s in gap.scenarios.values()]
    utter = gold.get("utterances", {})
    crows = [{"id": r["id"], "name": f"{r['id']} @ {r['t_start']:.0f}s",
              "blurb": (r["text"][:88] + "...") if len(r["text"]) > 88 else r["text"],
              "expected": utter.get(r["id"], {})} for r in transcript_rows()]
    creator_scen = [{"id": "ALL", "name": "whole creator stream",
                     "blurb": f"{len(crows)} segments, clock 0-240s, predictions settle on the feed",
                     "expected": {"cards_min": gold.get("expected_cards_min"),
                                  "cards_max": gold.get("expected_cards_max"),
                                  "segments": [r["id"] for r in crows]}}] + crows
    return [
        {"id": "gap", "route": "/gap", "title": "Gap Receipt",
         "subtitle": "the fan comes back mid-chase: rebuild only what they missed, "
                     "never a ball past their cursor",
         "scenarios": scenarios},
        {"id": "creator", "route": "/creator", "title": "Watch Along",
         "subtitle": "creator commentary verified against the feed, predictions tracked "
                     "and settled, opinion kept separate from fact",
         "scenarios": creator_scen},
    ]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "run_mode": run_mode(),
            "llm_backend": (os.getenv("LLM_BACKEND") or "openrouter").strip().lower(),
            "cassettes": cassette_count(), "sessions": len(STORE.all()),
            "frontend_built": DIST.is_dir(),
            "asr": "live" if run_mode() == "live-demo" else "cached",
            "ask_timeout_s": float(_env_int("ASK_TIMEOUT_S", 60))}


@app.get("/api/manifest")
def manifest() -> dict:
    return {"run_mode": run_mode(),
            "backend": (os.getenv("LLM_BACKEND") or "openrouter").strip().lower(),
            "cassettes": cassette_count(),
            "match": creator_feed().meta.model_dump(),
            "packs": _packs_manifest()}


def _cassette_count() -> int:
    """Counted from the store itself, so the page cannot claim a number the disk disagrees with."""
    try:
        return len(list(Path(os.getenv("LLM_CASSETTE_DIR", str(ROOT / "tests" / "cassettes"))).glob("*.json")))
    except Exception:
        return 0


@app.get("/api/models")
def models() -> dict:
    """Which real models this deployment can call, and what it would cost to try.

    Read-only: building the pool constructs clients, it does not send a request. `live_ready`
    is false when no key is configured, which is the one thing a UI needs to know before
    offering a live switch.
    """
    status = pool_status(live=True)
    return {
        "ok": bool(status.get("ok")),
        "error": status.get("error", ""),
        "members": status.get("members", []),
        "capacity": status.get("capacity", ""),
        "rotate_per_call": status.get("rotate", True),
        "cooldown_s": status.get("cooldown_s", 0),
        "gemini_chain": status.get("gemini_chain", []),
        "openrouter_chain": status.get("openrouter_chain", []),
        "default_layer": "live" if run_mode() == "live-demo" else "replay",
        "layers": [
            {"id": "replay", "label": "Recorded",
             "note": f"{_cassette_count()} recorded model turns; instant, spends nothing"},
            {"id": "live", "label": "Live models",
             "note": "real calls across the pool, rotated per turn; slower"},
            {"id": "off", "label": "No model",
             "note": "the deterministic fallback only, to show what stays standing"},
        ],
    }


@app.get("/api/kernel")
def kernel() -> dict:
    """The roster the agent-room animation draws: introspected, never illustrated by hand.

    Same rule as /api/facts - session-free and read-only, so opening a page that shows the org
    chart cannot start a run or spend a token.
    """
    return kernel_map()


@app.get("/api/facts")
def facts() -> dict:
    """Every figure the interface is allowed to print, read straight from the fixtures.

    Read-only and session-free: opening the home page must not create an agent run. The
    scoreboard, the ball-by-ball strip, the scenario cards, the scoreboard-frame reader and the
    real-vs-simulated table all come from here (see backend/app/facts.py).
    """
    mode = run_mode()
    return build_facts(run_mode=mode,
                       llm_backend=(os.getenv("LLM_BACKEND") or "openrouter").strip().lower(),
                       cassettes=cassette_count())


@app.get("/api/media/frame/{frame_id}")
def frame_image(frame_id: str) -> Any:
    """Serve one rendered scoreboard fixture so a reader can see what the OCR reads."""
    for f in gap_fixtures().frames:
        if f.frame_id == frame_id:
            return FileResponse(FIXTURES / f.path, media_type="image/png",
                                headers={"Cache-Control": "public, max-age=3600"})
    return JSONResponse({"error": f"no frame {frame_id}"}, status_code=404)


@app.post("/api/session")
def create_session(req: SessionRequest) -> JSONResponse:
    pack = (req.pack or "gap").strip().lower()
    mode = (req.mode or "watch").strip().lower()
    if mode not in ("watch", "quiet"):
        return JSONResponse({"error": f"unknown mode {mode}"}, status_code=400)
    run = (req.run_mode or run_mode()).strip().lower()
    if run not in RUN_MODES:
        return JSONResponse({"error": f"unknown run_mode {run}"}, status_code=400)
    ask_timeout = float(_env_int("ASK_TIMEOUT_S", 60))

    layer = (req.llm or "").strip().lower() or None
    if layer not in (None, "live", "replay", "off"):
        return JSONResponse({"error": f"unknown llm layer {layer}",
                             "known": ["live", "replay", "off"]}, status_code=400)
    # A situation nobody recorded has no cassette to replay, so leaving it on the replay layer
    # can only end in the fallback. When a key is configured, a custom case runs on the real
    # models unless the caller explicitly asked for something else. The response says which.
    layer_note = ""
    if layer is None and req.scenario.strip().lower() == "custom" and run != "offline-ci":
        ready = pool_status(live=True)
        if ready.get("ok"):
            layer = "live"
            layer_note = "custom case: no recording exists, so the live model pool was used"

    scenario = req.scenario.strip()
    if pack == "gap" and not scenario:
        scenario = "S1"        # the gap demo's entry point: the uncertain ad gap that asks
    auto = req.auto_answer if req.auto_answer is not None else run == "offline-ci"
    web_id = f"s_{uuid4()}"
    if pack == "gap":
        fixtures = gap_fixtures()
        if scenario == "custom":
            # A situation nobody scripted: the request names the ball and what happened, and the
            # boundary is re-estimated by the same rules from synthesised heartbeats.
            try:
                sc = gap_custom(fixtures, req.resume_event_id, req.interruption)
            except ValueError as exc:
                return JSONResponse({"error": str(exc),
                                     "known_balls": [e.id for e in fixtures.feed.events]},
                                    status_code=400)
            fixtures = GapFixtures(fixtures.feed, fixtures.frames,
                                   list(fixtures.scenarios.values()) + [sc])
            scenario = sc.id
            aa = dict(sc.fan_answers) if auto else None
        elif scenario not in fixtures.scenarios:
            return JSONResponse({"error": f"unknown gap scenario {scenario}",
                                 "known": list(fixtures.scenarios) + ["custom"]}, status_code=400)
        else:
            aa = dict(fixtures.scenarios[scenario].fan_answers) if auto else None
        try:
            web = build_gap(web_id, scenario, mode, run, ask_timeout, aa, layer=layer,
                            fixtures=fixtures)
        except RuntimeError as exc:                 # no provider configured
            return JSONResponse({"error": str(exc)}, status_code=503)
        except Exception as exc:
            return JSONResponse({"error": f"session build failed: {exc}"}, status_code=500)
    elif pack == "creator":
        rows = None
        if scenario == "custom":
            # Text the user typed takes the place of audio: triage, the verifier, the prediction
            # ledger and the commit gate all run against the real feed.
            try:
                rows = [creator_custom(req.text)]
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            scenario = "custom_1"
        else:
            known = {r["id"] for r in transcript_rows()} | {"ALL", "all", ""}
            if scenario not in known:
                return JSONResponse({"error": f"unknown creator scenario {scenario}",
                                     "known": ["ALL", "custom"] + sorted(known - {"ALL", "all", ""})},
                                    status_code=400)
        if auto:
            aa = {"which": "the one just now", "would you": "yes", "?": "yes"}
        else:
            aa = None
        try:
            web = build_creator(web_id, scenario or "ALL", mode, run, ask_timeout, aa,
                                layer=layer, rows=rows)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except Exception as exc:
            return JSONResponse({"error": f"session build failed: {exc}"}, status_code=500)
        if req.query:
            web.pending["query"] = req.query
    else:
        return JSONResponse({"error": f"unknown pack {pack}", "known": ["gap", "creator"]},
                            status_code=400)

    web.llm_note = layer_note
    STORE.put(web)
    web.publish("session", {"session_id": web.id, "pack": pack, "scenario": web.scenario_id,
                            "mode": mode, "run_mode": run, "llm_backend": web.llm.backend,
                            "llm_layer": layer or ("live" if run == "live-demo" else "replay"),
                            "llm_note": layer_note,
                            "ask_timeout_s": ask_timeout,
                            "bytes_endpoint": f"/api/media/clip/{{event_id}}?sid={web.id}"})
    start_run(web)
    return JSONResponse({"session_id": web.id, "pack": pack, "scenario": web.scenario_id,
                         "mode": mode, "run_mode": run,
                         "llm_layer": layer or ("live" if run == "live-demo" else "replay"),
                         "llm_note": layer_note,
                         "events": f"/api/session/{web.id}/events"})


@app.get("/api/session")
def list_sessions() -> dict:
    STORE.sweep()
    return {"sessions": STORE.all()}


@app.get("/api/session/{sid}")
def session_state(sid: str) -> JSONResponse:
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    return JSONResponse({"session_id": web.id, "pack": web.pack, "scenario": web.scenario_id,
                         "mode": web.mode, "run_mode": web.run_mode,
                         "frames": len(web.journal.frames), "done": web.finished_at is not None,
                         "badges": web.session.badges(), "cards": web.cards,
                         "pending_question": web.pending.get("question"),
                         "bytes": web.meter.snapshot(), "budget": web.budget_used(),
                         "errors": web.errors,
                         # The same layer report the SSE session frame carries. Without it a
                         # client that polls, or a page that reloads mid-run, could not say
                         # which transport answered - and the badge would be a guess.
                         "llm_layer": web.llm_layer, "llm_note": web.llm_note,
                         "llm_models": web.llm_models(),
                         "served_models": web.served_models()})


def _sse(frame: dict) -> str:
    body = {"seq": frame["seq"], "t": frame["t"], "type": frame["type"], **frame["data"]}
    return (f"id: {frame['seq']}\nevent: {frame['type']}\n"
            f"data: {json.dumps(body, ensure_ascii=False, default=str)}\n\n")


@app.get("/api/session/{sid}/events")
async def events(sid: str, request: Request, cursor: int = -1) -> StreamingResponse:
    """Replay the journal from the beginning, then follow it live.

    It keeps following a little past `done`, on purpose: in Pack B the clip tile is offered *on
    the receipt card*, so the fan's tap - and the `bytes` frame it produces - arrives after the
    run has finished. Cutting the stream at `done` would hide the byte counter from the very
    panel it belongs to. `SSE_FOLLOW_AFTER_DONE=0` restores the strict reading.
    """
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    start = 0 if cursor < 0 else max(0, min(cursor, len(web.journal.frames)))
    if request.headers.get("last-event-id", "").isdigit():
        start = max(start, int(request.headers["last-event-id"]))
    follow_after_done = os.getenv("SSE_FOLLOW_AFTER_DONE", "1") != "0"
    max_stream_s = float(_env_int("SSE_MAX_S", 1800))

    async def stream() -> Iterator[str]:
        cur = start
        opened = time.time()
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            frames = web.journal.after(cur)
            if frames:
                for f in frames:
                    cur = f["seq"]
                    yield _sse(f)
                idle = 0.0
            else:
                idle += 0.4
                if idle >= 15.0:
                    idle = 0.0
                    yield ": ping\n\n"          # keeps proxies from dropping a blocked ask
                await asyncio.sleep(0.4)
            if time.time() - opened > max_stream_s:
                return
            if web.finished_at is not None and cur >= len(web.journal.frames):
                last = web.journal.frames[-1]["type"] if web.journal.frames else ""
                if last == "done" and not follow_after_done:
                    return

    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@app.post("/api/session/{sid}/answer")
def answer(sid: str, req: AnswerRequest) -> JSONResponse:
    """Resolves a blocking `ask_user`: the fan answers over HTTP, the run thread wakes up."""
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    pending_q = web.pending.get("question") or {}
    if not pending_q:
        # A 200 here would tell the fan their answer was heard by a run that never asked, and a
        # client that lost the SSE stream would never learn the episode moved on.
        return JSONResponse({"error": "no question is pending for this session",
                             "hint": "questions arrive as `ask_user` frames on the events "
                                     "stream; poll /api/session/{sid} for pending_question"},
                            status_code=409)
    text = (req.text or "").strip()
    if not text and req.option_index is not None:
        options = web.pending.get("options") or []
        if not 0 <= req.option_index < len(options):
            return JSONResponse({"error": f"option_index {req.option_index} out of range",
                                 "options": options}, status_code=400)
        text = str(options[req.option_index])
    if not text:
        return JSONResponse({"error": "provide text or option_index"}, status_code=400)
    web.answers.append({"text": text, "t": time.time()})
    web.publish("answer_accepted", {"question": (web.pending.get("question") or {}).get("text", ""),
                                     "answer": text, "source": "fan over http"})
    with web.lock:
        try:
            web.session.provide_answer(text)
        except Exception as exc:
            return JSONResponse({"error": f"answer rejected: {exc}"}, status_code=500)
    web.pending = {k: v for k, v in web.pending.items() if k == "query"}
    return JSONResponse({"ok": True, "answer": text})


@app.post("/api/session/{sid}/interrupt")
def interrupt(sid: str, req: InterruptRequest) -> JSONResponse:
    """Player heartbeat. The boundary estimator reads the log, so this moves the gap window."""
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    kind = (req.kind or "").strip()
    if kind not in INTERRUPT_KINDS:
        return JSONResponse({"error": f"unknown interrupt {kind}",
                             "known": list(INTERRUPT_KINDS)}, status_code=400)
    t = float(req.t if req.t is not None else time.time() - web.started_at)
    event_id = req.event_id
    if event_id and event_id not in web.session.feed.index:
        return JSONResponse({"error": f"unknown event_id {event_id}"}, status_code=400)

    # `t_player` not `t`: TraceRecorder stamps its own wall-clock `t`, and a heartbeat time
    # measured in stream-seconds would silently overwrite it.
    frame_data: dict = {"kind": kind, "t_player": round(t, 2), "event_id": event_id,
                        "note": req.note, "player_mode": web.mode, "source": "jury pane"}
    with web.lock:
        # `trace.record` already mirrors into the journal through the recorder's own listener,
        # so the heartbeat is in the trace AND the stream from one write, never two.
        # (`record(kind, **payload)` reserves the word `kind` for the event name, so the
        # player's heartbeat type travels as `heartbeat`.)
        payload = dict(frame_data)
        payload["heartbeat"] = payload.pop("kind")
        web.trace.record("interrupt", **payload)
        seq = web.journal.frames[-1]["seq"] if web.journal.frames else 0
        if web.pack == "gap" and web.session.scenario is not None:
            sc = web.session.scenario
            if not event_id:
                # A heartbeat always paints *some* ball; if the jury names only the moment,
                # resolve it to the last event at or before that time.
                prior = [e for e in web.session.feed.events if e.t_wall <= t]
                event_id = prior[-1].id if prior else web.session.feed.events[0].id
                frame_data["event_id"] = event_id
            sc.heartbeats.append(HeartbeatRow(t=t, event_id=event_id, kind=kind, note=req.note))
            b = B.estimate(web.session.feed, sc)
            frame_data.update({"candidates": b.candidates,
                               "candidate_overs": b.labels(web.session.feed),
                               "confidence": b.confidence, "notes": b.notes})
            if web.session.boundary_source != "fan" and not web.cards:
                # The fan's own words outrank the log, and a committed receipt is never rewritten.
                web.session.boundary = b
                web.session.chosen_boundary = b.candidates[0] if b.candidates else ""
                web.session.released_events = set()
                frame_data["applied_to_session"] = True
            web.publish("boundary", {"candidates": b.candidates,
                                     "candidate_overs": b.labels(web.session.feed),
                                     "confidence": b.confidence, "notes": b.notes,
                                     "source": "interrupt", "kind": kind})
    return JSONResponse({"ok": True, "seq": seq, **frame_data})


@app.get("/api/session/{sid}/trace")
def trace_of(sid: str) -> JSONResponse:
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    sess = web.session
    return JSONResponse({"session_id": web.id, "pack": web.pack, "scenario": web.scenario_id,
                         "mode": web.mode, "run_mode": web.run_mode,
                         "events": web.trace.events,
                         "counts": web.trace.counts(),
                         "journal": {"frames": len(web.journal.frames),
                                     "closed": web.journal.closed},
                         "badges": sess.badges(),
                         "cards": [c.model_dump() if hasattr(c, "model_dump") else c
                                   for c in sess.cards],
                         "budget_used": web.budget_used(),
                         "bytes": web.meter.snapshot(),
                         "boundary": ({"candidates": sess.boundary.candidates,
                                       "candidate_overs": sess.boundary.labels(sess.feed),
                                       "confidence": sess.boundary.confidence,
                                       "notes": sess.boundary.notes,
                                       "chosen": sess.chosen_boundary,
                                       "source": sess.boundary_source}
                                      if getattr(sess, "boundary", None) else None),
                         "released_events": sorted(sess.released_events),
                         "answers": web.answers,
                         "errors": web.errors},
                        )


@app.get("/api/session/{sid}/bytes")
def bytes_of(sid: str) -> JSONResponse:
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    return JSONResponse(web.meter.snapshot())


# --------------------------------------------------------------------------- #
# media: the only place a clip byte is ever moved
# --------------------------------------------------------------------------- #

CLIP_DIR = FIXTURES / "clips"
CHUNK = 65_536


def _real_clip_file(event_id: str) -> Optional[Path]:
    """A clip fixture exists only if someone authored one - never invent one silently."""
    for ext in (".mkv", ".webm", ".mp4"):
        p = CLIP_DIR / f"{event_id}{ext}"
        if p.is_file():
            return p
    return None


def _clip_chunks(event_id: str, total: int, meta: dict) -> Iterator[bytes]:
    """A deterministic Matroska-shaped payload for an event.

    There is no encoded video in the fixture set (build_fixtures.py makes wav + png only), so
    the clip is a priced, byte-exact stand-in: a real EBML magic, the ball's own metadata, then
    a hash-chained body so the same event always costs the same bytes. The meter counts what
    leaves the process, which is the only number the UI is allowed to show.
    """
    header = (b"\x1a\x45\xdf\xa3" +
              json.dumps({"prototype_clip": True, "container": "matroska-like",
                          **meta}, default=str).encode("utf-8"))
    yield header
    remaining = total - len(header)
    state = hashlib.sha256(f"clip:{event_id}".encode()).digest()
    while remaining > 0:
        n = min(CHUNK, remaining)
        state = hashlib.sha256(state).digest()
        block = (state * (n // len(state) + 1))[:n]
        yield block
        remaining -= n


@app.get("/api/media/clip/{event_id}")
def media_clip(event_id: str, sid: str = "") -> Any:
    """Streamed through the server's byte counter. A quiet session is never charged a byte."""
    web = STORE.get(sid)
    if web is None:
        return JSONResponse({"error": "unknown or expired session",
                             "why": "media needs ?sid= so bytes are attributed to the "
                                    "server-side counter, not the client"}, status_code=403)
    endpoint = f"/api/media/clip/{event_id}"
    sess = web.session
    ev = sess.feed.by_id.get(event_id)
    price = int(CLIP_SECONDS * BYTES_PER_SECOND_KB * 1024)
    if ev is None:
        web.meter.refuse(endpoint, 0, f"unknown event {event_id}")
        return JSONResponse({"why": f"unknown event {event_id}"}, status_code=404)

    if web.mode == "quiet":
        frame = web.meter.refuse(endpoint, price, "quiet mode never loads clip bytes")
        return JSONResponse({"why": "quiet mode never loads clip bytes",
                             "session_mode": "quiet", "event_id": event_id,
                             "over_label": ev.over_label, "would_have_kb": frame["would_have_kb"],
                             "charged_kb": 0.0}, status_code=403)

    # Spoiler guard: the resume cursor is the product's whole promise, so it is enforced on the
    # media route too, not only in the agent's clamp.
    sc = getattr(sess, "scenario", None)
    if sc is not None and getattr(sc, "resume_cursor", ""):
        resume = sess.feed.by_id.get(sc.resume_cursor)
        if resume is not None and sess.feed.index[event_id] >= sess.feed.index[resume.id]:
            web.meter.refuse(endpoint, price, f"{ev.over_label} is at or past the fan's "
                                              f"resume cursor {resume.over_label}")
            return JSONResponse({"why": f"spoiler: {ev.over_label} is at or past your resume "
                                         f"position {resume.over_label}",
                                 "event_id": event_id}, status_code=403)

    meta = {"event_id": ev.id, "over_label": ev.over_label, "kind": ev.kind,
             "score": ev.score, "text": ev.text[:90], "duration_s": CLIP_SECONDS,
             "bytes_estimate_kb": CLIP_SECONDS * BYTES_PER_SECOND_KB, "session": web.id}
    real = _real_clip_file(event_id)
    if real is not None:
        size = real.stat().st_size
        ctype = mimetypes.guess_type(real.name)[0] or "video/x-matroska"
        chunks: Iterable[bytes] = _read_file(real)
        reason = f"clip fixture {real.name} loaded after the fan tapped it ({ev.over_label})"
    else:
        size = price
        ctype = "video/x-matroska"
        chunks = _clip_chunks(event_id, price, meta)
        reason = (f"12s clip at {BYTES_PER_SECOND_KB} KB/s priced and loaded after the fan "
                  f"tapped it ({ev.over_label})")

    gen = web.meter.counted_stream(chunks, endpoint, reason, event_id=event_id,
                                   over_label=ev.over_label, clip_kb=size / 1024.0)
    return StreamingResponse(gen, media_type=ctype, headers={
        "Content-Length": str(size), "Accept-Ranges": "bytes",
        "X-Session-Bytes-Kb": str(web.meter.kb(web.meter.total_bytes)),
        "X-Clip-Kb": str(round(size / 1024.0, 1))})


def _read_file(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                return
            yield b


# --------------------------------------------------------------------------- #
# frontend
# --------------------------------------------------------------------------- #


@app.get("/{path:path}")
def spa(path: str) -> Any:
    if path.startswith("api/"):
        return JSONResponse({"error": f"no route {path}"}, status_code=404)
    if not DIST.is_dir():
        return _html(PLACEHOLDER)
    rel = (DIST / (path or "index.html")).resolve()
    try:
        rel.relative_to(DIST.resolve())
    except ValueError:
        return JSONResponse({"error": "bad path"}, status_code=400)
    if rel.is_file():
        return FileResponse(rel)
    index = DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return _html(PLACEHOLDER)


def _html(markup: str) -> HTMLResponse:
    return HTMLResponse(markup)


def _boot_note() -> None:
    mode = run_mode()
    print(f"[http] run_mode={mode} llm_backend={(os.getenv('LLM_BACKEND') or 'openrouter')} "
          f"cassettes={cassette_count()} frontend_dist={DIST.is_dir()} "
          f"keys_loaded={bool(os.getenv('OPENROUTER_API_KEY') or os.getenv('GEMINI_API_KEY'))}",
          file=sys.stderr)


_boot_note()
