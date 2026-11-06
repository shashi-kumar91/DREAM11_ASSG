"""Feed access and deterministic match state.

`state_at` is the only thing that knows "what the fan has seen so far". Everything
downstream (verifier, reference resolution, settlement) reads derived state, never raw
event lists, so an agent cannot peek ahead even by accident.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ...kernel.clamp import ClampedFeed
from ...kernel.schemas import MatchEvent, MatchMeta, ScoreState

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class Feed:
    def __init__(self, events: list[MatchEvent], meta: MatchMeta):
        self.events = events
        self.meta = meta
        self.by_id = {e.id: e for e in events}
        self.index = {e.id: i for i, e in enumerate(events)}

    @classmethod
    def load(cls, path: Optional[Path] = None, meta_path: Optional[Path] = None) -> "Feed":
        events = [MatchEvent.model_validate(e) for e in
                  json.loads((path or FIXTURES / "match_feed.json").read_text(encoding="utf-8"))]
        meta = MatchMeta.model_validate(
            json.loads((meta_path or FIXTURES / "match_meta.json").read_text(encoding="utf-8")))
        return cls(events, meta)

    def clamped(self, resume_cursor: Optional[str] = None) -> ClampedFeed:
        return ClampedFeed(self.events, resume_cursor=resume_cursor)

    def state_at(self, t: float) -> Optional[ScoreState]:
        """Match state as of wall-clock t, using the last event that has already happened."""
        seen = [e for e in self.events if e.t_wall <= t]
        if not seen:
            return None
        e = seen[-1]
        return self.state_from(e)

    @staticmethod
    def state_from(e: MatchEvent) -> ScoreState:
        return ScoreState(
            over_label=e.over_label, ball_index=e.balls_faced, striker=e.striker,
            non_striker=e.non_striker, bowler=e.bowler, score=e.score, runs=e.innos_runs,
            wickets=e.wickets, rrr=e.rrr, crr=e.crr, last_event_id=e.id,
            wicket=e.wicket, out_player=e.out, ball_type=e.ball_type, kind=e.kind,
        )

    def next_over_bowler(self, t: float) -> Optional[str]:
        """Who bowls the over after the one in progress at t. Deterministic, feed-derived."""
        seen = [e for e in self.events if e.t_wall <= t]
        if not seen:
            return None
        cur_over = int(seen[-1].over_label.split(".")[0])
        for e in self.events:
            if int(e.over_label.split(".")[0]) > cur_over:
                return e.bowler
        return None

    def events_in_over(self, over: int) -> list[MatchEvent]:
        return [e for e in self.events if e.over_label.split(".")[0] == str(over)]

    def over_complete(self, over: int, upto_t: float) -> bool:
        rows = self.events_in_over(over)
        return bool(rows) and rows[-1].t_wall <= upto_t and rows[-1].over_label.endswith(".6")

    def find_by_over(self, label: str) -> Optional[MatchEvent]:
        for e in self.events:
            if e.over_label == label:
                return e
        return None


def load_feed() -> Feed:
    return Feed.load()
