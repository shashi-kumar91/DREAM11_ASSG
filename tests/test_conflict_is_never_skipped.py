"""The two behaviours the demo is judged on, tested without a working model.

A creator repeating a score is noise. A creator stating a *different* score is the most
valuable line in the feed - and the analyst, live, classified it as filler and the
supervisor skipped it. So the check that catches it may not depend on either of them:
these tests run the session against a provider that is dead, and against one that is
actively unhelpful, and still expect the conflict card.
"""
import pytest

from backend.adapters.asr import CachedASR
from backend.kernel.llm import LLMError
from backend.kernel.schemas import (SupervisorDecision, TruthLabel, Utterance, VerdictKind)
from backend.kernel.trace import TraceRecorder
from backend.packs.creator.domain import Feed
from backend.packs.creator.predictions import (Ledger, PredictionExtraction, to_int_or_none,
                                               to_metric_or_none)
from backend.packs.creator.session import CreatorSession, SessionConfig

U07 = "Score 142 hai abhi, 142/3, same as before."
U04 = "Vikram Naidu out ho gaye. Score abhi 142/4 hai."
T07 = 133.6


class DeadLLM:
    """Every call raises: the worst provider day a demo can hit."""

    mode_used = "unavailable"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        raise LLMError("provider down")


class SkippingLLM:
    """A supervisor that decides, every single time, that the contradiction is not interesting."""

    mode_used = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        return SupervisorDecision(action="skip", thought="not interesting",
                                  reason="filler restatement"), {"source": "stub"}


def _session(tmp_path, llm) -> CreatorSession:
    sess = CreatorSession(llm, Feed.load(), CachedASR(), [], TraceRecorder(tmp_path / "t.jsonl"),
                          lambda evt: None, cfg=SessionConfig())
    sess.t = T07
    for uid, t, text in [("u04", 92.0, U04), ("u07", T07 - 7.0, U07)]:
        sess.store_utterance(Utterance(id=uid, t_start=t, t_end=t + 6, text=text,
                                       speaker="creator", asr_confidence=0.94))
    sess.current_utterance = sess.history[-1]
    return sess


def test_a_spoken_score_is_verified_before_the_first_model_call(tmp_path):
    sess = _session(tmp_path, DeadLLM())
    verdict = sess._prescreen(sess.history[-1])
    assert verdict["kind"] == VerdictKind.CONFLICT.value
    assert verdict["claimed"] == "142/3" and verdict["observed"] == "147/5"
    assert sess.llm.calls == 0, "the check that catches the contradiction must not cost a call"


def test_the_conflict_card_survives_a_dead_provider(tmp_path):
    sess = _session(tmp_path, DeadLLM())
    ep = sess.run_utterance(sess.history[-1])
    assert ep.terminal.value == "failed"
    assert len(sess.cards) == 1
    card = sess.cards[0]
    assert card.truth_label is TruthLabel.CONFLICT
    assert "142/3" in card.body and "147/5" in card.body


def test_a_supervisor_that_skips_everything_cannot_swallow_the_contradiction(tmp_path):
    sess = _session(tmp_path, SkippingLLM())
    ep = sess.run_utterance(sess.history[-1])
    assert ep.terminal.value == "skipped"
    assert len(sess.cards) == 1, "the veto must still leave the fan with the conflict card"
    assert sess.cards[0].truth_label is TruthLabel.CONFLICT
    kinds = [r["kind"] for r in TraceRecorder.load(tmp_path / "t.jsonl")]
    assert "terminal_vetoed" in kinds


@pytest.mark.parametrize("uid,text", [("u02", "Yeh banda ab reverse sweep try kar sakta hai"),
                                      ("u10", "2.5 millimeter se kam edge ho to not out")])
def test_the_prescreen_only_fires_on_a_score_shape(tmp_path, uid, text):
    """It is a score detector, not a second verifier for everything - the cost valve stays."""
    sess = _session(tmp_path, DeadLLM())
    u = Utterance(id=uid, t_start=20.0, t_end=26.0, text=text, speaker="creator",
                  asr_confidence=0.94)
    assert sess._prescreen(u) is None


def test_prediction_fields_coerce_words_the_models_actually_say(tmp_path):
    """Live, the analyst answered `over: "next over"` and the int parse killed the episode."""
    assert to_int_or_none("next over") is None
    assert to_int_or_none("agle over") is None
    assert to_int_or_none("17") == 17 and to_int_or_none(17.2) == 17
    assert to_int_or_none("over 17.2") == 17
    assert to_int_or_none("do") == 2 and to_int_or_none("two yorkers") == 2
    assert to_metric_or_none("yorkers") == "yorker_count"
    assert to_metric_or_none("wickets fell") == "wicket"
    assert to_metric_or_none("vibes") is None

    ex = PredictionExtraction.model_validate(
        {"text": "Bedi agle over mein do yorker phenkega", "bowler": "Bedi",
         "over": "next over", "metric": "yorker_count", "threshold": "do"})
    sess = _session(tmp_path, DeadLLM())
    p, why = Ledger().log(ex, "u05", 108.0, sess.feed, sess.feed.next_over_bowler(108.0))
    assert why == "logged" and p is not None
    # the number stays code's: "next over" became the ledger's own current-over + 1
    assert p.over == 17 and p.threshold == 2
