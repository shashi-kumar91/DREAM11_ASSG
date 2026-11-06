"""Prediction ledger: log, settle, and produce a numeric tally the card may cite.

Settlement is pure code over the feed. The tally text is what licenses the digits in a
settlement card, so "2 yorkers" on a card is only possible when the feed really contains
two balls tagged yorker.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

from ...kernel.schemas import MatchEvent, Prediction
from .domain import Feed

Metric = Literal["yorker_count", "wicket", "runs_lte", "boundary_count", "dot_balls"]


def det_id(prefix: str, *parts: Any) -> str:
    """Content-addressed id: prediction and tally ids are dumped into the supervisor and
    critic prompts (scratchpad, tool results, settlement observation), so a uuid id makes
    every prompt - and every cassette key - unique per run. That is why replay used to miss
    and the episode fell back deterministically instead of proving the model path worked."""
    raw = "\x1f".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"

# The analyst is asked for integers and live models answer in words: "next over", "do
# yorker". A failed int parse costs a schema retry plus a supervisor turn, and on the last
# live pass it burnt the whole budget of the episode that carries the demo's prediction.
# So words are resolved here, by code, before validation.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "che": 6, "saat": 7, "aath": 8,
}
# Anything that means "the over that has not started yet" resolves to None on purpose: the
# ledger computes current + 1, so the number stays code's.
RELATIVE_OVER_WORDS = ("next", "agle", "agla", "agale", "nextover", "upcoming", "aane")

METRIC_WORDS = {
    "yorker": "yorker_count", "yorkers": "yorker_count", "yorker_count": "yorker_count",
    "wicket": "wicket", "wickets": "wicket", "dismissal": "wicket", "out": "wicket",
    "run": "runs_lte", "runs": "runs_lte", "conceded": "runs_lte", "runs_lte": "runs_lte",
    "boundary": "boundary_count", "boundaries": "boundary_count", "four": "boundary_count",
    "sixer": "boundary_count", "six": "boundary_count", "boundary_count": "boundary_count",
    "dot": "dot_balls", "dots": "dot_balls", "dot_balls": "dot_balls",
}


def to_int_or_none(v: Any) -> Any:
    """Coerce the shapes a model emits for a count into an int, or None.

    None means "the extraction gave us nothing numeric", which the ledger already treats as
    "resolve this from match state" (for `over`) or "reject this prediction" (for
    `threshold`). Turning "next over" into a guessed integer is invention; leaving it null
    lets the deterministic rule answer it.
    """
    if v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip().lower()
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    words = re.findall(r"[a-z]+", s)
    if any(w.startswith(RELATIVE_OVER_WORDS) for w in words):
        return None
    for w in words:
        if w in NUMBER_WORDS:
            return NUMBER_WORDS[w]
    return None


_METRICS = set(get_args(Metric))


def to_metric_or_none(v: Any) -> Any:
    """A metric the ledger does not know becomes a missing metric, not a crashed episode.

    `metric_missing` is an honest rejection the supervisor can read; a schema failure is
    invisible and eats the budget of the episode it happens in.
    """
    if v is None or (isinstance(v, str) and v in _METRICS):
        return v
    if isinstance(v, str):
        for w in re.findall(r"[a-z]+", v.lower()):
            if w in METRIC_WORDS:
                return METRIC_WORDS[w]
    return None


class PredictionExtraction(BaseModel):
    """What the analyst claims the creator predicted; validated before it is stored."""

    text: str = Field(default="", max_length=300)
    bowler: Optional[str] = None
    batter: Optional[str] = None
    over: Optional[int] = None
    metric: Optional[Metric] = None
    threshold: Optional[int] = None

    _coerce_over = field_validator("over", mode="before")(to_int_or_none)
    _coerce_threshold = field_validator("threshold", mode="before")(to_int_or_none)
    _coerce_metric = field_validator("metric", mode="before")(to_metric_or_none)


# Counting words a Hindi/English live stream actually uses. Only an explicit number in the
# creator's own sentence is read here: this completes a prediction the model left half-filled,
# and it can never invent a threshold that was not said.
_COUNT_WORDS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4,
    "four": 4, "paanch": 5, "panch": 5, "five": 5, "chhah": 6, "che": 6, "six": 6,
}
_PHRASE_METRIC = [
    (("six", "sixer", "chhaka", "chakka", "maximum"), "boundary_count"),
    (("four", "char", "chaar", "boundary"), "boundary_count"),
    (("wicket", "out", "wkt"), "wicket"),
    (("dot",), "dot_balls"),
    (("run", "runs"), "runs"),
]


def complete_from_text(ex: PredictionExtraction, text: str) -> tuple[PredictionExtraction, list[str]]:
    """Fill only what the creator's own sentence states explicitly.

    The failure this repairs was real: a live run said "ek six maar dega", the model returned
    `metric=boundary_count` with no threshold, the ledger correctly refused to store a
    prediction with no target, and the fan saw nothing. The word "ek" is right there in the
    evidence - reading it is not guessing, and every filled field is reported so the UI can say
    where the number came from.
    """
    if ex.metric is not None and ex.threshold is not None:
        return ex, []
    low = f" {(text or '').lower()} "
    filled: list[str] = []
    metric = ex.metric
    if metric is None:
        for words, m in _PHRASE_METRIC:
            if any(f" {w}" in low for w in words):
                metric = m      # Metric is a Literal, so the string is the value
                filled.append(f"metric={m}")
                break
    threshold = ex.threshold
    if threshold is None:
        for word, n in _COUNT_WORDS.items():
            if re.search(rf"\b{word}\b", low):
                threshold = n
                filled.append(f"threshold={n} (from '{word}')")
                break
    if threshold is None and metric is not None:
        # "will hit a boundary" with no count stated is still a checkable claim about one ball
        # or more; 1 is the weakest reading of it, and the field is reported as inferred.
        threshold = 1
        filled.append("threshold=1 (weakest reading of an unnumbered claim)")
    return ex.model_copy(update={"metric": metric, "threshold": threshold}), filled


class Settlement(BaseModel):
    prediction_id: str
    status: Literal["hit", "miss", "void"]
    tally_id: str
    tally_text: str
    event_ids: list[str] = Field(default_factory=list)
    why: str = ""


class Ledger:
    def __init__(self) -> None:
        self.open: list[Prediction] = []
        self.closed: list[Prediction] = []
        self.tallies: dict[str, str] = {}

    # -- logging ----------------------------------------------------------- #

    def log(self, ex: PredictionExtraction, utterance_id: str, t: float,
            feed: Feed, next_bowler: Optional[str]) -> tuple[Optional[Prediction], str]:
        if not ex.metric:
            return None, "metric_missing"
        bowler = _resolve(ex.bowler or "", feed.meta.squads)
        if bowler is None:
            return None, f"bowler_not_in_squad:{ex.bowler}"
        over = ex.over
        if over is None:
            cur = int(feed.state_at(t).over_label.split(".")[0]) if feed.state_at(t) else None
            over = (cur + 1) if cur is not None else None
        if over is None or not 1 <= over <= feed.meta.total_overs:
            return None, f"over_out_of_range:{over}"
        if ex.threshold is None or ex.threshold < 1:
            return None, f"threshold_missing:{ex.threshold}"
        if next_bowler and bowler != next_bowler:
            return None, f"bowler_not_bowling_next_over:{bowler}!={next_bowler}"
        p = Prediction(id=det_id("pr", utterance_id, over, ex.metric, ex.threshold,
                                 bowler, ex.batter or "", ex.text or "prediction"),
                       utterance_id=utterance_id, t_logged=t,
                       text=ex.text or "prediction", bowler=bowler, batter=_resolve(
                           ex.batter or "", feed.meta.squads), over=over,
                       metric=ex.metric, threshold=ex.threshold)
        self.open.append(p)
        return p, "logged"

    # -- settling ---------------------------------------------------------- #

    def settle_due(self, feed: Feed, upto_t: float) -> list[Settlement]:
        out: list[Settlement] = []
        for p in list(self.open):
            if not feed.over_complete(p.over, upto_t):
                continue
            self.open.remove(p)
            s = self._settle(p, feed)
            p.status = s.status
            p.t_settled = upto_t
            p.tally_text = s.tally_text
            p.settle_event_ids = s.event_ids
            self.closed.append(p)
            out.append(s)
        return out

    def _settle(self, p: Prediction, feed: Feed) -> Settlement:
        rows: list[MatchEvent] = feed.events_in_over(p.over)
        ids = [e.id for e in rows]
        over_bowler = rows[0].bowler if rows else ""
        if p.bowler and over_bowler and over_bowler != p.bowler:
            tally_text = f"{p.bowler} did not bowl over {p.over}; {over_bowler} did"
            return Settlement(prediction_id=p.id, status="void", tally_id=self._tally(tally_text, ids),
                              tally_text=tally_text, event_ids=ids, why="wrong_bowler")

        yorkers = len([e for e in rows if e.ball_type == "yorker"])
        slowers = len([e for e in rows if e.ball_type == "slower"])
        runs = sum(e.runs for e in rows)
        wickets = len([e for e in rows if e.wicket])
        boundaries = len([e for e in rows if e.runs >= 4])
        dots = len([e for e in rows if e.runs == 0])

        if p.metric == "yorker_count":
            actual, hits = yorkers, yorkers >= (p.threshold or 1)
            tally_text = f"{yorkers} yorkers, {slowers} slower ball in over {p.over}"
        elif p.metric == "wicket":
            actual, hits = wickets, wickets >= (p.threshold or 1)
            tally_text = f"{wickets} wickets fell in over {p.over}"
        elif p.metric == "boundary_count":
            actual, hits = boundaries, boundaries >= (p.threshold or 1)
            tally_text = f"{boundaries} boundaries in over {p.over}"
        elif p.metric == "dot_balls":
            actual, hits = dots, dots >= (p.threshold or 1)
            tally_text = f"{dots} dot balls in over {p.over}"
        else:  # runs_lte
            actual, hits = runs, runs <= (p.threshold or runs)
            tally_text = f"{runs} runs conceded in over {p.over}"

        status = "hit" if hits else "miss"
        return Settlement(prediction_id=p.id, status=status,
                          tally_id=self._tally(tally_text, ids), tally_text=tally_text,
                          event_ids=ids, why=f"observed {actual} vs threshold {p.threshold}")

    def _tally(self, text: str, ids: list[str]) -> str:
        tid = det_id("tal", text, ",".join(ids))
        self.tallies[tid] = text
        return tid

    def snapshot(self) -> dict:
        return {"open": [p.model_dump() for p in self.open],
                "closed": [p.model_dump() for p in self.closed]}


def _resolve(token: str, squads: list[str]) -> Optional[str]:
    token = (token or "").strip().lower()
    if not token:
        return None
    for name in squads:
        parts = name.lower().split()
        if token == name.lower() or token in parts or any(p.startswith(token) for p in parts):
            return name
    for name in squads:
        if token in name.lower():
            return name
    return None
