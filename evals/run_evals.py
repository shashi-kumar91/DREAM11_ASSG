"""Measured properties of both packs, from the recorded model turns, with no network.

This is not a test file: a test asks "does this still work" and this asks "how good is it, and
what did it cost". Every metric here is computed from a real run and a real trace, and the four
hard invariants (no silence, no spoiler, no unlicensed digit, no fabricated badge) exit non-zero
rather than printing a warning - a catch-up product that violates one of them is not a demo
waiting for polish, it is the failure the product exists to prevent.

    python -u evals/run_evals.py                 # both packs, replay mode
    python -u evals/run_evals.py --pack gap --llm off
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend.kernel.llm import UnavailableLLM  # noqa: E402
from backend.kernel.schemas import Budget  # noqa: E402
from backend.kernel.trace import TraceRecorder  # noqa: E402
from backend.packs.creator.domain import Feed  # noqa: E402
from backend.packs.creator.session import CreatorSession, SessionConfig  # noqa: E402
from backend.packs.gap.domain import GapFixtures  # noqa: E402
from backend.packs.gap.session import GapConfig, GapSession  # noqa: E402

FIX = ROOT / "backend" / "fixtures"
OUT = ROOT / "evals"

# The fan's answer for each Pack B scenario, taken from the options the question card actually
# offers: an eval that invents its own wording is measuring a path the product never takes.
GAP_ANSWERS = {
    "S1": {"did you see": "No, I left at 16.2, recap everything"},
    "S2": {"recap": "Yes, recap it"},
    "S3": {"recap": "Yes, recap everything - just the key moments for Chennai Kestrels"},
}

NUM_RE = re.compile(r"\d+(?:[./:]\d+)*")


def _nums(text: str) -> set[str]:
    """A number licenses the whole token and each of its parts.

    "03:24" is one timestamp, so it may also be written as "03" or "24". `commit.py` splits the
    same way; doing it here too keeps the check honest about what the gate actually enforces
    without importing the gate itself.
    """
    out: set[str] = set()
    for raw in NUM_RE.findall(text or ""):
        out.add(raw)
        out |= {p for p in re.split(r"[./:]", raw) if p}
    return out


def _budget(n: int) -> Budget:
    return Budget(max_llm_calls=n, max_tool_calls=12, max_ms=240_000, max_specialist_rounds=8)


# Each pack is measured at the budget the product actually runs it at, because the prompt renders
# BUDGET REMAINING: change the budget and you change the question, so a replayed turn no longer
# matches a recorded one. These must track the server defaults in backend/app/server.py.
PACK_BUDGET = {"gap": 14, "creator": 12}


def _make_llm(backend: str, mode: str):
    if mode == "off":
        return UnavailableLLM()
    from backend.kernel.openrouter import OpenRouterLLM
    live = mode in ("live", "record")
    return OpenRouterLLM(live=live, cassette_mode="record" if live else "replay")


# --------------------------------------------------------------------------- #
# the licensing check, written a second time on purpose
# --------------------------------------------------------------------------- #

def evidence_blob(card, feed, sess, rows_by_id) -> str:
    """Everything a card is allowed to print, assembled from the ids it cites.

    `commit.py` enforces this rule at commit time. Re-deriving it here from the card's citation
    lists is the point: if the two implementations ever disagree, the eval says so instead of
    trusting the gate that is supposed to be watched.

    Pack A quotes the creator's own words, so an utterance is primary evidence there; a check
    that read only the ball feed would flag every number spoken into a microphone as invented.
    """
    parts: list[str] = []
    for uid in getattr(card, "cited_utterance_ids", []):
        row = rows_by_id.get(uid)
        if row:
            parts.append(f"{_fmt(row['t_start'])} {row['t_start']:.1f} {row['text']}")
    for eid in card.cited_event_ids:
        e = feed.by_id.get(eid)
        if e is None:
            continue
        parts.append(f"{e.over_label} {e.score} {e.innos_runs} {e.runs} {e.rrr} "
                     f"{e.balls_faced} {e.wickets} {e.text}")
    for vid in getattr(card, "cited_verdict_ids", []):
        v = getattr(sess, "verdicts", {}).get(vid)
        if v is not None:
            parts.append(f"claimed {v.claimed} observed {v.observed} {v.matched_over or ''} "
                         f"{v.age_balls} {v.detail}")
    for tid in card.cited_tally_ids:
        t = _tally(sess, tid)
        if t:
            parts.append(t)
    notes = getattr(sess, "evidence_notes", {})
    parts.extend(str(v) for v in notes.values())
    # A prediction card quotes the ledger's own scope ("over 17", "2+ yorkers"). Those digits are
    # extracted and resolved by code before the model ever sees the turn, so the prediction
    # record is evidence - found here by the utterance it was logged from, not by trusting the
    # card's citation lists, which do not carry prediction ids.
    ledger = getattr(sess, "ledger", None)
    held = list(getattr(ledger, "open", [])) + list(getattr(ledger, "closed", []))
    for uid in getattr(card, "cited_utterance_ids", []):
        for p in held:
            if p.utterance_id == uid:
                parts.append(f"prediction {p.text} over {p.over or ''} {p.metric or ''} "
                             f"{'' if p.threshold is None else p.threshold} logged "
                             f"{_fmt(p.t_logged)} {p.t_logged:.1f}")
    return " | ".join(parts)


def _tally(sess, tid: str) -> str:
    ledger = getattr(sess, "ledger", None)
    src = getattr(ledger, "tallies", None) if ledger is not None else None
    return (src or getattr(sess, "tallies", {}) or {}).get(tid, "")


def _fmt(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def unlicensed_numbers(card, blob: str) -> list[str]:
    printed = _nums(f"{card.headline} {card.body}")
    licensed = _nums(blob)
    # The strict rule is what commit enforces, so a printed token must appear as itself or as a
    # part of a licensed token - not merely share digits with one.
    return sorted(t for t in printed if t not in licensed)


def unlicensed_detail(pairs: list[tuple]) -> str:
    """Name the offending card and number, because '1 violation' is not actionable."""
    return "; ".join(f"[{c.kind} {c.timestamp_label}] printed {n}, not in cited evidence"
                      for c, blob in pairs for n in unlicensed_numbers(c, blob))


def spoilers(card, feed, cursor: str) -> list[str]:
    hi = feed.index[cursor]
    return [eid for eid in card.cited_event_ids if feed.index.get(eid, 0) > hi]


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #

def run_gap(backend: str, mode: str, budget: int) -> list[dict]:
    fx = GapFixtures.load()
    rows: list[dict] = []
    for sid, sc in fx.scenarios.items():
        trace = TraceRecorder(OUT / f"gap_{sid}.jsonl")
        trace.events.clear()
        llm = _make_llm(backend, mode)
        sess = GapSession(llm, fx, trace, lambda e: None,
                          cfg=GapConfig(budget=_budget(budget), ask_timeout_s=2.0),
                          auto_answer=GAP_ANSWERS.get(sid, {}))
        t0 = time.time()
        ep = sess.run_scenario(sid)
        kinds = [e["kind"] for e in trace.events]
        cards = sess.cards
        # Per card, not one pooled blob: a digit on the boundary tally must not license a number
        # printed by a different card.
        pairs = [(c, evidence_blob(c, fx.feed, sess, {})) for c in cards]
        rows.append({
            "pack": "gap", "scenario": sid, "outcome": "card" if cards else "SILENCE",
            "cards": len(cards), "kind": cards[0].kind if cards else "",
            "truth_label": cards[0].truth_label.value if cards else "",
            "template": cards[0].template_used if cards else "",
            "body_chars": len(cards[0].body) if cards else 0,
            "terminal": ep.terminal.value, "reason": ep.terminal_reason[:60],
            "asked": kinds.count("ask_user"), "answered": kinds.count("boundary_from_fan"),
            "no_question": kinds.count("no_question"),
            "ask_refused": kinds.count("ask_refused"),
            "tool_calls": kinds.count("tool_call"),
            "model_turns": kinds.count("supervisor_decision"),
            "llm_calls": llm.calls if hasattr(llm, "calls") else 0,
            "budget_llm": budget,
            "guardrail_rejects": kinds.count("guardrail"),
            "clip_rejects": kinds.count("clip_rejected"),
            "commits_ok": kinds.count("commit") - kinds.count("commit_rejected"),
            "terminal_vetoed": kinds.count("terminal_vetoed"),
            "modality_overridden": kinds.count("modality_overridden"),
            "fallback_used": any("deterministic" in str(e.get("note", ""))
                                 for e in trace.events if e["kind"] == "status"),
            "llm_badge": sess.badges().get("llm", ""),
            "seconds": round(time.time() - t0, 2),
            "spoilers": sum(len(spoilers(c, fx.feed, sc.resume_cursor)) for c in cards),
            "unlicensed": sum(len(unlicensed_numbers(c, blob)) for c, blob in pairs),
            "unlicensed_detail": unlicensed_detail(pairs),
            "answer_changed_artifact": False,
        })
    # A/B the question: same scenario, two fan answers, and the receipts must actually differ.
    variants = {}
    for answer in ("No, I left at 16.2, recap everything",
                   "No, I left at 16.2, only wickets and boundaries please"):
        trace = TraceRecorder(OUT / "gap_AB.jsonl")
        trace.events.clear()
        sess = GapSession(UnavailableLLM(), fx, trace, lambda e: None,
                          cfg=GapConfig(budget=_budget(6), ask_timeout_s=2.0),
                          auto_answer={"did you see": answer})
        sess.run_scenario("S1")
        variants[answer] = sess.cards[0].body if sess.cards else ""
    rows[0]["answer_changed_artifact"] = (variants[list(variants)[0]]
                                          != variants[list(variants)[1]])
    return rows


def run_creator(backend: str, mode: str, budget: int) -> list[dict]:
    from backend.adapters.asr import CachedASR
    feed = Feed.load()
    rows_in = json.loads((FIX / "transcript_expected.json").read_text(encoding="utf-8"))
    for r in rows_in:
        r["audio_bytes"] = (FIX / r["wav"]).read_bytes()
    trace = TraceRecorder(OUT / "creator_all.jsonl")
    trace.events.clear()
    llm = _make_llm(backend, mode)
    sess = CreatorSession(llm, feed, CachedASR(), rows_in, trace, lambda e: None,
                          cfg=SessionConfig(budget=_budget(budget)))
    t0 = time.time()
    for r in sorted(rows_in, key=lambda x: x["t_start"]):
        sess.advance(r["t_end"])
        sess.process(r)
    # The innings ends; every prediction the ledger is holding is settled against real balls.
    sess.advance(240.0)
    kinds = [e["kind"] for e in trace.events]
    cards = sess.cards
    labels: dict[str, int] = {}
    spol = 0
    checked = 0
    rows_by_id = {r["id"]: r for r in rows_in}
    pairs = [(c, evidence_blob(c, feed, sess, rows_by_id)) for c in cards]
    for c in cards:
        labels[c.truth_label.value] = labels.get(c.truth_label.value, 0) + 1
        # Pack A's cursor is the moment the creator spoke: a card may not cite a ball the
        # transcript has not reached yet. Only count it when the card's own label resolves.
        anchor = feed.find_by_over(c.timestamp_label) if c.timestamp_label else None
        if anchor is not None and c.cited_event_ids:
            checked += 1
            hi = feed.index[anchor.id]
            spol += sum(1 for eid in c.cited_event_ids
                        if eid in feed.index and feed.index[eid] > hi)
    return [{
        "pack": "creator", "scenario": f"{len(rows_in)} segments",
        "outcome": "card" if cards else "SILENCE", "cards": len(cards),
        "kind": ",".join(sorted({c.kind for c in cards})),
        "truth_label": json.dumps(labels), "template": "",
        "body_chars": max((len(c.body) for c in cards), default=0),
        "terminal": "", "reason": "",
        "asked": kinds.count("ask_user"), "answered": 0,
        "no_question": 0, "ask_refused": kinds.count("ask_refused"),
        "tool_calls": kinds.count("tool_call"),
        "model_turns": kinds.count("supervisor_decision"),
        "llm_calls": llm.calls if hasattr(llm, "calls") else 0, "budget_llm": budget,
        "guardrail_rejects": kinds.count("guardrail"),
        "clip_rejects": 0,
        "commits_ok": kinds.count("commit"),
        "terminal_vetoed": kinds.count("terminal_vetoed"),
        "modality_overridden": kinds.count("modality_overridden"),
        "fallback_used": any("deterministic" in str(e.get("note", ""))
                             for e in trace.events if e["kind"] == "status"),
        "llm_badge": sess.badges().get("llm", ""),
        "seconds": round(time.time() - t0, 2),
        "spoilers": spol, "unlicensed": sum(len(unlicensed_numbers(c, b)) for c, b in pairs),
        "unlicensed_detail": unlicensed_detail(pairs),
        "cursor_checked_cards": checked,
        "tier0_skips": kinds.count("tier0_skip"),
        "predictions": kinds.count("prediction"),
        "settled": kinds.count("settlement"),
        "answer_changed_artifact": False,
    }]


COLS = ("pack", "scenario", "outcome", "cards", "model_turns", "tool_calls", "llm_calls",
        "asked", "answered", "answer_changed_artifact", "guardrail_rejects", "spoilers",
        "unlicensed", "fallback_used", "llm_badge", "seconds")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="both", choices=["both", "gap", "creator"])
    ap.add_argument("--llm", default="replay", choices=["replay", "off", "record"])
    ap.add_argument("--backend", default=os.getenv("LLM_BACKEND", "openrouter"))
    ap.add_argument("--budget", type=int, default=0,
                    help="override every pack's budget; default is the per-pack product budget")
    a = ap.parse_args()

    def budget_for(pack: str) -> int:
        return a.budget or PACK_BUDGET[pack]

    results: list[dict] = []
    if a.pack in ("both", "gap"):
        results += run_gap(a.backend, a.llm, budget_for("gap"))
    if a.pack in ("both", "creator"):
        results += run_creator(a.backend, a.llm, budget_for("creator"))

    w = max(len(c) for c in COLS) + 2
    print("  ".join(c.ljust(w - 2)[:w - 2] for c in COLS))
    for r in results:
        print("  ".join(str(r.get(c, ""))[:w - 2].ljust(w - 2) for c in COLS))

    hard = []
    if any(r["outcome"] == "SILENCE" for r in results if r["pack"] == "gap"):
        hard.append("a gap scenario produced no receipt")
    if sum(r["spoilers"] for r in results):
        hard.append(f"{sum(r['spoilers'] for r in results)} cited event(s) past a resume cursor")
    if sum(r["unlicensed"] for r in results):
        bad = "; ".join(f"{r['pack']}/{r['scenario']}: {r['unlicensed_detail']}"
                        for r in results if r["unlicensed"])
        hard.append(f"{sum(r['unlicensed'] for r in results)} unlicensed number(s) printed -> {bad}")
    if any(r["asked"] > 1 for r in results):
        hard.append("a fan was asked more than one question in one scenario")
    if a.llm == "replay":
        dead = [f"{r['pack']}/{r['scenario']}" for r in results if r["llm_badge"] != "cassette"]
        if dead:
            # A run that fell through to the deterministic path is not measuring the agent, and a
            # green table would be a lie about which code path produced the artifact.
            hard.append(f"replay found no recorded turns for: {', '.join(dead)}")

    totals = {
        "runs": len(results),
        "cards": sum(r["cards"] for r in results),
        "model_turns": sum(r["model_turns"] for r in results),
        "tool_calls": sum(r["tool_calls"] for r in results),
        "silence": sum(1 for r in results if r["outcome"] == "SILENCE"),
        "spoilers": sum(r["spoilers"] for r in results),
        "unlicensed": sum(r["unlicensed"] for r in results),
        "fallbacks": sum(1 for r in results if r["fallback_used"]),
        "llm_off": a.llm == "off",
    }
    (OUT / "results.json").write_text(json.dumps({"totals": totals, "runs": results},
                                                 indent=2), encoding="utf-8")
    print(f"\ntotals: {json.dumps(totals)}")
    print(f"written: {OUT / 'results.json'}")
    for h in hard:
        print(f"FAIL: {h}")
    print("PASS - all four hard invariants hold" if not hard else f"{len(hard)} violation(s)")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
