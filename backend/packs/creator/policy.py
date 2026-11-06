"""Surface policy: how much of this fan's attention a card may take.

The agent supplies salience; this code supplies the bounds. Conflict and settlement cards
bypass the rate limit because withholding them would be a correctness failure, not a
taste one. Quiet mode changes modality, never truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from ...kernel.schemas import CardDraft

SurfaceMode = Literal["card", "collapsed", "silent", "offer_clip"]

MANDATORY_KINDS = {"conflict", "settlement", "query_answer", "receipt"}


@dataclass
class FanContext:
    quiet: bool = False
    data_budget_kb_per_min: float = 60.0
    spoiler_tolerance: str = "strict"
    favorite_players: list[str] = field(default_factory=list)
    sound: bool = True


@dataclass
class SurfaceDecision:
    mode: SurfaceMode
    reason: str
    salience: float = 0.0


class SurfacePolicy:
    def __init__(self, salience_floor: float = 0.45, max_cards_per_min: int = 5,
                 min_gap_s: float = 6.0):
        self.salience_floor = salience_floor
        self.max_cards_per_min = max_cards_per_min
        self.min_gap_s = min_gap_s

    def decide(self, draft: CardDraft, t: float, recent: list[float],
               fan: FanContext, clip_requested: bool = False) -> SurfaceDecision:
        sal = draft.salience
        if draft.kind in MANDATORY_KINDS:
            return SurfaceDecision("card", f"{draft.kind} cards are never rate-limited", sal)
        if sal < self.salience_floor:
            return SurfaceDecision("silent", f"salience {sal:.2f} below floor {self.salience_floor}", sal)
        in_last_min = [r for r in recent if t - r <= 60.0]
        if len(in_last_min) >= self.max_cards_per_min:
            return SurfaceDecision("collapsed", "fan's card budget for this minute is spent", sal)
        if recent and t - max(recent) < self.min_gap_s:
            return SurfaceDecision("collapsed", f"less than {self.min_gap_s}s since last card", sal)
        if fan.quiet:
            return SurfaceDecision("card", "quiet mode: text only, no media fetch", sal)
        if clip_requested and sal >= 0.8:
            return SurfaceDecision("offer_clip", "clip offered only behind explicit permission", sal)
        return SurfaceDecision("card", "above floor and inside rate limits", sal)

    def should_ask_before_clip(self, fan: Optional[FanContext]) -> bool:
        return True
