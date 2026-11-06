"""Regressions for three audit findings: blank ASR, answered-nothing 409, kernel roster.

Each test names the defect it pins down, because the value of a regression test is the bug it
remembers. All three are offline: the ASR is a stub, the roster is introspection, and the HTTP
check drives the app with an in-process client.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class BlankASR:
    """A provider that returns empty text with maximum confidence - Gemini does exactly this."""

    def __init__(self, text: str = "", confidence: float = 1.0):
        self.text, self.confidence = text, confidence

    def transcribe(self, audio: bytes, keyterms, hint_id: str = ""):
        from backend.adapters.asr import AsrResult

        return AsrResult(text=self.text, confidence=self.confidence, source="live",
                         model="stub-asr", uncertain_words=[])


@pytest.fixture()
def rows() -> list[dict]:
    import json

    return json.loads((ROOT / "backend" / "fixtures" / "transcript_expected.json")
                      .read_text(encoding="utf-8"))


def _session(tmp_path, asr):
    from backend.kernel.openrouter import OpenRouterLLM
    from backend.kernel.trace import TraceRecorder
    from backend.packs.creator.domain import Feed
    from backend.packs.creator.session import CreatorSession, SessionConfig
    from backend.kernel.schemas import Budget

    return CreatorSession(OpenRouterLLM(live=False, cassette_mode="replay"), Feed.load(), asr, [],
                          TraceRecorder(tmp_path / "t.jsonl"), lambda e: None,
                          cfg=SessionConfig(budget=Budget(max_llm_calls=1, max_tool_calls=1,
                                                          max_ms=1000, max_specialist_rounds=1)))


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_blank_transcript_is_not_evidence(tmp_path, text, rows):
    """Empty ASR text used to be ingested at confidence 1.0 and handed to the supervisor."""
    sess = _session(tmp_path, BlankASR(text=text))
    row = dict(rows[0])
    row["audio_bytes"] = b""

    u = sess.ingest(row)

    assert u.text.strip() == ""
    assert u.id in sess.not_evidence, "a blank transcript must never count as evidence"
    kinds = [(e["kind"], e.get("reason")) for e in sess.trace.events if e["kind"] == "asr_abstain"]
    assert ("asr_abstain", "asr_empty_transcript") in kinds
    assert sess.process(row) is None, "no episode may run on an empty transcript"


def test_confident_blank_asr_does_not_reach_the_agents(tmp_path, rows):
    """The gate reads the text, not only the confidence number."""
    sess = _session(tmp_path, BlankASR(text="", confidence=1.0))
    row = dict(rows[1])
    row["audio_bytes"] = b""

    assert sess.process(row) is None
    assert sess.cards == []
    assert not [e for e in sess.trace.events if e["kind"] == "episode_start"]


def test_low_confidence_still_abstains_with_its_own_reason(tmp_path, rows):
    """The original gate must not lose its own reason in the rewrite."""
    sess = _session(tmp_path, BlankASR(text="Kohli will finish it", confidence=0.2))
    row = dict(rows[0])
    row["audio_bytes"] = b""

    sess.ingest(row)

    reasons = [e.get("reason") for e in sess.trace.events if e["kind"] == "asr_abstain"]
    assert reasons == ["asr_confidence_below_floor"]


def test_kernel_map_is_introspected_not_hand_written():
    """The agent-room roster must be the registry's roster, or the animation is a cartoon."""
    from backend.app.kernel_map import kernel_map

    m = kernel_map()
    gap_roles = [a["role"] for a in m["packs"]["gap"]["agents"]]
    creator_roles = [a["role"] for a in m["packs"]["creator"]["agents"]]
    assert gap_roles == ["reader", "investigator", "questioner", "strategist", "critic"]
    assert creator_roles == ["perceiver", "analyst", "verifier", "strategist", "critic"]
    # Tool lists come from the same objects the registry hands the agents.
    gap_tools = {t["name"] for t in m["packs"]["gap"]["tools"]}
    assert {"query_event_feed", "inspect_frame", "ask_fan", "offer_clip"} <= gap_tools
    for agent in m["packs"]["creator"]["agents"]:
        assert set(agent["tools"]) <= {t["name"] for t in m["packs"]["creator"]["tools"]}


def test_answer_without_a_pending_question_is_a_conflict():
    """A 200 here told a client its answer was heard by a run that never asked."""
    from fastapi.testclient import TestClient

    from backend.app.server import app

    client = TestClient(app)
    created = client.post("/api/session", json={"pack": "creator", "scenario": "u09",
                                                "mode": "watch", "run_mode": "offline-ci"})
    assert created.status_code == 200
    sid = created.json()["session_id"]

    res = client.post(f"/api/session/{sid}/answer", json={"text": "yes"})

    assert res.status_code == 409, res.text
    assert "no question is pending" in res.json()["error"]


def test_query_abstain_gets_its_own_truth_label(tmp_path):
    """"Answer from creator memory" is a lie when the creator never covered the question.

    The abstain card says so in its body, so the label it carries must say so too - otherwise a
    reader who only sees the badge believes an answer exists.
    """
    from backend.kernel.schemas import CardDraft, TruthLabel

    sess = _session(tmp_path, BlankASR(text="anything", confidence=1.0))

    abstain = CardDraft(kind="query_answer", headline="The creator hasn't said anything about that",
                        body="No attributed transcript segment matches the question.",
                        tags=["query", "abstain"])
    real = CardDraft(kind="query_answer", headline="From the creator's own words", body="- 01:10 …",
                     tags=["query"])

    label, why = sess._label(abstain, {})
    assert label is TruthLabel.NO_EVIDENCE
    assert why == "query_abstain_template"

    label, why = sess._label(real, {})
    assert label is TruthLabel.FAN_QUERY
    assert why == "query_template"
