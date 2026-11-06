"""The commit gate, exercised through the real session instead of a fake draft.

This path had no test until a live run showed it rejecting its own template: the conflict
card quotes the over the fan is sitting at, and nothing in the cited evidence carried that
over label, so code refused a number code had itself written.
"""
import pytest

from backend.adapters.asr import CachedASR
from backend.kernel.openrouter import OpenRouterLLM
from backend.kernel.schemas import TruthLabel, Utterance, VerdictKind
from backend.kernel.trace import TraceRecorder
from backend.packs.creator.domain import Feed
from backend.packs.creator.session import CreatorSession, SessionConfig
from backend.packs.creator.templates import conflict, verified
from backend.packs.creator.verify import StructuredClaim, verify


def _session(tmp_path) -> CreatorSession:
    feed = Feed.load()
    rows = [{"id": "u04", "t_start": 92.0, "t_end": 99.0, "text": "", "wav": ""}]
    return CreatorSession(OpenRouterLLM(live=False, cassette_mode="replay"), feed, CachedASR(),
                          rows, TraceRecorder(tmp_path / "trace.jsonl"), lambda evt: None,
                          cfg=SessionConfig())


def _run(tmp_path, uid: str, text: str, t: float, claim: dict):
    sess = _session(tmp_path)
    sess.t = t
    u = Utterance(id=uid, t_start=t - 7.0, t_end=t, text=text, speaker="creator",
                  asr_confidence=0.94)
    sess.store_utterance(u)
    sess.current_utterance = u
    verdict = verify(StructuredClaim.model_validate(claim), text, sess.feed,
                     sess.feed.state_at(t))
    sess.store_verdict(verdict)
    sess._scratch = {"verdict": verdict.model_dump()}
    draft = sess._before_commit(sess._verdict_card(
        _blank(u, verdict), sess._scratch["verdict"], u), sess._scratch)
    return sess, verdict, draft, sess._commit_draft(draft)


def _blank(u, verdict):
    return conflict(u, verdict, "0.0") if verdict.kind == VerdictKind.CONFLICT \
        else verified(u, verdict, "0.0")


def test_conflict_card_over_the_fan_position_is_licensed_and_commits(tmp_path):
    """u07: the creator's stale 142/3 against a feed that has moved to 147/5 at 16.6."""
    sess, verdict, draft, res = _run(
        tmp_path, "u07", "Score 142 hai abhi, 142/3, same as before.", 133.6,
        {"kind": "score", "raw_phrase": "142/3", "runs": 142, "wickets": 3})
    assert verdict.kind is VerdictKind.CONFLICT
    assert res.ok, [v.model_dump() for v in res.violations]
    card = res.card
    assert res.card.truth_label is TruthLabel.CONFLICT
    assert card.template_used == "conflict_template"
    # the current over is on the card because code put it there, not because the model wrote it
    assert "16.6" in card.body
    assert card.cited_event_ids[-1] == sess.feed.state_at(133.6).last_event_id


def test_verified_card_commits_with_feed_position(tmp_path):
    sess, verdict, draft, res = _run(
        tmp_path, "u04", "Vikram Naidu out ho gaye. Score abhi 142/4 hai.", 99.1,
        {"kind": "score", "raw_phrase": "142/4", "runs": 142, "wickets": 4})
    assert verdict.kind in (VerdictKind.CONFIRMED, VerdictKind.CONFIRMED_LAG)
    assert res.ok, [v.model_dump() for v in res.violations]
    assert res.card.truth_label is TruthLabel.VERIFIED
    assert res.card.template_used == "verified_template"


def test_model_authored_number_is_still_refused(tmp_path):
    """Auto-citing the current event must not widen the licence beyond that event."""
    sess, verdict, draft, res = _run(
        tmp_path, "u07", "Score 142 hai abhi, 142/3, same as before.", 133.6,
        {"kind": "score", "raw_phrase": "142/3", "runs": 142, "wickets": 3})
    draft = draft.model_copy(update={"body": draft.body + " Target was 153 in over 19.4."})
    refused = sess._commit_draft(draft)
    assert not refused.ok
    offending = " ".join(v.offending or "" for v in refused.violations)
    assert "153" in offending and "19.4" in offending


@pytest.mark.parametrize("bad_over", ["17.0", "18.3"])
def test_state_beyond_the_cursor_is_never_cited(tmp_path, bad_over):
    """The auto-cite goes through the clamp, so a clock ahead of the fan yields no licence."""
    sess = _session(tmp_path)
    sess.t = 133.6
    st = sess.feed.state_at(sess.t)
    assert st.over_label == "16.6"
    assert sess.clamped().get(sess.feed.state_at(180.0).last_event_id) is None


def test_prediction_card_licenses_its_own_ledger_scope(tmp_path):
    """u05: the template quotes "over 17", which the creator never said as a digit.

    The ledger entry is code's, so it is evidence. Without that the commit gate rejected the
    pack's own prediction template in a live run.
    """
    from backend.kernel.schemas import Prediction
    from backend.packs.creator.templates import prediction_card

    sess = _session(tmp_path)
    sess.t = 112.0
    u = Utterance(id="u05", t_start=108.0, t_end=112.0,
                  text="Bedi agle over mein do yorker phenkega, main dekh raha hoon uska length.",
                  speaker="creator", asr_confidence=0.94)
    sess.store_utterance(u)
    sess.current_utterance = u
    p = Prediction(id="pr_test", utterance_id="u05", t_logged=110.0,
                   text="Bedi agle over mein do yorker phenkega", bowler="Rohan Bedi", over=17,
                   metric="yorker_count", threshold=2)
    sess._scratch = {"prediction": p.model_dump()}
    res = sess._commit_draft(prediction_card(u, p))
    assert res.ok, [v.model_dump() for v in res.violations]
    assert res.card.truth_label is TruthLabel.PREDICTION
    assert "17" in res.card.numbers


def _query_session(tmp_path):
    sess = _session(tmp_path)
    sess.t = 210.0
    for uid, t, text in [("u01", 4.0, "Kya baat hai doston, comments mein batao kaun dekh raha hai"),
                         ("u08", 142.0, "Death overs mein spin ko medium length par rakhna chahiye"),
                         ("u12", 204.0, "Haan bhai, aaj rain ka koi risk nahi, poora clear sky hai")]:
        sess.store_utterance(Utterance(id=uid, t_start=t, t_end=t + 6, text=text,
                                       speaker="creator", asr_confidence=0.94))
    sess.not_evidence.add("u01")
    return sess


def test_query_answer_quotes_the_matching_sentence(tmp_path):
    sess = _query_session(tmp_path)
    sess._scratch = {"is_query": True, "query": "What did he say about the weather?"}
    res = sess._commit_draft(sess._query_card(sess._scratch["query"]))
    assert res.ok, [v.model_dump() for v in res.violations]
    body = res.card.body
    assert "rain ka koi risk nahi" in body
    assert "Haan bhai" not in body          # the filler fragment must not become the answer
    assert "u01" not in res.card.cited_utterance_ids


def test_query_with_no_evidence_commits_an_abstain_card(tmp_path):
    """"The creator never said that" is a claim about the search, so it cites the search."""
    sess = _query_session(tmp_path)
    sess._scratch = {"is_query": True, "query": "Did the creator talk about guitar lessons?"}
    res = sess._commit_draft(sess._query_card(sess._scratch["query"]))
    assert res.ok, [v.model_dump() for v in res.violations]
    assert res.card.cited_verdict_ids
    assert "hasn't said anything" in res.card.headline
