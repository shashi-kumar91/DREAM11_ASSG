"""Tier-0 triage: cheap, deterministic filters that run before any LLM call.

This is the cost valve (roughly two-thirds of live speech is greeting, noise or restatement)
and it is deliberately *not* intelligent - a small model cannot be trusted to decide what
is worthless, but a rule can detect an obviously unusable transcript.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ...kernel.schemas import Utterance

NUMBER_RE = re.compile(r"\d+(?:[./]\d+)?")

CRICKET_TERMS = [
    "yorker", "reverse sweep", "death overs", "medium length", "ball tracking", "review",
    "DRS", "sixer", "chhaka", "char", "four", "boundary", "wicket", "out", "score",
    "over", "field", "spin", "pace", "run", "dot", "slower", "bouncer", "length",
    "millimeter", "catch", "run out", "bowler", "batsman", "strike", "chase", "required",
]
GREETING_MARKERS = ["comments mein", "batao kaun", "kaun kaun dekh", "doston", "live with",
                    "subscribe", "share karo"]
FILLER_ONLY = re.compile(r"^(aur woh yaar| matlab| haan haan| toh| ok ok| chal)\b[\W]*$", re.I)
# Explicit self-reference to something already said. Cheap to detect, and it is the one
# restatement signal that does not depend on the wording overlapping by accident.
RESTATE_MARKERS = ["dobara", "dobaara", "wahi dobara", "phir se", "same again",
                   "the same thing", "again bhai"]

STOPWORDS = {"hai", "tha", "thi", "ho", "ka", "ki", "ke", "kar", "raha", "rahi", "gaya",
             "gaye", "liye", "par", "mein", "main", "aur", "yeh", "ye", "wo", "woh", "ek",
             "to", "toh", "na", "nae", "ab", "hi", "bhi", "as", "the", "a", "is", "it",
             "of", "and", "so", "my", "your"}


@dataclass
class TriageOut:
    skip: bool
    reason: str
    matched_prior_id: str = ""
    distinctive: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.distinctive = self.distinctive or []


def keyterms_in(text: str) -> list[str]:
    low = (text or "").lower()
    return [t for t in CRICKET_TERMS if t.lower() in low]


def content_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9/]+", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def numeric_tokens(text: str) -> set[str]:
    """Scores, paces and counts. A changed number is never a restatement - it is either a
    fresh claim or a contradiction, and both are the most valuable things to check."""
    return set(NUMBER_RE.findall((text or "").lower()))


def triage(u: Utterance, recent: list[Utterance], conf_floor: float = 0.60) -> TriageOut:
    text = (u.text or "").strip()
    if not text:
        return TriageOut(True, "empty_transcript")
    if u.asr_confidence < conf_floor:
        return TriageOut(True, f"low_asr_confidence:{u.asr_confidence:.2f}")
    toks = content_tokens(text)
    if len(text.split()) < 4:
        return TriageOut(True, "too_short")
    if FILLER_ONLY.match(text):
        return TriageOut(True, "filler_only")

    distinctive = keyterms_in(text)
    low = text.lower()
    if not distinctive and any(g in low for g in GREETING_MARKERS):
        return TriageOut(True, "greeting_no_content")

    # restatement: this utterance's distinctive terms are already covered by a recent one
    if distinctive:
        nums = numeric_tokens(text)
        for prior in reversed(recent[-8:]):
            p_dist = set(keyterms_in(prior.text))
            if not p_dist:
                continue
            if set(distinctive) <= p_dist:
                if nums != numeric_tokens(prior.text):
                    continue
                says_again = any(m in low for m in RESTATE_MARKERS)
                few_new_words = len(toks - content_tokens(prior.text)) <= 4
                if says_again or few_new_words:
                    return TriageOut(True, f"repeats:{prior.id}", matched_prior_id=prior.id,
                                     distinctive=distinctive)
    return TriageOut(False, "worth_a_model_call", distinctive=distinctive)
