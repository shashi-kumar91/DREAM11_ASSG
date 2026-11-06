"""Deterministic gap-boundary estimation.

The agent does not get to guess where the fan lost the stream. This function turns the
player heartbeat log into a set of candidate last-seen balls plus a confidence, following
fixed signal rules, and the agent only ever reasons *over* that set. A boundary invented by
a model is the one failure mode a catch-up product cannot ship with.
"""
from __future__ import annotations

from ...kernel.schemas import MatchEvent
from .domain import Boundary, GapScenario, HeartbeatRow

VIEWED_KINDS = {"play"}
INTERRUPT_KINDS = {"ad_start", "stall", "gap"}
CLEAN_CONF = 0.95
STALL_PENALTY = 0.05
AD_PENALTY = 0.30
NO_RESUME_PENALTY = 0.20


def estimate(feed, scenario: GapScenario) -> Boundary:
    """Where the fan lost the stream, from the player log only.

    The distinction that matters: an `ad_end` or `resume` heartbeat reports where the player
    *restarted*, not what the fan watched. Reading those as the last-seen ball makes every
    gap empty and the whole product a no-op - so only rows the renderer actually painted,
    before the interruption began, are evidence of a boundary.
    """
    rows = scenario.heartbeats
    played = [r for r in rows if r.kind in VIEWED_KINDS and r.event_id in feed.index]
    interrupt = next((r for r in rows if r.kind in INTERRUPT_KINDS), None)
    if not played:
        return Boundary(candidates=[], confidence=0.0, needs_fan_input=True,
                        clean_last_position=False,
                        notes=["no ball was ever rendered for this fan (late join): the "
                               "telemetry cannot give a boundary; only the fan can scope "
                               "the window"])

    watched = [r for r in played if interrupt is None or r.t < interrupt.t]
    if not watched:
        return Boundary(candidates=[], confidence=0.0, needs_fan_input=True,
                        clean_last_position=False,
                        notes=[f"the interruption began before the first rendered ball "
                               f"({interrupt.kind} at {interrupt.t:.0f}s): no boundary in the log"])

    last = watched[-1]
    idx = feed.index[last.event_id]
    b = Boundary(candidates=[last.event_id], confidence=CLEAN_CONF,
                 notes=[f"last ball the player rendered: {feed.events[idx].over_label} "
                        f"({last.kind})"])

    if interrupt is not None:
        interrupted = (feed.events[feed.index[interrupt.event_id]].over_label
                       if interrupt.event_id in feed.index else "?")
    if interrupt is not None and interrupt.kind == "ad_start":
        # The overlay drew over the player around this ball, so even this ball is not certain
        # evidence - widen one ball either way and say so.
        b.clean_last_position = False
        b.confidence = round(b.confidence - AD_PENALTY, 3)
        b.candidates = _neighbours(feed, idx, scenario.resume_cursor)
        b.notes.append(f"ad overlay covered the screen over ball {interrupted}: boundary "
                       f"widened by one ball either side, confidence -{AD_PENALTY}")
    elif interrupt is not None and interrupt.kind == "stall":
        b.confidence = round(b.confidence - STALL_PENALTY, 3)
        b.notes.append(f"player stalled on ball {interrupted}: the stalled ball was never "
                       f"rendered, so it belongs to the gap, not to what the fan saw "
                       f"(-{STALL_PENALTY})")
    if not any(r.kind == "resume" for r in rows):
        b.confidence = round(b.confidence - NO_RESUME_PENALTY, 3)
        b.notes.append(f"no resume marker in the log: confidence -{NO_RESUME_PENALTY}")
    return b


def _neighbours(feed, idx: int, resume_cursor: str) -> list[str]:
    """Most-likely first: the reported position, then the ball before it, then the ball after.
    A candidate past the resume cursor is not a boundary, it is a spoiler, so it is dropped."""
    hi = feed.index.get(resume_cursor, len(feed.events) - 1)
    out = [feed.events[idx].id]
    for j in (idx - 1, idx + 1):
        # Strictly before the resume ball. `j <= hi` let the resume ball itself become a
        # candidate, which is nonsense ("the last ball you saw is the one you came back on") and
        # produced an empty window for every fan whose gap was one ball long.
        if 0 <= j < hi and j != idx:
            out.append(feed.events[j].id)
    return out


def missed_window(feed, boundary: str, resume_cursor: str) -> list[MatchEvent]:
    """Events strictly after the chosen boundary and strictly before the resume cursor.

    `boundary` is an event id - the id the telemetry or the fan settled on, not the
    candidate list.

    The resume ball is excluded on purpose, and this is the one rule the product exists to
    keep: the fan resumed *at* that ball, so they are watching it. Printing it as "you
    missed this" is a spoiler wearing a receipt's clothes - a live screenshot caught the
    card listing the resume ball while the shield beside it said nothing past that point had
    been released. The end-of-window score is still read from the resume event (that is what
    the scoreboard shows at the fan's position), but the ball itself is not in the window.
    """
    start = feed.index[boundary] + 1 if boundary in feed.index else 0
    end = feed.index.get(resume_cursor, len(feed.events))
    return [e for e in feed.events[start:end]]
