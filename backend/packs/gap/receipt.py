"""Gap receipt templates.

The fan-facing wording of a catch-up receipt is the part of this product where a wrong
number is indistinguishable from a lie, so the digits never come from the model: this module
writes them from the cited feed events and the deterministic gap summary. The agent chooses
the modality, the ordering and one "why it matters" sentence - and that sentence has to
carry event citations, which `guardrails` checks and this module strips once they are valid.
"""
from __future__ import annotations

import re

from ...kernel.schemas import CardDraft, MatchEvent

MARK_RE = re.compile(r"\[(evt_[0-9]{2,6})\]")


def delta_text(rate: dict) -> str:
    """One formatting site for the one digit the receipt states as a difference.

    The body prints it, so the tally must contain it too - otherwise the commit gate calls
    the pack's own arithmetic an unlicensed number.
    """
    d = rate["delta"]
    return f"+{d}" if d >= 0 else f"{d}"


def gap_summary(boundary: MatchEvent, resume: MatchEvent, missed: list[MatchEvent],
                rate: dict) -> str:
    """One line of code-computed arithmetic. The receipt cites this as its tally."""
    balls = len(missed)
    wickets = len([e for e in missed if e.wicket])
    material = len([e for e in missed if e.wicket or e.runs >= 4])
    plural = "" if wickets == 1 else "s"
    return (f"gap {boundary.over_label} -> {resume.over_label}: you missed {balls} balls, "
            f"{material} of them key, {wickets} wicket{plural} in that window; score "
            f"{boundary.score} -> {resume.score} by the time you came back; required rate "
            f"{rate['before_rrr']} -> {rate['after_rrr']} ({delta_text(rate)}); "
            f"{rate['runs_needed']} off {rate['balls_left']} balls")


def event_line(prev: MatchEvent, e: MatchEvent, text_limit: int = 52) -> str:
    arrow = f" ({prev.score} -> {e.score})" if e.score != prev.score else ""
    mark = "WICKET" if e.wicket else ("BOUNDARY" if e.runs >= 4 else "")
    return f"- {e.over_label} {mark + ': ' if mark else ''}{clip_text(e.text, text_limit)}{arrow}".rstrip()


