"""Deterministic card text for the cards where a wrong number would be a lie.

The LLM still decides *whether* these happen and *what* the creator meant; it does not get
to write the wording of a verified, conflict or settlement card. That is the whole point of
the propose -> verify -> commit split.
"""
from __future__ import annotations

from ...kernel.schemas import CardDraft, Prediction, Utterance, Verdict
from .predictions import Settlement

Q = "\u201c"


def conflict(u: Utterance, verdict: Verdict, over: str) -> CardDraft:
    lag = (f" It was last correct at {verdict.matched_over}, "
           f"{verdict.age_balls} balls earlier.") if verdict.matched_over else ""
    return CardDraft(
        kind="conflict", headline=f"Creator said {Q}{verdict.claimed}{Q} - feed says {verdict.observed}",
        body=(f'Creator at {fmt(u.t_start)}: "{trim(u.text)}". '
              f"Match feed at {over}: {verdict.observed}.{lag} "
              f"Label: creator claim conflicts with live feed; the creator's words are kept as said."),
        salience=1.0, cited_utterance_ids=[u.id], cited_verdict_ids=[verdict.id],
        cited_event_ids=[verdict.matched_event_id] if verdict.matched_event_id else [],
        tags=["conflict", "verification"],
    )


def verified(u: Utterance, verdict: Verdict, over: str) -> CardDraft:
    return CardDraft(
        kind="insight", headline=f"Checked against the feed: {verdict.observed}",
        body=(f'Creator at {fmt(u.t_start)}: "{trim(u.text)}". '
              f"Feed at {over} agrees ({verdict.detail})."),
        salience=0.75, cited_utterance_ids=[u.id], cited_verdict_ids=[verdict.id],
        cited_event_ids=[verdict.matched_event_id] if verdict.matched_event_id else [],
        tags=["verified", "fact"],
    )


def unverifiable(u: Utterance, verdict: Verdict) -> CardDraft:
    return CardDraft(
        kind="insight", headline="Creator claim, not checkable from the feed",
        body=f'Creator at {fmt(u.t_start)}: "{trim(u.text)}". {verdict.detail}.',
        salience=0.5, cited_utterance_ids=[u.id], cited_verdict_ids=[verdict.id],
        tags=["unverified"],
    )


def prediction_card(u: Utterance, p: Prediction, striker_hint: str = "") -> CardDraft:
    scope = ", ".join(x for x in [p.bowler, f"over {p.over}" if p.over else ""] if x)
    return CardDraft(
        kind="prediction",
        headline=f"Creator predicts: {p.text[:70]}",
        body=(f'Creator at {fmt(u.t_start)}: "{trim(u.text)}". '
              f"Tracked against the feed for {scope}. "
              f"This is a prediction, not a match fact."),
        salience=0.8, cited_utterance_ids=[u.id], tags=["prediction", scope or "unset"],
    )


def settlement_card(u: Utterance, p: Prediction, s: Settlement) -> CardDraft:
    word = {"hit": "HIT", "miss": "MISSED", "void": "void"}[s.status]
    return CardDraft(
        kind="settlement",
        headline=f"Prediction {word}: {s.tally_text}",
        body=(f'Creator predicted at {fmt(u.t_start)}: "{trim(u.text)}". '
              f"Observed in the feed: {s.tally_text}. Verdict: {s.status} ({s.why})."),
        salience=1.0, cited_utterance_ids=[u.id], cited_tally_ids=[s.tally_id],
        cited_event_ids=s.event_ids, tags=["settlement", s.status],
    )


def quote_card(u: Utterance, why: str = "") -> CardDraft:
    return CardDraft(
        kind="insight", headline=f"Creator line worth keeping: {Q}{trim(u.text, 58)}{Q}",
        body=f'Spoken at {fmt(u.t_start)}. Kept as the creator\'s own words, not as fact. '
             + (why[:120] if why else ""),
        salience=0.6, cited_utterance_ids=[u.id], tags=["quote"],
    )


def query_answer(query: str, hits: list[tuple[Utterance, str]]) -> CardDraft:
    if not hits:
        return CardDraft(
            kind="query_answer", headline="The creator hasn't said anything about that",
            body=f'No attributed transcript segment matches "{trim(query, 60)}". '
                 "Nothing was invented to fill the gap.",
            salience=0.6, tags=["query", "abstain"],
        )
    lines = [f'- {fmt(u.t_start)}: "{trim(snip)}"' for u, snip in hits[:4]]
    ids = [u.id for u, _ in hits[:4]]
    return CardDraft(
        kind="query_answer",
        headline="From the creator's own words",
        body=f'You asked: "{trim(query, 60)}".\n' + "\n".join(lines),
        salience=0.9, cited_utterance_ids=ids, tags=["query"],
    )


def fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def trim(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "\u2026"
