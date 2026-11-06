"""Pack B tools - six, hard-capped, and each one answers a different kind of question.

| tool | answers | deterministic? |
|---|---|---|
| get_playback_telemetry | where did the fan lose the stream | yes (simulated log + code boundary estimate) |
| query_event_feed | what happened in the window | yes, clamped |
| inspect_frame | does the scoreboard agree with the feed | pixel read + code compare |
| search_commentary | what did the commentary say | retrieval |
| ask_fan | what the fan actually saw | human |
| offer_clip | what a clip would cost before loading it | code quotation, human accepts |

There is no tool that writes a receipt, chooses a truth label, or returns an event past the
fan's resume cursor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ...kernel.registry import Tool, ToolRegistry
from ...kernel.schemas import PendingQuestion
from . import ocr, voi
from .domain import GapScenario, material_events

CLIP_SECONDS = 12
BYTES_PER_SECOND_KB = 200          # ~1.6 Mbit/s, simulated player profile


def as_str(v: Any) -> Any:
    if isinstance(v, (int, float)):
        return str(v)
    return v


class EmptyArgs(BaseModel):
    """`get_playback_telemetry` takes nothing, so models pass nothing in every shape."""


class FeedRangeArgs(BaseModel):
    start_over: str = ""
    end_over: str = ""
    only_material: bool = False

    _coerce = field_validator("start_over", "end_over", mode="before")(as_str)


class FrameArgs(BaseModel):
    """Agents point at a ball by over label, by event id, or by raw clock seconds."""

    over_label: str = ""
    event_id: str = ""
    t: float = 0.0

    _coerce = field_validator("over_label", "event_id", mode="before")(as_str)


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    limit: int = 5

    _coerce = field_validator("query", mode="before")(as_str)


class AskArgs(BaseModel):
    question: str = Field(min_length=3, max_length=200)
    options_json: str = "[]"

    _coerce = field_validator("question", mode="before")(as_str)


class ClipArgs(BaseModel):
    event_id: str = Field(min_length=1, max_length=40)

    _coerce = field_validator("event_id", mode="before")(as_str)


class GapToolbox:
    def __init__(self, session):
        self.s = session

    # -- 1 ------------------------------------------------------------------ #

    def get_playback_telemetry(self, _args: EmptyArgs) -> dict:
        sc: GapScenario = self.s.scenario
        b = self.s.boundary
        receipts = voi.receipts_for(self.s.feed, b.candidates, sc.resume_cursor, sc.resume_cursor)
        ask, why = voi.should_ask(self.s.feed, b, sc.resume_cursor, sc.resume_cursor)
        return {
            "interruption_type": sc.interruption_type,
            "heartbeats": [r.model_dump() for r in sc.heartbeats],
            "resume_cursor": sc.resume_cursor,
            "resume_over": self.s.feed.by_id[sc.resume_cursor].over_label,
            "boundary": {"candidates": b.candidates,
                         "candidate_overs": b.labels(self.s.feed),
                         "confidence": b.confidence, "notes": b.notes,
                         "clean_last_position": b.clean_last_position},
            "material_events_by_candidate": {c: [{"id": e.id, "over": e.over_label,
                                                  "what": e.text, "score": e.score}
                                                 for e in evs]
                                             for c, evs in receipts.items()},
            "value_of_information": {"ask": ask, "why": why},
        }

    # -- 2 ------------------------------------------------------------------ #

    def query_event_feed(self, args: FeedRangeArgs) -> dict:
        clamp_to = self.s.scenario.resume_cursor
        start = self.s.resolve_over(args.start_over)
        end = self.s.resolve_over(args.end_over) or clamp_to
        rows = self.s.feed.clamped(clamp_to).between(start, end)
        if args.only_material:
            rows = material_events(rows)
        self.s.note_feed_read(rows)
        return {"count": len(rows),
                "clamped_at": self.s.feed.by_id[clamp_to].over_label,
                "material_count": len(material_events(rows)),
                "events": [{"id": e.id, "over_label": e.over_label, "kind": e.kind,
                            "runs": e.runs, "wicket": e.wicket, "score": e.score,
                            "rrr": e.rrr, "ball_type": e.ball_type, "text": e.text,
                            "striker": e.striker, "bowler": e.bowler, "out": e.out}
                           for e in rows]}

    # -- 3 ------------------------------------------------------------------ #

    def inspect_frame(self, args: FrameArgs) -> dict:
        ev = None
        if args.event_id:
            ev = self.s.feed.by_id.get(args.event_id)
        elif args.over_label:
            ev = self.s.feed.find_by_over(self.s.normalise_over(args.over_label))
        t = args.t if not ev else ev.t_wall
        if ev is not None and self.s.feed.clamped(self.s.scenario.resume_cursor).get(ev.id) is None:
            return {"error": f"frame_for_event_beyond_resume_cursor: {ev.over_label}"}
        frame = self.s.fixtures.frame_at(t)
        if frame is None:
            return {"found": False, "t": round(t, 1),
                    "why": "no scoreboard frame captured at or before this moment"}
        read = ocr.read_frame(frame.image_bytes())
        feed_score = ev.score if ev is not None else None
        agrees = bool(feed_score) and read.get("score") == feed_score
        result = {"found": True, "frame_id": frame.frame_id, "path": frame.path,
                  "read_from_pixels": read, "over_label": frame.over_label,
                  "feed_score": feed_score, "agrees": agrees,
                  "reader": "pixel_template_match", "injected_fault": frame.injected_fault}
        self.s.note_frame_check(result)
        return result

    # -- 4 ------------------------------------------------------------------ #

    def search_commentary(self, args: SearchArgs) -> dict:
        from ..creator.triage import content_tokens

        qtok = content_tokens(args.query)
        rows = []
        for e in self.s.feed.clamped(self.s.scenario.resume_cursor).upto_cursor():
            overlap = len(qtok & (content_tokens(e.text) | content_tokens(" ".join(e.tags))))
            if overlap:
                rows.append((overlap, e))
        rows.sort(key=lambda x: (-x[0], -x[1].balls_faced))
        return {"count": len(rows[:args.limit]),
                "lines": [{"id": e.id, "over_label": e.over_label, "text": e.text,
                           "score": e.score} for _, e in rows[:args.limit]]}

    # -- 5 ------------------------------------------------------------------ #

    def ask_fan(self, args: AskArgs) -> dict:
        import json

        settled = self.s.fan_answer()
        if settled:
            # Asking twice is the failure mode the value-of-information test exists to prevent.
            self.s.trace.record("ask_fan_refused", reason="the kernel asked this before the "
                                                          "first model turn",
                                answer=settled.get("answer", "")[:80])
            return {"asked": True, "answered": True, "question": settled.get("question", ""),
                    "answer": settled.get("answer", ""),
                    "note": "already put to the fan by the kernel; the boundary is settled"}

        try:
            options = json.loads(args.options_json or "[]")
        except (TypeError, ValueError):
            options = []
        text = args.question
        canonical_q, canonical_opts = self.s.canonical_question()
        if canonical_q:
            # The value of the question is that its options are the candidate boundaries.
            self.s.trace.record("question_wording_from_kernel", model_text=args.question[:120],
                                used_text=canonical_q[:160])
            text, options = canonical_q, canonical_opts
        pq = PendingQuestion(text=text, reason="boundary or preference unresolved",
                             options=[str(o) for o in (options or [])][:4], blocking=True,
                             expected_info_gain=0.6)
        answer = self.s.ask(pq)
        if answer is None:
            return {"asked": True, "answered": False,
                      "why": "the fan did not answer; do not assume an answer",
                      "question": pq.text}
        return {"asked": True, "answered": True, "question": pq.text, "answer": answer}

    # -- 6 ------------------------------------------------------------------ #

    def offer_clip(self, args: ClipArgs) -> dict:
        from .guardrails import clip_is_offerable

        ev = self.s.feed.by_id.get(args.event_id)
        if ev is None:
            return {"error": "unknown_event", "event_id": args.event_id}
        if self.s.feed.clamped(self.s.scenario.resume_cursor).get(ev.id) is None:
            return {"error": "beyond_resume_cursor", "over": ev.over_label}
        check = self.s.frame_check_for(ev)
        ok, why = clip_is_offerable(check)
        quote = {"event_id": ev.id, "over_label": ev.over_label, "duration_s": CLIP_SECONDS,
                 "bytes_estimate_kb": CLIP_SECONDS * BYTES_PER_SECOND_KB,
                 "load_policy": "not fetched until the fan taps it", "frame_check": why}
        self.s.note_clip_quote({"offerable": ok, "quote": quote})
        if not ok:
            self.s.trace.record("clip_rejected", event_id=ev.id, why=why)
            return {"offerable": False, "why": why, "quote": quote}
        return {"offerable": True, "quote": quote}

    # -- registry ----------------------------------------------------------- #

    def build_registry(self) -> ToolRegistry:
        r = ToolRegistry()
        r.tool("get_playback_telemetry",
               "the fan's own player log plus the code's boundary estimate and the "
               "value-of-information test", self.get_playback_telemetry,
               capabilities=("telemetry",), args_model=EmptyArgs)
        r.tool("query_event_feed",
               "events between two over labels, hard-clamped to the fan's resume cursor",
               self.query_event_feed, capabilities=("past_events",), args_model=FeedRangeArgs)
        r.tool("inspect_frame",
               "read the scoreboard out of a frame's pixels and compare it with the feed",
               self.inspect_frame, capabilities=("video_evidence",), args_model=FrameArgs)
        r.tool("search_commentary", "text commentary lines inside the clamped window",
               self.search_commentary, capabilities=("past_events",), args_model=SearchArgs)
        r.tool("ask_fan", "ask the fan one specific question and wait",
               self.ask_fan, capabilities=("ask_user",), args_model=AskArgs, deterministic=False,
               read_only=False)
        r.tool("offer_clip", "price a 12s clip and check its frame before offering it",
               self.offer_clip, capabilities=("permission",), args_model=ClipArgs,
               deterministic=False, read_only=False)
        return r
