"""Pack A tools. Every tool is capability-tagged and returns structured data or a
structured error - never an exception into the agent loop.

Note what is *not* here: no tool computes a score, no tool returns a "verified" flag, and
no tool can hand back an event the fan has not reached (they go through ClampedFeed).
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ...kernel.registry import Tool, ToolRegistry
from ...kernel.schemas import InsightType, Utterance, Verdict, new_id
from .predictions import PredictionExtraction
from .verify import StructuredClaim, verify


def as_str(v: Any) -> Any:
    """Agents emit 16 where the schema says "16.0"; a lost tool call costs a whole turn."""
    if isinstance(v, (int, float)):
        return str(v)
    return v


def as_json_str(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if v is None:
        return "{}"
    return v


class ResolveRefArgs(BaseModel):
    phrase: str = Field(min_length=1, max_length=60)
    kind_hint: str = ""

    _coerce = field_validator("phrase", "kind_hint", mode="before")(as_str)


class FeedRangeArgs(BaseModel):
    """Agents say overs as "16.3", 16, 16.3 or None interchangeably. Coerce rather than
    lose a tool call, but never silently widen the clamp."""

    start_over: str = ""
    end_over: str = ""
    only_material: bool = False

    _coerce = field_validator("start_over", "end_over", mode="before")(as_str)


class VerifyArgs(BaseModel):
    claim_json: str = "{}"

    _coerce = field_validator("claim_json", mode="before")(as_json_str)


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = 4

    _coerce = field_validator("query", mode="before")(as_str)


class LogPredArgs(BaseModel):
    prediction_json: str = "{}"

    _coerce = field_validator("prediction_json", mode="before")(as_json_str)


class RecallArgs(BaseModel):
    utterance_id: str = Field(min_length=1, max_length=40)

    _coerce = field_validator("utterance_id", mode="before")(as_str)


class CreatorToolbox:
    def __init__(self, session):
        self.s = session

    # -- individual tools -------------------------------------------------- #

    def get_match_state(self, _args: dict) -> dict:
        st = self.s.feed.state_at(self.s.t)
        if st is None:
            return {"available": False, "why": "no ball completed yet at this clock time"}
        nxt = self.s.feed.next_over_bowler(self.s.t)
        return {"available": True, "t": round(self.s.t, 1), **st.model_dump(),
                "next_over_bowler": nxt, "target": self.s.feed.meta.target,
                "batting_team": self.s.feed.meta.team_b, "bowling_team": self.s.feed.meta.team_a,
                "squads": self.s.feed.meta.squads}

    def resolve_reference(self, args: ResolveRefArgs) -> dict:
        st = self.s.feed.state_at(self.s.t)
        if st is None:
            return {"resolved": None, "ambiguity": "no match state available"}
        hint = (args.kind_hint or "").lower()
        cands: list[tuple[str, str]] = [("striker", st.striker), ("bowler", st.bowler),
                                        ("non_striker", st.non_striker)]
        named = [(k, v) for k, v in cands if _token_in_phrase(args.phrase, v)]
        if named:
            return {"resolved": named[0][1], "role": named[0][0], "confidence": 1.0,
                    "method": "explicit_name_in_phrase"}
        if hint in {"striker", "bowler", "non_striker"}:
            val = dict(cands).get(hint)
            return {"resolved": val, "role": hint, "confidence": 0.9,
                    "method": f"role_hint:{hint}"}
        if hint in {"batter", "batsman", "batsmen", "striker_end"}:
            return {"resolved": st.striker, "role": "striker", "confidence": 0.85,
                    "method": "role_hint:batter->striker"}
        if hint in {"bowler_end", "delivery"}:
            return {"resolved": st.bowler, "role": "bowler", "confidence": 0.85,
                    "method": "role_hint:bowler"}
        return {"resolved": None, "ambiguity": "no role hint",
                "candidates": [{"role": k, "name": v} for k, v in cands],
                "confidence": 0.0,
                "note": "pronouns like 'yeh banda' usually mean the striker; the supervisor "
                        "should dispatch perceiver or ask rather than guess"}

    def query_event_feed(self, args: FeedRangeArgs) -> dict:
        cursor = (self.s.feed.state_at(self.s.t) or None)
        clamped = self.s.clamped()
        if args.start_over or args.end_over:
            lo = clamped.first_at_or_after(args.start_over) if args.start_over else None
            hi = clamped.first_at_or_after(args.end_over) if args.end_over else None
            rows = clamped.between(lo.id if lo else None, hi.id if hi else None)
        else:
            rows = clamped.latest(6)
        if args.only_material:
            rows = [e for e in rows if e.material]
        self.s.note_feed_read(rows)
        return {"count": len(rows),
                "clamped_at": cursor.over_label if cursor else "",
                "events": [{"id": e.id, "over_label": e.over_label, "kind": e.kind,
                            "runs": e.runs, "wicket": e.wicket, "score": e.score,
                            "rrr": e.rrr, "ball_type": e.ball_type, "text": e.text,
                            "striker": e.striker, "bowler": e.bowler, "out": e.out}
                           for e in rows]}

    def verify_claim(self, args: VerifyArgs) -> dict:
        u = self.s.current_utterance
        if u is None:
            return {"error": "no utterance in this episode"}
        try:
            payload = json.loads(args.claim_json or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"bad_claim:not valid JSON ({str(exc)[:120]})"}
        if not isinstance(payload, dict):
            return {"error": "bad_claim:expected a JSON object"}
        # StructuredClaim itself recovers the shapes agents reach for, so the kernel path and
        # this tool path verify the same claim. What is left here is only the signal: a payload
        # with nothing recognisable in it must say so, not return a plausible-looking verdict.
        try:
            claim = StructuredClaim.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            return {"error": f"bad_claim:{first.get('loc')}: {str(first.get('msg'))[:120]}"}
        if claim.kind == "other":
            return {"error": "bad_claim:nothing checkable in this claim",
                    "expected": {"kind": "score|wicket_by_player|boundary_in_over|"
                                          "yorker_in_over|required_rate|team_total",
                                 "raw_phrase": "the creator's own words",
                                 "runs": "int", "wickets": "int", "player": "name",
                                 "over": "int", "count": "int", "rate": "float"},
                    "note": "only numbers the creator actually said may be included"}
        st = self.s.feed.state_at(self.s.t)
        v = verify(claim, u.text, self.s.feed, st)
        self.s.store_verdict(v)
        return {"verdict_id": v.id, "kind": v.kind.value, "claimed": v.claimed,
                "observed": v.observed, "matched_over": v.matched_over,
                "matched_event_id": v.matched_event_id, "age_balls": v.age_balls,
                "detail": v.detail,
                "authoritative": True,
                "note": "this verdict is computed by code; agents may not override it"}

    def search_transcript(self, args: SearchArgs) -> dict:
        hits = self.s.transcript_search(args.query, args.limit)
        return {"query": args.query, "count": len(hits), "segments": hits,
                "note": "excerpts are verbatim substrings of the stored transcript; "
                        "no paraphrase was generated"}

    def log_prediction(self, args: LogPredArgs) -> dict:
        u = self.s.current_utterance
        if u is None:
            return {"error": "no utterance in this episode"}
        try:
            ex = PredictionExtraction.model_validate(json.loads(args.prediction_json or "{}"))
        except (ValidationError, json.JSONDecodeError) as exc:
            return {"error": f"bad_prediction:{str(exc)[:160]}",
                    "hint": "fields: text, over, metric (boundary_count|wicket|runs|dot_balls), "
                            "threshold (an integer >= 1), bowler, batter"}
        # Two fields the model often leaves empty are not the model's to fill from memory:
        # the bowler of the next over is feed data, and the words the fan will read are the
        # creator's own. Filling them here is copying evidence, not inventing it - and it is
        # the difference between a tracked prediction and a silently dropped one.
        from .predictions import complete_from_text

        ex, filled = complete_from_text(ex, u.text)
        next_bowler = self.s.feed.next_over_bowler(self.s.t)
        if not (ex.bowler or "").strip() and next_bowler:
            ex = ex.model_copy(update={"bowler": next_bowler})
        if not (ex.text or "").strip():
            ex = ex.model_copy(update={"text": u.text[:300]})
        if filled:
            self.s.trace.record("prediction_completed", utterance_id=u.id, filled=filled,
                                note="fields read out of the creator's own sentence")
        p, why = self.s.ledger.log(ex, u.id, self.s.t, self.s.feed, next_bowler)
        if p is None:
            return {"logged": False, "why": why, "next_over_bowler": next_bowler,
                    "fan_clock": self.s.feed.state_at(self.s.t).over_label
                    if self.s.feed.state_at(self.s.t) else "",
                    "hint": "the creator's words are cited evidence: supply over, metric and an "
                            "integer threshold; the bowler defaults to the feed's next over"}
        self.s.record_prediction(p)
        return {"logged": True, "prediction_id": p.id, "bowler": p.bowler, "over": p.over,
                "metric": p.metric, "threshold": p.threshold, "why": why}

    def get_predictions(self, _args: dict) -> dict:
        snap = self.s.ledger.snapshot()
        return {"open": snap["open"], "settled": snap["closed"][-5:]}

    def recall_utterance(self, args: RecallArgs) -> dict:
        uid = args.utterance_id
        for u in self.s.history:
            if u.id == uid:
                return {"id": u.id, "text": u.text, "t_start": u.t_start,
                        "asr_confidence": u.asr_confidence}
        return {"error": f"unknown_utterance:{uid}",
                "known_ids": [u.id for u in self.s.history]}

    # -- registration ------------------------------------------------------ #

    def build_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        add = reg.register
        add(Tool("get_match_state", "current score, over, striker, bowler, RRR and squads as of "
                                   "the fan's clock; deterministic", self.get_match_state,
                 ("match_state",), None))
        add(Tool("resolve_reference", "map a mention or role hint to a concrete player from the "
                                      "match state; returns candidates when ambiguous",
                 self.resolve_reference, ("match_state",), ResolveRefArgs))
        add(Tool("query_event_feed", "ball-by-ball events up to the fan's playback position; "
                                     "events beyond it are unqueryable",
                 self.query_event_feed, ("past_events",), FeedRangeArgs))
        add(Tool("verify_claim", "run the deterministic verifier on a structured claim; "
                                 "the only source of CONFIRMED/CONFLICT",
                 self.verify_claim, ("verify",), VerifyArgs))
        add(Tool("search_transcript", "retrieve verbatim attributed segments matching a query",
                 self.search_transcript, ("speech_memory",), SearchArgs))
        add(Tool("log_prediction", "validate and store a creator prediction in the ledger",
                 self.log_prediction, ("predictions",), LogPredArgs))
        add(Tool("get_predictions", "open and settled predictions", self.get_predictions,
                 ("predictions",), None))
        add(Tool("recall_utterance", "fetch a stored utterance by id", self.recall_utterance,
                 ("speech_memory",), RecallArgs))
        return reg


def _token_in_phrase(phrase: str, name: str) -> bool:
    if not name:
        return False
    low = (phrase or "").lower()
    return any(part.lower() in low for part in name.split())


def insight_type_ok(raw: str) -> bool:
    return raw in {e.value for e in InsightType} or raw in {e.name for e in InsightType}
