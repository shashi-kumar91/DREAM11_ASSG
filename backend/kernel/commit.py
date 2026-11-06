"""The commit gate. No card reaches the fan except through `commit()`.

Rules enforced here, in code:
1. Every card must cite evidence that exists and that the fan has already reached.
2. Every digit in the card must appear in that cited evidence (or in a deterministic
   tally). A number the model invented is a hard rejection, not a warning.
3. The truth label is assigned by code from the deterministic verdict, never copied from
   the model's draft.
4. Prompt-injection phrasing inside creator speech or model output is flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .clamp import ClampedFeed
from .schemas import (Card, CardDraft, CommitResult, InsightType, TruthLabel,
                      ValidatorViolation, Verdict, VerdictKind, new_id)

NUM_RE = re.compile(r"\d+(?:[./:]\d+)*")
INJECTION_PATTERNS = [
    r"ignore (?:all |your )?(?:previous |prior )?(?:rules|instructions)",
    r"disregard (?:the )?(?:system|safety) prompt",
    r"you are now (?:a|an)",
    r"reveal (?:your )?(?:system )?prompt",
    r"override (?:the )?(?:guardrail|validator)",
]


def numbers_in(text: str) -> set[str]:
    """Numeric tokens plus their dot/colon/slash components."""
    out: set[str] = set()
    for raw in NUM_RE.findall(text or ""):
        out.add(raw)
        for part in re.split(r"[./:]", raw):
            if part:
                out.add(part)
    return out


@dataclass
class Evidence:
    """Deterministic sources a card is allowed to cite, and the numbers they contain."""

    texts: dict[str, str] = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)
    tally_ids: set[str] = field(default_factory=set)
    verdict_ids: set[str] = field(default_factory=set)

    def add(self, source_id: str, text: str) -> None:
        self.texts[source_id] = (self.texts.get(source_id, "") + " " + text).strip()

    def numbers(self) -> set[str]:
        out: set[str] = set()
        for text in self.texts.values():
            out |= numbers_in(text)
        return out

    def known(self, ids: list[str], kind: str) -> list[str]:
        store = {"event": self.event_ids, "tally": self.tally_ids, "verdict": self.verdict_ids}[kind]
        if kind == "event":
            return [i for i in ids if i in store]
        return [i for i in ids if i in store or i in self.texts]


class LabelRule:
    """Code-side truth labelling. Packs supply the mapping; the model never picks a badge."""

    def __init__(self, fn: Callable[[CardDraft, dict], tuple[TruthLabel, str]]):
        self.fn = fn

    def label(self, draft: CardDraft, scratch: dict) -> tuple[TruthLabel, str]:
        return self.fn(draft, scratch)


def commit(
    draft: CardDraft,
    *,
    episode_id: str,
    evidence: Evidence,
    feed: ClampedFeed,
    label_rule: LabelRule,
    scratch: dict,
    attribution: str,
    timestamp_label: str = "",
    source_badges: Optional[dict[str, str]] = None,
    template_used: Optional[str] = None,
) -> CommitResult:
    v: list[ValidatorViolation] = []
    body_all = f"{draft.headline} {draft.body}"

    cited = (draft.cited_utterance_ids + draft.cited_event_ids
             + draft.cited_verdict_ids + draft.cited_tally_ids)
    if not cited:
        v.append(ValidatorViolation(rule="evidence_required",
                                     detail="card cites no utterance, event, verdict or tally"))

    unknown = [i for i in draft.cited_utterance_ids if i not in evidence.texts]
    if unknown:
        v.append(ValidatorViolation(rule="unknown_utterance", offending=",".join(unknown),
                                     detail="cited utterance id not in this episode's evidence"))
    bad_events = [i for i in draft.cited_event_ids if i not in evidence.event_ids]
    if bad_events:
        v.append(ValidatorViolation(rule="uncited_event", offending=",".join(bad_events),
                                     detail="event id was not produced by a feed tool this episode"))

    # future-event / spoiler check, independent of the clamp's own guard
    for eid in draft.cited_event_ids:
        ev = feed.get(eid)
        if ev is None:
            v.append(ValidatorViolation(rule="event_beyond_resume_cursor", offending=eid,
                                         detail="event is at or beyond the fan's playback boundary"))

    allowed = evidence.numbers()
    stray = sorted(n for n in numbers_in(body_all) if n not in allowed)
    if stray:
        v.append(ValidatorViolation(rule="unlicensed_number", offending=" ".join(stray),
                                     detail="digit does not appear in cited evidence; LLM may not author numbers"))

    for pat in INJECTION_PATTERNS:
        if re.search(pat, body_all, flags=re.I):
            v.append(ValidatorViolation(rule="injection_phrasing", offending=pat,
                                         detail="card text contains instruction-override phrasing"))

    if not draft.headline.strip() or not draft.body.strip():
        v.append(ValidatorViolation(rule="empty_card", detail="headline or body empty"))
    if len(draft.body) > 700:
        v.append(ValidatorViolation(rule="over_length", detail=f"body {len(draft.body)} > 700"))

    if v:
        return CommitResult(ok=False, violations=v)

    truth_label, template = label_rule.label(draft, scratch)
    card = Card(
        id=new_id("card"),
        episode_id=episode_id,
        **draft.model_dump(exclude={"salience"}),
        salience=draft.salience,
        truth_label=truth_label,
        attribution=attribution,
        timestamp_label=timestamp_label,
        source_badges=source_badges or {},
        template_used=template,
        numbers=sorted(numbers_in(body_all) & allowed),
    )
    return CommitResult(ok=True, card=card)


def verdict_label(verdict: Optional[Verdict]) -> Optional[TruthLabel]:
    if verdict is None:
        return None
    return {
        VerdictKind.CONFIRMED: TruthLabel.VERIFIED,
        VerdictKind.CONFIRMED_LAG: TruthLabel.VERIFIED,
        VerdictKind.CONFLICT: TruthLabel.CONFLICT,
        VerdictKind.UNVERIFIABLE: TruthLabel.UNVERIFIABLE,
    }[verdict.kind]
