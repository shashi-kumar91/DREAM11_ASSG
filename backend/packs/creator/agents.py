"""Specialist role definitions for Pack A."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ...kernel.orchestrator import AgentSpec
from ...kernel.schemas import InsightType
from . import prompts
from .predictions import PredictionExtraction
from .verify import StructuredClaim


def _tolerate_loose_payload(v: Any) -> Any:
    """Normalise what models actually emit for a nested object field.

    Two shapes arrived from live Gemini turns: `null` (meaning "no claim here"), and the
    whole utterance pasted in as a bare string. A default_factory only fires when the key
    is absent, so both would otherwise cost a retry and, at 20 requests/day of free-tier
    quota, a retry is not free.
    """
    if v is None:
        return {}
    if isinstance(v, str):
        return {"raw_phrase": v[:200]}
    return v


def _loose_claim(v: Any) -> Any:
    return _tolerate_loose_payload(v)


def _loose_prediction(v: Any) -> Any:
    if isinstance(v, str):
        return {"text": v[:300]}
    return _tolerate_loose_payload(v)


class Mention(BaseModel):
    phrase: str = Field(default="", max_length=60)
    kind: str = Field(default="unresolved", max_length=24)
    resolved: str = Field(default="", max_length=60)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PerceiverOut(BaseModel):
    mentions: list[Mention] = Field(default_factory=list)
    ambiguity: str = ""
    summary: str = ""


class AnalystOut(BaseModel):
    insight_type: InsightType = InsightType.filler
    is_new_information: bool = True
    claim: StructuredClaim = Field(default_factory=StructuredClaim)
    prediction: PredictionExtraction = Field(default_factory=PredictionExtraction)
    headline: str = Field(default="", max_length=90)
    body: str = Field(default="", max_length=500)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=240)

    _fix_claim = field_validator("claim", mode="before")(_loose_claim)
    _fix_prediction = field_validator("prediction", mode="before")(_loose_prediction)


class VerifierAgentOut(BaseModel):
    agrees: bool = True
    concerns: list[str] = Field(default_factory=list)
    note_for_card: str = Field(default="", max_length=200)


class StrategistOut(BaseModel):
    mode: str = Field(default="card", max_length=20)
    reason: str = Field(default="", max_length=200)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)


class CriticOut(BaseModel):
    approve: bool = True
    reason: str = Field(default="", max_length=220)
    must_fix: str = Field(default="", max_length=220)


TOOLSETS = {
    "perceiver": {"get_match_state", "resolve_reference", "recall_utterance"},
    "analyst": {"get_match_state", "query_event_feed", "search_transcript"},
    "verifier": {"verify_claim", "query_event_feed", "get_match_state", "recall_utterance"},
    "strategist": {"get_match_state"},
    "critic": {"recall_utterance", "query_event_feed"},
}

CAPSETS = {
    "perceiver": {"match_state", "speech_memory"},
    "analyst": {"match_state", "past_events", "speech_memory"},
    "verifier": {"match_state", "past_events", "speech_memory", "verify"},
    "strategist": {"match_state", "surface"},
    "critic": {"speech_memory", "past_events"},
}


def build_agents() -> dict[str, AgentSpec]:
    specs = [
        AgentSpec("perceiver", "resolve references and entities in the creator's words",
                  prompts.PERCEIVER, PerceiverOut, TOOLSETS["perceiver"], CAPSETS["perceiver"]),
        AgentSpec("analyst", "classify the utterance and draft the insight",
                  prompts.ANALYST, AnalystOut, TOOLSETS["analyst"], CAPSETS["analyst"]),
        AgentSpec("verifier", "adversarially test the deterministic verdict",
                  prompts.VERIFIER_AGENT, VerifierAgentOut, TOOLSETS["verifier"],
                  CAPSETS["verifier"]),
        AgentSpec("strategist", "decide whether this is worth the fan's attention right now",
                  prompts.STRATEGIST, StrategistOut, TOOLSETS["strategist"],
                  CAPSETS["strategist"], max_inner_calls=1),
        AgentSpec("critic", "reject cards that would mislead a fan", prompts.CRITIC,
                  CriticOut, TOOLSETS["critic"], CAPSETS["critic"], max_inner_calls=1),
    ]
    return {s.role: s for s in specs}
