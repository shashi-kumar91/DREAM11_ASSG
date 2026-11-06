"""Pack B hard guardrails: the spoiler boundary and cited causality.

Two failures a catch-up product cannot ship with: telling the fan about a ball they have not
seen yet, and asserting a causal link ("two wickets in four balls raised the rate") that no
evidence supports. Both are checked on the way out, in code, whatever the model proposed.
"""
from __future__ import annotations

import re

from ...kernel.schemas import ValidatorViolation

MARK_RE = re.compile(r"\[(evt_[0-9]{2,6})\]")


def validate_receipt(cited_event_ids: list[str], resume_cursor: str, feed,
                     why: str = "", end_over: str = "") -> list[ValidatorViolation]:
    v: list[ValidatorViolation] = []
    hi = feed.index.get(resume_cursor)
    if hi is None:
        v.append(ValidatorViolation(rule="unknown_resume_cursor", offending=resume_cursor,
                                    detail="the resume cursor is not an event in this feed"))
        return v

    for eid in cited_event_ids:
        idx = feed.index.get(eid)
        if idx is None:
            v.append(ValidatorViolation(rule="unknown_event", offending=eid,
                                        detail="receipt cites an event that does not exist"))
        elif idx > hi:
            v.append(ValidatorViolation(rule="spoiler_beyond_resume_cursor", offending=eid,
                                        detail=f"{feed.by_id[eid].over_label} is after the "
                                               f"fan's position {feed.by_id[resume_cursor].over_label}"))
    if end_over:
        end_ev = feed.find_by_over(end_over)
        if end_ev is None or feed.index[end_ev.id] > hi:
            v.append(ValidatorViolation(rule="spoiler_end_over", offending=end_over,
                                        detail="receipt window ends beyond the resume cursor"))

    for sentence in _causal_sentences(why):
        marks = set(MARK_RE.findall(sentence))
        if not marks:
            v.append(ValidatorViolation(rule="uncited_causality", offending=sentence[:80],
                                        detail="a causal sentence cites no event id"))
        elif not marks <= set(cited_event_ids):
            v.append(ValidatorViolation(rule="cites_uncited_event",
                                        offending=" ".join(sorted(marks - set(cited_event_ids))),
                                        detail="causal sentence cites an event the receipt "
                                               "does not carry"))
    return v


def _causal_sentences(why: str) -> list[str]:
    text = (why or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def clip_is_offerable(frame_check: dict) -> tuple[bool, str]:
    """A clip may only be offered when its thumbnail frame agrees with the feed.

    The frame value is read from the pixels by `ocr.read_frame`, so this comparison can
    genuinely fail - which is what the injected-fault fixture is for.
    """
    if not frame_check.get("found"):
        return False, "no scoreboard frame in this window"
    if not frame_check.get("readable"):
        return False, "frame unreadable by the scoreboard reader"
    if not frame_check.get("agrees"):
        read = frame_check.get("read_from_pixels") or {}
        return False, (f"frame shows {read.get('score')} but the feed says "
                       f"{frame_check.get('feed_score')}")
    return True, "frame agrees with the feed"
