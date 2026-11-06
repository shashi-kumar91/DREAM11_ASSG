"""Shared kernel contracts for both agent packs.

The invariant that everything else follows from:
    The LLM proposes. Deterministic code verifies, labels and commits.
    The LLM never authors a number, a "verified" badge, or an event the fan has not reached.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Match domain
# --------------------------------------------------------------------------- #


class MatchEvent(BaseModel):
    """One deterministic ball in the replay feed."""

    id: str
    t_wall: float = Field(description="Wall-clock seconds from feed start when this event is live.")
    over_label: str = Field(description="Cricket-style label, e.g. '16.3'.")
    kind: Literal["ball", "wicket", "boundary", "review", "interval", "innos_end", "other"]
    runs: int = 0
    wicket: bool = False
    text: str
    actors: list[str] = Field(default_factory=list)
    striker: str = ""
    non_striker: str = ""
    bowler: str = ""
    out: Optional[str] = None
    ball_type: str = ""
    score: str = Field(default="", description="Cumulative 'runs/wickets' after this event.")
    balls_faced: int = 0
    innos_runs: int = 0
    innos_balls: int = 0
    rrr: Optional[float] = None
    crr: Optional[float] = None
    tags: list[str] = Field(default_factory=list)

    @property
    def material(self) -> bool:
        return self.kind in ("wicket", "boundary", "review", "innos_end") or self.runs >= 4

    @property
    def wickets(self) -> int:
        return int(self.score.split("/")[1]) if "/" in self.score else 0

    @property
    def over(self) -> int:
        return int(self.over_label.split(".")[0])

    @property
    def ball_in_over(self) -> int:
        parts = self.over_label.split(".")
        return int(parts[1]) if len(parts) > 1 else 0


class MatchMeta(BaseModel):
    match_id: str
    title: str
    team_a: str
    team_b: str
    format: str
    squads: list[str]
    target: Optional[int] = None
    total_overs: int = 20
    keyterms: list[str] = []
    creator: Optional[str] = None


class ScoreState(BaseModel):
    """Derived by deterministic code only. Never by the LLM."""

    over_label: str
    ball_index: int
    striker: str
    non_striker: str
    bowler: str
    score: str
    runs: int
    wickets: int
    rrr: Optional[float] = None
    crr: Optional[float] = None
    last_event_id: str
    wicket: bool = False
    out_player: Optional[str] = None
    ball_type: str = ""
    kind: str = "ball"


# --------------------------------------------------------------------------- #
# Perception
# --------------------------------------------------------------------------- #


class Utterance(BaseModel):
    id: str
    t_start: float
    t_end: float
    text: str
    speaker: str
    asr_confidence: float = Field(ge=0.0, le=1.0)
    asr_source: Literal["live", "cached", "typed"] = "cached"
    keyterms_applied: list[str] = Field(default_factory=list)
    raw_provider_text: Optional[str] = None


# --------------------------------------------------------------------------- #
# Truth / labelling
# --------------------------------------------------------------------------- #


class InsightType(str, Enum):
    factual_claim = "factual_claim"
    prediction = "prediction"
    opinion = "opinion"
    tactic = "tactic"
    rule_explanation = "rule_explanation"
    quote = "quote"
    answer = "answer"
    filler = "filler"


class TruthLabel(str, Enum):
    VERIFIED = "VERIFIED vs feed"
    CONFLICT = "CONFLICTS with feed"
    OPINION = "Opinion, not match fact"
    PREDICTION = "Prediction, being tracked"
    SETTLED_HIT = "Prediction HIT"
    SETTLED_MISS = "Prediction MISSED"
    SETTLED_VOID = "Prediction void"
    UNVERIFIABLE = "Not checkable from feed"
    REPORTED = "Creator said"
    FAN_QUERY = "Answer from creator memory"
    NO_EVIDENCE = "No evidence — nothing invented"
    EVIDENCED = "Evidence-backed"


class VerdictKind(str, Enum):
    CONFIRMED = "CONFIRMED"
    CONFIRMED_LAG = "CONFIRMED_LAG"
    CONFLICT = "CONFLICT"
    UNVERIFIABLE = "UNVERIFIABLE"


class Verdict(BaseModel):
    """Produced only by the deterministic verifier, never by an LLM."""

    id: str = Field(default_factory=lambda: new_id("ver"))
    kind: VerdictKind
    claimed: str = ""
    observed: str = ""
    matched_event_id: Optional[str] = None
    matched_over: Optional[str] = None
    age_balls: int = 0
    detail: str = ""


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

CardKind = Literal["insight", "conflict", "prediction", "settlement", "query_answer",
                   "receipt", "noop", "permission", "status"]


class CardDraft(BaseModel):
    """What an LLM is allowed to propose. Commit() turns this into a Card or rejects it."""

    kind: CardKind = "insight"
    insight_type: Optional[InsightType] = None
    headline: str = Field(max_length=90)
    body: str = Field(max_length=700)
    salience: float = Field(default=0.5, ge=0.0, le=1.0,
                            description="Agent's 0-1 usefulness estimate; surface policy gates on it.")
    cited_utterance_ids: list[str] = Field(default_factory=list)
    cited_event_ids: list[str] = Field(default_factory=list)
    cited_verdict_ids: list[str] = Field(default_factory=list)
    cited_tally_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Card(CardDraft):
    id: str
    episode_id: str
    truth_label: TruthLabel
    attribution: str
    timestamp_label: str = ""
    committed_by: Literal["kernel.commit"] = "kernel.commit"
    source_badges: dict[str, str] = Field(default_factory=dict)
    template_used: Optional[str] = None
    numbers: list[str] = Field(default_factory=list, description="Numeric tokens the validator allowed.")
    created_at: float = Field(default_factory=now)


# --------------------------------------------------------------------------- #
# Prediction ledger
# --------------------------------------------------------------------------- #


class Prediction(BaseModel):
    id: str
    utterance_id: str
    t_logged: float
    text: str
    bowler: Optional[str] = None
    batter: Optional[str] = None
    over: Optional[int] = None
    metric: Optional[Literal["yorker_count", "wicket", "runs_lte", "boundary_count",
                             "dot_balls"]] = None
    threshold: Optional[int] = None
    status: Literal["open", "hit", "miss", "void"] = "open"
    t_settled: Optional[float] = None
    tally_text: Optional[str] = None
    settle_event_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Uncertainty / human-in-the-loop
# --------------------------------------------------------------------------- #


class Uncertainty(BaseModel):
    id: str = Field(default_factory=lambda: new_id("unc"))
    kind: Literal["asr_low_conf", "asr_empty", "ambiguous_reference", "boundary_low_conf",
                 "claim_unclear"]
    detail: str
    raised_by_agent: str
    t: float = Field(default_factory=now)
    resolved: bool = False
    resolution: Optional[str] = None


class PendingQuestion(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    text: str
    reason: str
    options: list[str] = Field(default_factory=list)
    expected_info_gain: float = Field(ge=0.0, le=1.0)
    blocking: bool = True
    answer: Optional[str] = None
    t_asked: float = Field(default_factory=now)
    t_answered: Optional[float] = None


# --------------------------------------------------------------------------- #
# Multi-agent dispatch
# --------------------------------------------------------------------------- #

AgentRole = Literal[
    "supervisor",
    "perceiver",     # resolves references/entities from raw speech
    "analyst",       # classifies insight type, drafts card, scores salience
    "verifier",      # adversarial evidence-checking, proposes refutations
    "strategist",    # surface/quiet policy (A) or recovery modality (B)
    "investigator",  # (B) gathers boundary evidence across sources
    "questioner",    # (B) value-of-information reasoning
    "critic",        # reviews the composed output against objective + bounds
]

ActionName = Literal["dispatch", "tool", "ask_user", "request_permission",
                     "commit", "skip", "stop", "note"]


class SupervisorDecision(BaseModel):
    """The only thing the supervisor LLM may output."""

    thought: str = Field(max_length=400, description="Why, in one line, referencing observed state.")
    action: ActionName
    agent: Optional[AgentRole] = None
    tool: Optional[str] = None
    args_json: str = Field(default="{}", description="JSON object of tool arguments, or {}.")
    reason: str = Field(default="", max_length=300)
    card: Optional[CardDraft] = None
    question: Optional[str] = None
    options: list[str] = Field(default_factory=list)


class DispatchRecord(BaseModel):
    step: int
    t: float = Field(default_factory=now)
    action: ActionName
    agent: Optional[str] = None
    tool: Optional[str] = None
    args_preview: str = ""
    result_preview: str = ""
    thought: str = ""
    reason: str = ""
    ms: float = 0.0
    ok: bool = True
    error: Optional[str] = None
    llm_tokens: int = 0
    budget_after: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Budgets
# --------------------------------------------------------------------------- #


class Budget(BaseModel):
    max_llm_calls: int = 14
    max_tool_calls: int = 10
    max_ms: float = 150_000.0
    max_specialist_rounds: int = 8

    def remaining(self, used: "BudgetUsed") -> dict[str, float]:
        return {
            "llm_calls": max(0, self.max_llm_calls - used.llm_calls),
            "tool_calls": max(0, self.max_tool_calls - used.tool_calls),
            "ms": max(0.0, self.max_ms - used.elapsed_ms),
            "specialist_rounds": max(0, self.max_specialist_rounds - used.specialist_rounds),
        }


class BudgetUsed(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    specialist_rounds: int = 0
    started_at: float = Field(default_factory=now)
    elapsed_ms: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0

    def touch(self) -> None:
        self.elapsed_ms = (now() - self.started_at) * 1000.0


class Exhausted(BaseModel):
    kind: Literal["budget", "steps", "walls", "circuit_break"]
    detail: str


# --------------------------------------------------------------------------- #
# Episodes
# --------------------------------------------------------------------------- #


class TerminalState(str, Enum):
    COMMITTED = "committed"
    SKIPPED = "skipped"
    STOPPED_NOOP = "stopped_noop"
    ASKED = "awaiting_user"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


class Episode(BaseModel):
    """One unit of perception -> decision -> action."""

    id: str = Field(default_factory=lambda: new_id("ep"))
    pack: Literal["creator", "gap"]
    trigger: str
    started_at: float = Field(default_factory=now)
    terminal: Optional[TerminalState] = None
    terminal_reason: str = ""
    dispatches: list[DispatchRecord] = Field(default_factory=list)
    card_ids: list[str] = Field(default_factory=list)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    used: BudgetUsed = Field(default_factory=BudgetUsed)
    llm_calls_by_agent: dict[str, int] = Field(default_factory=dict)


class ValidatorViolation(BaseModel):
    rule: str
    detail: str
    offending: str = ""


class CommitResult(BaseModel):
    ok: bool
    card: Optional[Card] = None
    violations: list[ValidatorViolation] = Field(default_factory=list)
