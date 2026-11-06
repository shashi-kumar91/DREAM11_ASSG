"""A malformed tool call must teach the agent, not quietly return a usable-looking verdict.

This cost 8 live Gemini calls during development: the verifier invented its own claim shape,
`verify_claim` answered UNVERIFIABLE "no checkable claim", and the agent read that as "the
creator was wrong" instead of "I sent garbage" and tried again.
"""
import json

import pytest

from backend.kernel.registry import ToolRegistry
from backend.packs.creator.domain import Feed
from backend.packs.creator.tools import CreatorToolbox


class FakeSession:
    """Just enough session for the claim tools: a clock, a feed and one utterance."""

    def __init__(self):
        self.feed = Feed.load()
        self.t = 99.1
        self.verdicts = {}
        from backend.kernel.schemas import Utterance
        self.current_utterance = Utterance(id="u04", t_start=92.0, t_end=96.0,
                                           text="Vikram Naidu out ho gaye. Score abhi 142/4 hai.",
                                           speaker="c", asr_confidence=0.94)
        self.history = [self.current_utterance]

    def store_verdict(self, v):
        self.verdicts[v.id] = v

    def note_feed_read(self, rows):
        pass

    def clamped(self):
        return self.feed.clamped(self.feed.state_at(self.t).last_event_id)

    def transcript_search(self, q, limit=4):
        return []


@pytest.fixture()
def registry():
    return CreatorToolbox(FakeSession()).build_registry()


def _claim(reg, payload) -> dict:
    res = reg.call("verify_claim", {"claim_json": json.dumps(payload)})
    return res.data if res.ok else {"error": res.error}


def test_unknown_claim_shape_is_named_not_muddling_through(registry):
    res = _claim(registry, {"nonsense": True})
    assert "bad_claim" in str(res.get("error", ""))
    assert "expected" in res, "the error must carry the schema so the agent can correct"


def test_agent_synonyms_recover_into_a_real_verdict(registry):
    """What a live model actually sent: score as a string, 'event': 'wicket', no kind."""
    res = _claim(registry, {"claim": "Score abhi 142/4 hai.", "event": "wicket",
                           "player": "Vikram Naidu", "score": "142/4", "over": "16.3"})
    assert res.get("kind") in ("CONFIRMED", "CONFLICT", "CONFIRMED_LAG"), res


def test_numbers_are_still_grounded_after_recovery(registry):
    """Recovery must not become a licence: 199 was never spoken."""
    res = _claim(registry, {"kind": "score", "raw_phrase": "199/4", "runs": 199, "wickets": 4})
    assert res["kind"] == "UNVERIFIABLE"
    assert "extraction_not_grounded" in res["detail"]


def test_score_string_splits_into_runs_and_wickets():
    from backend.packs.creator.verify import StructuredClaim

    c = StructuredClaim.model_validate({"score": "142/4"})
    assert (c.kind, c.runs, c.wickets) == ("score", 142, 4)


def test_recall_requires_an_id_and_lists_what_exists(registry):
    res = registry.call("recall_utterance", {})
    assert not res.ok and res.error.startswith("bad_args:")
    ok = registry.call("recall_utterance", {"utterance_id": "u04"})
    assert ok.ok and ok.data["text"].startswith("Vikram Naidu")
    miss = registry.call("recall_utterance", {"utterance_id": "nope"})
    assert miss.data["known_ids"] == ["u04"]
