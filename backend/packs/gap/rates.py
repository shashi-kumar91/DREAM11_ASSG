"""Required-rate arithmetic, computed rather than quoted.

A catch-up receipt that says "required rate jumped" has to be able to show the division.
This module is the only place Pack B turns runs and balls into a rate, and it deliberately
recomputes from the raw fields instead of trusting the feed's own `rrr`, so a stale fixture
column cannot silently become a number on the fan's screen.
"""
from __future__ import annotations

from ...kernel.schemas import MatchEvent


def required_rate(target: int, runs: int, balls_faced: int, total_balls: int = 120) -> float:
    left = total_balls - balls_faced
    if left <= 0:
        return 0.0
    return round(max(0.0, (target - runs) / left * 6), 2)


def change(target: int, before: MatchEvent, after: MatchEvent,
           total_balls: int = 120) -> dict:
    r_before = required_rate(target, before.innos_runs, before.balls_faced, total_balls)
    r_after = required_rate(target, after.innos_runs, after.balls_faced, total_balls)
    return {
        "before_over": before.over_label, "after_over": after.over_label,
        "before_score": before.score, "after_score": after.score,
        "before_rrr": r_before, "after_rrr": r_after, "delta": round(r_after - r_before, 2),
        "runs_needed": max(0, target - after.innos_runs),
        "balls_left": total_balls - after.balls_faced,
        "consistent_with_feed": r_after == after.rrr,
    }
