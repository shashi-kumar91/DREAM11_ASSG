"""Value-of-information: when is it worth spending the fan's patience to ask?

Asking is only justified when the answer would actually change the receipt. Two candidate
boundaries that both miss the same set of key events are the same receipt, so the agent
stays quiet and the trace says why. This is deterministic so that "the agent asked a useful
question" is a measured property rather than a vibe.
"""
from __future__ import annotations

from ...kernel.schemas import MatchEvent
from .domain import Boundary, is_material

CONFIDENT_ABOVE = 0.80


def receipts_for(feed, candidates: list[str], resume_cursor: str,
                 clamp_to: str) -> dict[str, list[MatchEvent]]:
    """Per candidate boundary: the material events the fan would have missed.

    The resume ball is excluded: the fan came back *on* it, so it is the one ball they are
    watching rather than missing. Including it read as a receipt citing a spoiler.
    """
    hi = min(feed.index.get(resume_cursor, len(feed.events)),
             feed.index.get(clamp_to, len(feed.events)))
    out: dict[str, list[MatchEvent]] = {}
    for c in candidates:
        if c not in feed.index:
            continue
        window = feed.events[feed.index[c] + 1: hi]
        out[c] = [e for e in window if is_material(e)]
    return out


def distinguishing_event(receipts: dict[str, list[MatchEvent]]) -> MatchEvent | None:
    """The event whose presence in the receipt depends on which candidate is right."""
    ids = {tuple(e.id for e in evs) for evs in receipts.values()}
    if len(ids) <= 1:
        return None
    seen: dict[str, MatchEvent] = {}
    for evs in receipts.values():
        for e in evs:
            seen.setdefault(e.id, e)
    every = [set(e.id for e in evs) for evs in receipts.values()]
    for eid, e in seen.items():
        if any(eid in s for s in every) and any(eid not in s for s in every):
            return e
    return None


def boundary_is_ambiguous(boundary: Boundary) -> bool:
    return len(boundary.candidates) > 1 or boundary.confidence < CONFIDENT_ABOVE


def boundary_question(feed, boundary: Boundary, resume_cursor: str,
                      clamp_to: str, fan_team: str = "") -> tuple[str, list[str]]:
    """A specific, evidence-shaped question - never "what did you miss?". Options are the
    candidate boundaries in the fan's own words, so the answer maps back onto a candidate.

    A late join has no boundary to resolve: the honest question is about how much of the
    innings the fan wants, and it still has to name the span rather than say "everything".
    """
    cursor = feed.by_id[resume_cursor].over_label
    if not boundary.candidates:
        span = f"{feed.events[0].over_label}-{cursor}"
        who = f" for {fan_team}" if fan_team else ""
        return (f"You joined mid-innings, so your player log has no ball in it yet. "
                f"Want the recap of {span}?",
                [f"Everything in {span}", f"Only wickets and boundaries{who}",
                 "Nothing, take me to live"])
    ev = distinguishing_event(receipts_for(feed, boundary.candidates, resume_cursor, clamp_to))
    labels = [feed.by_id[c].over_label for c in boundary.candidates if c in feed.by_id]
    if ev is None:
        return (f"You were away from ball {labels[-1]}. Want the recap of overs "
                f"{labels[0]}-{cursor}?",
                ["Yes, recap it", "No, take me to live"])
    what = "wicket" if ev.wicket else ("boundary" if ev.runs >= 4 else ev.kind)
    return (f"Did you see the {what} at ball {ev.over_label} "
            f"({ev.score}) before you left?",
            [f"Yes, I saw ball {ev.over_label}", f"No, I left at {labels[0]}"])


def should_ask(feed, boundary: Boundary, resume_cursor: str, clamp_to: str) -> tuple[bool, str]:
    if not boundary.candidates:
        # Nothing was ever rendered for this fan. The window is the whole clamped innings and
        # only the fan can scope it, so asking is the only correct move.
        return True, ("no ball in the playback log (late join): the window cannot be bounded "
                      "from telemetry, so the fan has to scope it")
    if not boundary_is_ambiguous(boundary):
        return False, (f"boundary confident ({boundary.confidence}) and single candidate; "
                       "no question")
    receipts = receipts_for(feed, boundary.candidates, resume_cursor, clamp_to)
    if len({tuple(e.id for e in evs) for evs in receipts.values()}) <= 1:
        return False, ("boundary uncertain but immaterial: every candidate yields the same "
                       "key events, no question")
    return True, "candidates change the receipt, so the answer has value"