def clip_text(text: str, limit: int) -> str:
    """Commentary is flavour; the over and the score are the receipt.

    A fifteen-ball window cannot be printed in full on a phone card, so the prose is cut at a
    word boundary - never the numbers, which live in the arrow outside the clipped span.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return f"{cut}..."


def strip_marks(text: str) -> str:
    return re.sub(r"\s+", " ", MARK_RE.sub("", text or "")).strip()


CARD_LIMIT = 690          # CardDraft caps the body at 700; leave room for the joins
MAX_BULLETS = 5

SHORT_OTHERS = ("The window also held balls that were not key actions - they are counted "
                "in the summary above.")
OVERFLOW = "plus the other key actions in this window - all counted in the summary above."


def assemble(parts: list[tuple[bool, str]]) -> str:
    """Join the card's lines, letting the optional ones go from the bottom up.

    A rejected commit means the fan gets no receipt at all, so the prose is expendable and the
    arithmetic is not: flavour lines are dropped before a key-action line ever is, and the
    digits only ever live in the required lines.
    """
    optional_idx = [i for i, (required, _) in enumerate(parts) if not required]

    def render(drop: int) -> str:
        gone = set(optional_idx[len(optional_idx) - drop:]) if drop else set()
        return "\n".join(p for i, (_, p) in enumerate(parts) if i not in gone)

    for drop in range(len(optional_idx) + 1):
        body = render(drop)
        if len(body) <= CARD_LIMIT:
            return body
    return render(len(optional_idx))


def key_priority(key: list[MatchEvent], limit: int) -> list[MatchEvent]:
    """The handful of balls a phone card can carry, wickets before boundaries, then over order.

    Selection is by consequence, printing is in feed order, so a collapsed window still reads
    forwards in time.
    """
    ranked = sorted(key, key=lambda e: (0 if e.wicket else 1 if e.runs >= 4 else 2,
                                        e.balls_faced))
    chosen = ranked[:limit]
    return [e for e in key if e.id in {c.id for c in chosen}]


def bullet_block(key: list[MatchEvent], boundary: MatchEvent, limit: int) -> str:
    chosen = key_priority(key, limit)
    lines, prev = [], boundary
    for e in chosen:
        lines.append(event_line(prev, e))
        prev = e
    if limit < len(key):
        lines.append(OVERFLOW)
    return "\n".join(lines)


def receipt(key: list[MatchEvent], all_missed: list[MatchEvent], boundary: MatchEvent,
            resume: MatchEvent, rate: dict, tally_id: str, why: str = "",
            clip: dict | None = None, frame_checks: list[str] | None = None,
            fan_saw: str = "", scope_note: str = "",
            other_labels: list[str] | None = None) -> CardDraft:
    """The core artifact: what happened, in the fan's own playback terms, and nothing after it.

    `key` is what gets a line of its own; `all_missed` is what the card is allowed to cite.
    A 15-ball window cannot be printed as 15 bullets on a phone, so the template is tried from
    richest to leanest and the first version that fits the card wins: the spare balls collapse
    from a list of overs to a digit-free sentence, the model's prose falls away, and the key
    lines give up their tail. Every digit printed here is in `all_missed` or the cited tally.
    """
    first, last = all_missed[0], all_missed[-1]
    # The span ends at the last ball actually missed. Ending it at the resume ball printed
    # "you missed 16.3-16.6" on a card whose bullets stopped at 16.5.
    span = first.over_label if first is last else f"{first.over_label}-{last.over_label}"
    delta = delta_text(rate)
    checks = ", ".join(frame_checks) if frame_checks else "no frame captured in this window"
    must = [
        (f"Score {boundary.score} -> {resume.score}. Required rate "
         f"{rate['before_rrr']} -> {rate['after_rrr']} ({delta}); "
         f"{rate['runs_needed']} needed off {rate['balls_left']} balls."),
        f"Scoreboard frames read from the pixels: {checks}.",
        (f"Clip offered: {clip['duration_s']}s, about {clip['bytes_estimate_kb']} KB, "
         f"loads only if you tap it.") if clip else "No clip loaded - this receipt is text.",
        f"Nothing after {resume.over_label} is included.",
    ]
    # The model's sentence is flavour; the scope note is the answer to the question the fan
    # just answered, so it stays and a key-action line is what gives way.
    prose = [(False, f"Why it matters: {strip_marks(why)}" if why else ""),
             (True, scope_note)]
    others = [f"Other balls in the window: {', '.join(other_labels)}", SHORT_OTHERS, ""] \
        if other_labels else [""]
    bodies: list[str] = []
    for n in range(min(MAX_BULLETS, len(key)) or 1, 0, -1):
        for other in others:
            score = [must[0]] + ([other] if other else [])
            parts = [(True, bullet_block(key, boundary, n)),
                     *[(True, line) for line in score],
                     *prose,
                     *[(True, line) for line in must[1:]]]
            bodies.append(assemble([(required, line) for required, line in parts if line]))
    body = next((b for b in bodies if len(b) <= CARD_LIMIT), bodies[-1])
    return CardDraft(
        kind="receipt", headline=f"You missed {span}: {boundary.score} -> {resume.score}",
        body=body, salience=1.0, cited_event_ids=[e.id for e in all_missed],
        cited_tally_ids=[tally_id],
        tags=["receipt", span, "clip" if clip else "text_only",
              *({"fan_set_boundary"} if fan_saw else set()),
              *({"scope_key_only"} if scope_note else set())],
    )


def no_op_summary(boundary: MatchEvent, resume: MatchEvent, missed: list[MatchEvent]) -> str:
    """The abstention also has to show its arithmetic, or its one digit is unlicensed."""
    return (f"gap {boundary.over_label} -> {resume.over_label}: {len(missed)} balls missed, "
            f"{len([e for e in missed if e.wicket])} wickets, "
            f"{len([e for e in missed if e.wicket or e.runs >= 4])} key actions, "
            f"score {boundary.score} -> {resume.score} by the time you came back")


def no_op(boundary: MatchEvent, resume: MatchEvent, missed: list[MatchEvent],
          tally_id: str = "") -> CardDraft:
    """Abstention, as an artifact. Proving the agent does not manufacture drama is a result."""
    balls = len(missed)
    cited = [boundary.id] + [e.id for e in missed]
    if resume.id not in cited:
        cited.append(resume.id)
    return CardDraft(
        kind="noop",
        headline=f"Nothing key missed ({resume.score} unchanged by any wicket or boundary)",
        body=(f"{balls} balls passed while you were away and none of them was a wicket, a "
              f"four or a six: {', '.join(e.over_label for e in missed)}. Match state at "
              f"{resume.over_label} is {resume.score}. No clip offered, no recap - there was "
              f"nothing worth your attention."),
        salience=0.25, cited_event_ids=cited, cited_tally_ids=[tally_id] if tally_id else [],
        tags=["noop", f"{boundary.over_label}-{resume.over_label}"],
    )


def jump_to_live(resume: MatchEvent) -> CardDraft:
    return CardDraft(
        kind="status", headline="Back to live",
        body=f"Live position is {resume.over_label}, {resume.score}. Nothing later than that "
             "was shown to you.",
        salience=0.4, cited_event_ids=[resume.id], tags=["jump_to_live"],
    )
