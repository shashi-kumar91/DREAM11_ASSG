"""Tier-0 triage: the cost valve must be stupid-but-correct.

The one thing it must never do is swallow a contradiction. A creator repeating a score is
noise; a creator stating a *different* score is the most valuable utterance in the feed.
"""
from backend.kernel.schemas import Utterance
from backend.packs.creator.triage import numeric_tokens, triage


def _u(uid: str, text: str, conf: float = 0.9) -> Utterance:
    return Utterance(id=uid, t_start=0.0, t_end=1.0, text=text, speaker="c",
                     asr_confidence=conf)


def test_greeting_is_skipped_without_a_model_call():
    out = triage(_u("u01", "Kya baat hai doston, comments mein batao kaun kaun dekh raha hai"), [])
    assert out.skip and out.reason == "greeting_no_content"


def test_low_confidence_abstains():
    out = triage(_u("u03", "yorker bhai", conf=0.2), [])
    assert out.skip and out.reason.startswith("low_asr_confidence")


def test_same_score_restatement_is_a_repeat():
    prior = _u("u04", "Vikram Naidu out ho gaye. Score abhi 142/4 hai.")
    again = _u("u09", "Score 142/4 hai abhi, main dekh raha hoon score")
    out = triage(again, [prior])
    assert out.skip and out.reason.startswith("repeats:u04")


def test_changed_score_is_never_treated_as_a_repeat():
    """Regression: u07 (142/3 after u04 said 142/4) was silently dropped as a restatement."""
    prior = _u("u04", "Vikram Naidu out ho gaye. Score abhi 142/4 hai.")
    contradict = _u("u07", "Score 142 hai abhi, 142/3, same as before.")
    out = triage(contradict, [prior])
    assert not out.skip, out.reason


def test_numeric_tokens_keep_score_and_rate_shapes():
    assert numeric_tokens("Score 142/3, rrr 9.67") == {"142/3", "9.67"}


def test_explicit_restatement_is_a_repeat_even_when_worded_differently():
    """Fixture u06 restates u02's tactic in new words and says so ("wahi dobara").

    The old rule only counted overlapping words, so a paraphrased repeat slipped through and
    cost a full multi-agent episode on something the fan had already been shown.
    """
    prior = _u("u02", "Yeh banda ab reverse sweep try kar sakta hai, field dekho, third man up hai.")
    again = _u("u06", "Woh reverse sweep wala idea, haan, wahi dobara, reverse sweep hi aayega.")
    out = triage(again, [prior])
    assert out.skip and out.reason == "repeats:u02"
