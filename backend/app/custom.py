"""Scenarios the fixtures do not contain.

The three recorded situations are a demo, and a demo is not a product. Everything below builds a
*real* scenario from what a user asked for, and then hands it to the same kernel, the same clamp
and the same commit gate the recorded ones use - so a case nobody scripted is answered by the
same code path, not by a fallback.

Two shapes:

  * `gap_custom`   - "I was on this ball, then <this> happened": the boundary is re-estimated by
                     `boundary.estimate` from synthesised heartbeats, so the agent reasons over a
                     window it has never seen before.
  * `creator_custom` - "the creator just said this": the text is the ASR result, and triage, the
                     claim verifier, the ledger and the commit gate all run against the real feed.

Nothing here invents evidence. The feed, the frames and the ball-by-ball record are the same
fixture objects; only the fan-side situation is new.
"""
from __future__ import annotations

from typing import Optional

from ..packs.gap.domain import GapFixtures, GapScenario, HeartbeatRow

# What can happen to a fan, in the vocabulary the boundary rules already understand.
INTERRUPTIONS = {
    "ad":       {"kind": "ad_start", "label": "an ad covered the screen"},
    "stall":    {"kind": "stall",    "label": "the stream stalled"},
    "app":      {"kind": "stall",    "label": "you switched apps and came back"},
    "late":     {"kind": "join",     "label": "you joined late"},
}


def _feed(fx: GapFixtures):
    return fx.feed


def gap_custom(fx: GapFixtures, resume_event_id: str, interruption: str = "ad",
               seen_before: int = 3) -> GapScenario:
    """Build a scenario for an arbitrary resume ball.

    `seen_before` is how many balls before the resume point the player had actually painted.
    Three is the default because a one-ball gap is a trick question: the only ball between the
    last painted one and the resume point is the resume ball itself, which the clamp may never
    reveal, so the honest answer is "you missed nothing" every time. With three, the window the
    fan actually missed is real, and the receipt has something true to say.
    With 0 the fan never saw a ball (a late join), and `boundary.estimate` correctly reports that
    only the fan can scope the window - it is not special-cased here.
    """
    feed = _feed(fx)
    if resume_event_id not in feed.index:
        raise ValueError(f"unknown ball {resume_event_id!r}")
    idx = feed.index[resume_event_id]
    resume = feed.events[idx]
    spec = INTERRUPTIONS.get(interruption, INTERRUPTIONS["ad"])

    rows: list[HeartbeatRow] = []
    last_idx = max(0, idx - max(1, seen_before))       # the last ball the phone actually painted
    if seen_before > 0 and spec["kind"] != "join":
        for j in range(max(0, last_idx - 1), last_idx + 1):
            e = feed.events[j]
            rows.append(HeartbeatRow(t=round(max(0.0, e.t_wall - 0.4), 2), event_id=e.id,
                                     kind="play", note="rendered"))
        # The interruption begins right after the last painted ball, and the fan comes back
        # `seen_before` balls later. Painting right up to the ball before the resume point made
        # every custom gap one ball long - and the one ball left is the resume ball, which the
        # clamp can never show, so the honest answer was "you missed nothing" every time.
        rows.append(HeartbeatRow(t=round(max(0.0, feed.events[last_idx].t_wall + 0.2), 2),
                                 event_id=feed.events[last_idx].id, kind=spec["kind"],
                                 note=spec["label"]))
    else:
        rows.append(HeartbeatRow(t=round(max(0.0, resume.t_wall - 1.0), 2), event_id=resume.id,
                                 kind="join", note=spec["label"]))
    rows.append(HeartbeatRow(t=round(resume.t_wall, 2), event_id=resume.id, kind="resume",
                             note="player restarted"))

    return GapScenario(
        id=f"custom_{resume_event_id}",
        name=f"from {resume.over_label}",
        interruption_type={"ad": "ad", "stall": "buffer", "app": "app_switch",
                           "late": "late_join"}.get(interruption, "unknown"),
        resume_t=resume.t_wall, resume_cursor=resume.id,
        heartbeats=rows,
        blurb=f"Your player stopped painting after the ball before {resume.over_label} "
              f"({spec['label']}). You came back at {resume.over_label}.",
        # The auto-fan answers any question with the most likely candidate, which is what a fan
        # who is unsure would do; it is a stand-in for a person, not a chosen boundary.
        # The fan answers with the ball the *phone* last painted, which is the whole premise of
        # this scenario. Answering with `idx - 1` claimed to have seen the ball right before the
        # resume point, which left an empty window ("you missed nothing") for every custom case.
        # The fan answers with the ball their own log says was painted last, which is the whole
        # premise of this scenario; the answer then maps onto a boundary candidate by label.
        # A late join is asked a different question (how much of the innings?), so it answers
        # that one instead of naming a ball it never saw.
        fan_answers={"?": (f"Yes, I saw ball {feed.events[last_idx].over_label}"
                           if spec["kind"] != "join"
                           else f"Everything from {feed.events[0].over_label}")},
    )


def creator_custom(text: str, t_start: float = 60.0, duration: float = 8.0) -> dict:
    """A transcript row for text the user typed instead of speech we transcribed."""
    clean = " ".join((text or "").split())[:400]
    if not clean:
        raise ValueError("no text given")
    return {"id": "custom_1", "t_start": t_start, "t_end": t_start + duration,
            "wav": "", "provided_text": clean, "speaker": "creator"}


class ProvidedASR:
    """ASR stand-in for typed text.

    It reports `source="typed"` so no badge can claim this was transcribed, and it keeps the
    same confidence contract every other transcript obeys: a user who types nothing gets an
    abstention, not an invented line.
    """

    source = "typed"

    def __init__(self, rows: list[dict]):
        self.by_id = {r["id"]: r for r in rows if r.get("provided_text")}

    def transcribe(self, audio: bytes, keyterms: list[str], hint_id: str = ""):
        from ..adapters.asr import AsrResult

        row: Optional[dict] = self.by_id.get(hint_id)
        text = (row or {}).get("provided_text", "")
        return AsrResult(text=text, confidence=1.0 if text.strip() else 0.0, source="typed",
                         model="typed by the user")
