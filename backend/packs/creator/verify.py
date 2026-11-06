"""Deterministic claim verification.

The verifier is pure code: it compares an extracted, *grounded* claim against the feed the
fan has already seen. The LLM never receives a "verified" badge - the kernel derives the
card label from the verdict returned here.

Grounding rule: every number the analyst extracts must literally appear in the utterance
text. If it does not, the extraction is untrusted and the claim becomes UNVERIFIABLE
rather than being checked against a number the model invented.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from ...kernel.schemas import MatchEvent, ScoreState, Verdict, VerdictKind
from .domain import Feed

ClaimKind = Literal["score", "wicket_by_player", "boundary_in_over", "yorker_in_over",
                    "required_rate", "team_total", "other"]
_CLAIM_KINDS = set(get_args(ClaimKind))

NUMBER_RE = re.compile(r"\d+(?:[./]\d+)?")
# A score can be said four ways on a real Hinglish stream, and the first version only knew the
# first one: "142 for 2" (a live line I tested with) slipped past the verifier entirely and the
# supervisor was free to call it an opinion and skip it. The shapes below are all "runs then
# wickets", never "wickets for runs", and each keeps a digit boundary so "1420 for 2" cannot
# masquerade as 142.
SCORE_RES = (
    re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,2})\b"),                      # 142/2
    re.compile(r"\b(\d{1,3})\s*[-:]\s*(\d{1,2})\b"),                  # 142-2, 142: 2
    re.compile(r"\b(\d{1,3})\s+(?:for|pe|par)\s+(\d{1,2})\b"),        # 142 for 2, 142 pe 2
    re.compile(r"\b(\d{1,3})\s*runs?\b[^.]{0,18}?\b(\d{1,2})\s*wickets?\b"),  # 142 runs for 2 wickets
)
SCORE_RE = SCORE_RES[0]        # kept for callers that predate the wider set


def mk_verdict(**fields: Any) -> Verdict:
    """Build a verdict whose id is derived from its own content, not from uuid4.

    The verdict dict is dumped into the supervisor and critic prompt text (scratchpad), so a
    random id makes the prompt - and therefore the cassette key - different on every run:
    replay misses and the whole episode silently degrades to the deterministic fallback.
    A content hash gives the same verdict the same id in every process, which is what makes
    the recorded path byte-reproducible.
    """
    v = Verdict(**fields)
    body = v.model_dump_json(exclude={"id"})
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    return v.model_copy(update={"id": f"ver_{digest}"})


def _usable_fields(out: dict) -> bool:
    """True when the payload carries something a verifier could actually check."""
    return any(out.get(k) not in (None, "", []) for k in
               ("runs", "wickets", "player", "rate", "count", "score"))


class StructuredClaim(BaseModel):
    """Analyst's extraction of what the creator asserted."""

    kind: ClaimKind = "other"
    raw_phrase: str = Field(default="", max_length=200)
    runs: Optional[int] = None
    wickets: Optional[int] = None
    player: Optional[str] = None
    over: Optional[int] = None
    count: Optional[int] = None
    rate: Optional[float] = None

    @field_validator("over", "runs", "wickets", "count", mode="before")
    @classmethod
    def _coerce_intish(cls, v: Any) -> Any:
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            return int(m.group()) if m else None
        if isinstance(v, float):
            return int(v)
        return v

    @field_validator("rate", mode="before")
    @classmethod
    def _coerce_float(cls, v: Any) -> Any:
        if isinstance(v, str):
            m = re.search(r"\d+(?:\.\d+)?", v)
            return float(m.group()) if m else None
        return v

    @field_validator("kind", mode="before")
    @classmethod
    def _kind_must_be_known(cls, v: Any) -> Any:
        """An unknown kind degrades to 'other' (no checkable claim) instead of failing the
        whole analyst turn - the kernel verifier is the authority, not this enum."""
        if isinstance(v, str) and v not in _CLAIM_KINDS:
            return "other"
        return v

    @model_validator(mode="before")
    @classmethod
    def _recover_agent_shape(cls, data: Any) -> Any:
        """Models reach for several shapes for the same claim.

        Recovery belongs on the model, not on the tool: the kernel runs the same verifier on
        the analyst's payload without going through `verify_claim`, and a shape tolerated in
        one path but not the other silently yields UNVERIFIABLE "no checkable claim" - which
        is exactly what happened to a correct 142/4 wicket claim.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "raw_phrase" not in out:
            for k in ("phrase", "claim", "text", "quoted"):
                if isinstance(out.get(k), str):
                    out["raw_phrase"] = out[k]
                    break
        score = out.get("score")
        if isinstance(score, str) and "/" in score:
            runs, _, wickets = score.partition("/")
            out.setdefault("runs", runs)
            out.setdefault("wickets", wickets)
            out.setdefault("kind", "score")
        if out.get("runs") in (None, "", 0) and isinstance(out.get("raw_phrase"), str):
            # The analyst repeatedly quotes "142/3" verbatim and still leaves runs/wickets
            # null. Reading the score shape out of its own quote is parsing, not invention:
            # the grounding check below still requires those digits in the spoken text.
            m = SCORE_RE.search(out["raw_phrase"])
            if m:
                out["runs"], out["wickets"] = int(m.group(1)), int(m.group(2))
        if out.get("kind") in (None, "", "other") and _usable_fields(out):
            if out.get("rate") is not None:
                out["kind"] = "required_rate"
            elif out.get("runs") is not None:
                out["kind"] = "score"
            elif out.get("event") in ("wicket", "out", "dismissal") or out.get("player"):
                out["kind"] = "wicket_by_player"
        return out


def spoken_score_claim(text: str) -> Optional[StructuredClaim]:
    """A score shape in the transcript is a factual claim, whether or not a model notices.

    The kernel verifier used to fire only when the analyst *labelled* the utterance a
    factual claim, and on the live pass it labelled "Score 142 hai abhi, 142/3, same as
    before" as filler - so the contradiction that this whole pack exists to catch reached
    the supervisor unverified and was skipped. Parsing a score the creator literally said
    is not invention: the digits come from the transcript and grounding still applies.
    """
    text = text or ""
    for rx in SCORE_RES:
        m = rx.search(text)
        if not m:
            continue
        runs, wickets = int(m.group(1)), int(m.group(2))
        # A T20 score is tens of runs and single-figure wickets. The guard is what stops
        # "50 for 50" or "3 for 2 in the third over" being read as a score line.
        if runs < 10 or wickets > 10:
            continue
        return StructuredClaim(kind="score", raw_phrase=m.group(0), runs=runs, wickets=wickets)
    return None


def spoken_atoms(text: str) -> set[str]:
    """Every number the creator could be quoting, split into its exact components.

    "142/4" licenses a claim of 142 runs *and* 4 wickets, so the compound token has to
    decompose - but only on its own separators. Substring matching would let "142" vouch for
    a claim of 42, which is exactly the kind of invented number this gate exists to stop.
    """
    atoms = set(NUMBER_RE.findall(text or ""))
    for tok in list(atoms):
        for part in re.split(r"[./]", tok):
            if part:
                atoms.add(part)
    return atoms


def _claim_digits(claim: StructuredClaim) -> set[str]:
    """The magnitudes the creator asserted. `over` is excluded: it is a locator resolved from
    match state, and the text it produces on a card is feed-derived either way."""
    out: set[str] = set()
    for v in (claim.runs, claim.wickets, claim.count):
        if v is not None:
            out.add(str(v))
    if claim.rate is not None:
        out.add(str(claim.rate))
        out.add(f"{claim.rate:.2f}")
    if claim.runs is not None and claim.wickets is not None:
        out.add(f"{claim.runs}/{claim.wickets}")
    return out


def verify(claim: StructuredClaim, utterance_text: str, feed: Feed,
           state: Optional[ScoreState]) -> Verdict:
    if claim.kind == "other" or state is None:
        return mk_verdict(kind=VerdictKind.UNVERIFIABLE, detail="no checkable claim",
                       claimed=claim.raw_phrase)

    # 1. grounding: refuse to verify numbers the creator did not say
    spoken = spoken_atoms(utterance_text)
    for tok in _claim_digits(claim):
        base = tok.split("/")[0]
        if tok not in spoken and base not in spoken:
            return mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=claim.raw_phrase,
                           detail=f"extraction_not_grounded: '{tok}' is not in the utterance text")

    if claim.kind == "score":
        return _verify_score(claim, state, feed)
    if claim.kind == "wicket_by_player":
        return _verify_wicket(claim, state, feed)
    if claim.kind == "required_rate":
        return _verify_rate(claim, state)
    if claim.kind in ("boundary_in_over", "yorker_in_over"):
        return _verify_over_stat(claim, state, feed)
    if claim.kind == "team_total":
        return _verify_score(StructuredClaim(kind="score", raw_phrase=claim.raw_phrase,
                                             runs=claim.runs), state, feed)
    return mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=claim.raw_phrase,
                   detail="unsupported claim kind")


def _verify_score(claim: StructuredClaim, state: ScoreState, feed: Feed) -> Verdict:
    claimed = f"{claim.runs}" + (f"/{claim.wickets}" if claim.wickets is not None else "")
    if claim.runs == state.runs and (claim.wickets is None or claim.wickets == state.wickets):
        return mk_verdict(kind=VerdictKind.CONFIRMED, claimed=claimed, observed=state.score,
                       matched_event_id=state.last_event_id, matched_over=state.over_label,
                       detail="matches the live feed")

    seen = [e for e in feed.events if e.balls_faced <= state.ball_index]
    match: Optional[MatchEvent] = None
    for e in reversed(seen):
        runs_ok = claim.runs == e.innos_runs
        wk_ok = claim.wickets is None or claim.wickets == e.wickets
        if runs_ok and wk_ok:
            match = e
            break
    if match is not None:
        age = state.ball_index - match.balls_faced
        kind = VerdictKind.CONFIRMED_LAG if age <= 2 else VerdictKind.CONFLICT
        return mk_verdict(kind=kind, claimed=claimed, observed=state.score,
                       matched_event_id=match.id, matched_over=match.over_label, age_balls=age,
                       detail=f"was the score at {match.over_label}, {age} balls earlier")
    return mk_verdict(kind=VerdictKind.CONFLICT, claimed=claimed, observed=state.score,
                   detail="no earlier feed state matches this score")


def _verify_wicket(claim: StructuredClaim, state: ScoreState, feed: Feed) -> Verdict:
    if not claim.player:
        return mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=claim.raw_phrase,
                       detail="no player named")
    seen = [e for e in feed.events if e.balls_faced <= state.ball_index]
    for e in reversed(seen[-8:]):
        if e.wicket and e.out and _name_match(claim.player, e.out):
            return mk_verdict(kind=VerdictKind.CONFIRMED, claimed=claim.raw_phrase,
                           observed=f"{e.out} out at {e.over_label}, score {e.score}",
                           matched_event_id=e.id, matched_over=e.over_label,
                           detail="wicket matches the feed")
    for e in reversed(seen[-8:]):
        if e.wicket and _name_match(claim.player, e.bowler):
            return mk_verdict(kind=VerdictKind.CONFLICT, claimed=claim.raw_phrase,
                           observed=f"{e.out} got out, bowled by {e.bowler} at {e.over_label}",
                           matched_event_id=e.id, matched_over=e.over_label,
                           detail="named player is the bowler, not the batsman out")
    return mk_verdict(kind=VerdictKind.CONFLICT, claimed=claim.raw_phrase,
                   observed=f"no wicket for {claim.player} in the last 8 balls ({state.score})",
                   detail="wicket not found in feed")


def _verify_rate(claim: StructuredClaim, state: ScoreState) -> Verdict:
    if claim.rate is None or state.rrr is None:
        return mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=claim.raw_phrase)
    if abs(claim.rate - state.rrr) <= 0.06:
        return mk_verdict(kind=VerdictKind.CONFIRMED, claimed=str(claim.rate),
                       observed=str(state.rrr), matched_over=state.over_label,
                       matched_event_id=state.last_event_id, detail="required rate matches")
    return mk_verdict(kind=VerdictKind.CONFLICT, claimed=str(claim.rate), observed=str(state.rrr),
                   matched_over=state.over_label, matched_event_id=state.last_event_id,
                   detail="required rate differs from feed")


def _verify_over_stat(claim: StructuredClaim, state: ScoreState, feed: Feed) -> Verdict:
    over = claim.over if claim.over is not None else int(state.over_label.split(".")[0])
    rows = [e for e in feed.events if e.over == over and e.balls_faced <= state.ball_index]
    if claim.kind == "yorker_in_over":
        n = len([e for e in rows if e.ball_type == "yorker"])
        obs = f"{n} yorkers bowled in over {over}"
    else:
        n = len([e for e in rows if e.runs >= 4])
        obs = f"{n} boundaries in over {over}"
    if claim.count is None:
        return mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=claim.raw_phrase, observed=obs)
    kind = VerdictKind.CONFIRMED if n == claim.count else VerdictKind.CONFLICT
    return mk_verdict(kind=kind, claimed=claim.raw_phrase, observed=obs,
                   matched_event_id=rows[-1].id if rows else None,
                   matched_over=f"{over}", detail="counted from the feed")


def _name_match(a: str, b: str) -> bool:
    al, bl = a.lower().strip(), (b or "").lower().strip()
    if not bl:
        return False
    if al == bl or al in bl or bl in al:
        return True
    aw, bw = set(al.split()), set(bl.split())
    return bool(aw & bw - {"the", "a"})
