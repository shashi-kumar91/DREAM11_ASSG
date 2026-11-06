"""Specialist role definitions for Pack B."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ...kernel.orchestrator import AgentSpec
from . import prompts


def _as_list(v: Any) -> Any:
    """Models return null, one object, or a JSON string where a list belongs."""
    if v is None:
        return []
    if isinstance(v, (str, bytes)):
        return []
    if isinstance(v, dict):
        return [v]
    return v


def _as_text(v: Any) -> Any:
    """A yes/no answer to "what did this tool find?" is text, not a schema violation."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return str(v)
    return v


class Hypothesis(BaseModel):
    candidate: str = ""
    why: str = ""
    risk: str = ""


class ReaderOut(BaseModel):
    primary_candidate: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    what_would_settle_it: str = ""
    summary: str = ""

    _fix = field_validator("hypotheses", mode="before")(_as_list)


class SourceCheck(BaseModel):
    tool: str = ""
    found: str = ""
    detail: str = ""

    # A model answering "did this tool find it?" with `true` is answering the question it was
    # asked, not violating a contract - so the bool is read as text instead of killing the turn.
    _coerce = field_validator("tool", "found", "detail", mode="before")(_as_text)


class InvestigatorOut(BaseModel):
    sources: list[SourceCheck] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    coverage_ok: bool = True

    _fix_sources = field_validator("sources", mode="before")(_as_list)
    _fix_disc = field_validator("disagreements", mode="before")(_as_list)


class QuestionerOut(BaseModel):
    ask: bool = False
    question: str = ""
    options: list[str] = Field(default_factory=list)
    expected_info_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    why: str = ""

    _fix = field_validator("options", mode="before")(_as_list)


class StrategistOut(BaseModel):
    modality: str = Field(default="receipt", max_length=20)
    offer_clip_event: str = Field(default="", max_length=40)
    why_it_matters: str = Field(default="", max_length=320)
    ordering: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=200)


class CriticOut(BaseModel):
    approve: bool = True
    reason: str = Field(default="", max_length=220)
    must_fix: str = Field(default="", max_length=220)


TOOLSETS = {
    "reader": {"get_playback_telemetry"},
    "investigator": {"query_event_feed", "inspect_frame", "search_commentary"},
    "questioner": {"get_playback_telemetry", "query_event_feed"},
    "strategist": {"query_event_feed", "offer_clip"},
    "critic": {"query_event_feed", "inspect_frame", "search_commentary"},
}

CAPSETS = {
    "reader": {"telemetry"},
    "investigator": {"past_events", "video_evidence"},
    "questioner": {"telemetry", "past_events"},
    "strategist": {"past_events", "permission"},
    "critic": {"past_events", "video_evidence"},
}


def build_agents() -> dict[str, AgentSpec]:
    specs = [
        AgentSpec("reader", "read the playback log and name the boundary hypotheses",
                  prompts.READER, ReaderOut, TOOLSETS["reader"], CAPSETS["reader"]),
        AgentSpec("investigator", "cross-check the missed window against frames and commentary",
                  prompts.INVESTIGATOR, InvestigatorOut, TOOLSETS["investigator"],
                  CAPSETS["investigator"]),
        AgentSpec("questioner", "decide whether asking the fan would change the receipt",
                  prompts.QUESTIONER, QuestionerOut, TOOLSETS["questioner"],
                  CAPSETS["questioner"], max_inner_calls=1),
        AgentSpec("strategist", "choose the recovery modality and the one causal sentence",
                  prompts.STRATEGIST, StrategistOut, TOOLSETS["strategist"],
                  CAPSETS["strategist"], max_inner_calls=2),
        AgentSpec("critic", "reject a receipt that would spoil or mislead the fan",
                  prompts.GAP_CRITIC, CriticOut, TOOLSETS["critic"], CAPSETS["critic"],
                  max_inner_calls=1),
    ]
    return {s.role: s for s in specs}
