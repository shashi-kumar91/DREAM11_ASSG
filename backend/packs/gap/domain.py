"""Pack B domain: playback telemetry, scoreboard frames and material-event rules.

Why the fixtures live here as data rather than being synthesised on the fly: the gap agent's
whole job is to reason about *what the player actually did*, so the evidence has to be a
log the agent can be wrong about. Handing it a ready-made "the gap was 16.2-16.6" answer
would test nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ...kernel.schemas import MatchEvent
from ..creator.domain import Feed, FIXTURES

MATERIAL_KINDS = {"wicket", "boundary", "six", "milestone", "review", "drs", "innings_break"}


class HeartbeatRow(BaseModel):
    """One simulated player heartbeat. `event_id` is what the screen was showing."""

    t: float
    event_id: str = ""
    kind: Literal["play", "ad_start", "ad_end", "stall", "resume", "join", "gap"] = "play"
    note: str = ""


class Frame(BaseModel):
    frame_id: str
    path: str
    over_label: str
    t_wall: float
    true_score: str
    true_rrr: Optional[float] = None
    injected_fault: bool = False

    def image_bytes(self, root: Path = FIXTURES) -> bytes:
        return (root / self.path).read_bytes()


class GapScenario(BaseModel):
    id: str
    name: str
    interruption_type: Literal["ad", "buffer", "app_switch", "late_join", "unknown"] = "unknown"
    resume_t: float
    resume_cursor: str
    fan_team: str = ""
    heartbeats: list[HeartbeatRow] = Field(default_factory=list)
    blurb: str = ""
    expected: dict = Field(default_factory=dict)
    # The simulated fan: substring of the question -> answer. Headless runs need a stand-in
    # for the person, and the answer has to be part of the fixture so a driver cannot
    # quietly choose the boundary it wanted.
    fan_answers: dict[str, str] = Field(default_factory=dict)


class Boundary(BaseModel):
    """Deterministic estimate of the last ball the fan actually saw."""

    candidates: list[str] = Field(default_factory=list)   # event ids, most likely first
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)
    needs_fan_input: bool = False
    clean_last_position: bool = True

    def labels(self, feed: Feed) -> list[str]:
        return [feed.by_id[c].over_label for c in self.candidates if c in feed.by_id]


def is_material(e: MatchEvent) -> bool:
    return bool(e.wicket) or e.runs >= 4 or e.kind in MATERIAL_KINDS


def material_events(events: list[MatchEvent]) -> list[MatchEvent]:
    return [e for e in events if is_material(e)]


class GapFixtures:
    def __init__(self, feed: Feed, frames: list[Frame], scenarios: list[GapScenario]):
        self.feed = feed
        self.frames = frames
        self.by_frame_id = {f.frame_id: f for f in frames}
        self.scenarios = {s.id: s for s in scenarios}

    @classmethod
    def load(cls, root: Path = FIXTURES) -> "GapFixtures":
        feed = Feed.load(root / "match_feed.json", root / "match_meta.json")
        frames = [Frame.model_validate(f) for f in
                  json.loads((root / "frames.json").read_text(encoding="utf-8"))]
        raw = json.loads((root / "gap_scenarios.json").read_text(encoding="utf-8"))
        return cls(feed, frames, [GapScenario.model_validate(s) for s in raw["scenarios"]])

    def frame_at(self, t: float) -> Optional[Frame]:
        """The scoreboard frame nearest at or before t, if one was captured."""
        prior = [f for f in self.frames if f.t_wall <= t + 0.001]
        return max(prior, key=lambda f: f.t_wall) if prior else None

    def frame_for_over(self, label: str) -> Optional[Frame]:
        return next((f for f in self.frames if f.over_label == label), None)
