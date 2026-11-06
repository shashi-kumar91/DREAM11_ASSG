"""Acceptance criteria for Pack A's promises and for the HTTP layer.

Companion to `tests/test_acceptance.py` (Pack B / kernel). Two things are tested here that the
pack's own unit tests do not: the promises about *speech* (a contradicted claim must carry the
contradicting label, the ledger may only settle against balls that have already happened, an
unavailable ASR must degrade to the badged cached transcript) and the promises the reviewer can
only check over HTTP (SSE replays the journal from the beginning, the fan's answer travels over
the wire, `quiet` costs zero bytes and `watch` costs exactly the clip).

Everything is offline: either the provider is dead on purpose, or the turn comes from
`tests/cassettes`. `make_asr`/`make_llm` are only ever asked for a non-live mode, so no test in
this file can reach the network.
"""
import json
import time
from pathlib import Path

import pytest

from backend.kernel.commit import numbers_in
from backend.kernel.llm import LLMError
from backend.kernel.openrouter import OpenRouterLLM
from backend.kernel.schemas import Budget, TruthLabel, VerdictKind
from backend.kernel.trace import TraceRecorder
from backend.packs.creator.domain import Feed
from backend.packs.creator.predictions import Ledger, PredictionExtraction
from backend.packs.creator.session import CreatorSession, SessionConfig
from backend.packs.creator.templates import fmt

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "fixtures"

BUDGET = Budget(max_llm_calls=12, max_tool_calls=12, max_ms=240_000, max_specialist_rounds=8)


class DeadProvider:
    """Every call raises: the promise has to hold on the day the provider does not answer."""

    mode_used = "unavailable"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, prompt, *a, **k):
        self.calls += 1
        self.prompts.append(prompt)
        raise LLMError("provider down")


def replay_llm() -> OpenRouterLLM:
    return OpenRouterLLM(live=False, cassette_mode="replay")


def transcript_rows() -> list[dict]:
    rows = json.loads((FIXTURES / "transcript_expected.json").read_text(encoding="utf-8"))
    rows.sort(key=lambda r: r["t_start"])
    for r in rows:
        r["audio_bytes"] = (FIXTURES / r["wav"]).read_bytes()
    return rows


@pytest.fixture(scope="module")
def feed() -> Feed:
    return Feed.load()


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return transcript_rows()


def _session(feed, rows, llm, tmp_path, asr=None, name="creator", events=None) -> CreatorSession:
    from backend.adapters.asr import CachedASR

    return CreatorSession(llm, feed, asr or CachedASR(), rows,
                          TraceRecorder(tmp_path / f"{name}.jsonl"),
                          events.append if events is not None else (lambda e: None),
                          cfg=SessionConfig(budget=BUDGET))


def _run_one(feed, rows, llm, tmp_path, uid: str, asr=None,
             events=None) -> CreatorSession:
    """One utterance, in clock order up to it: the state the pack sees is the state it had."""
    row = next(r for r in rows if r["id"] == uid)
    sess = _session(feed, rows, llm, tmp_path, asr=asr, name=f"one_{uid}", events=events)
    sess.advance(float(row["t_end"]))
    sess.process(row)
    return sess


def _kinds(sess: CreatorSession, kind: str) -> list[dict]:
    return [e for e in sess.trace.events if e["kind"] == kind]


def _replay_session(feed, rows, tmp_path, name: str) -> CreatorSession:
    """The whole creator stream against recorded turns: no network, no flakes."""
    sess = _session(feed, rows, replay_llm(), tmp_path, name=name)
    for r in rows:
        sess.advance(float(r["t_end"]))
        sess.process(r)
    sess.advance(240.0)          # the innings ends; open predictions settle against real balls
    return sess


# --------------------------------------------------------------------------- #
# AC-12  a contradicted claim gets the contradicting label - with the model dead
# --------------------------------------------------------------------------- #

def test_a_claim_the_feed_contradicts_is_committed_as_the_contradiction(feed, rows, tmp_path):
    sess = _run_one(feed, rows, DeadProvider(), tmp_path, "u07")
    assert len(sess.cards) == 1, "the most valuable line in the feed may not be dropped"
    card = sess.cards[0]
    assert card.truth_label is TruthLabel.CONFLICT
    assert card.kind == "conflict"
    assert "u07" in card.cited_utterance_ids
    body = f"{card.headline} {card.body}"
    assert "142/3" in body, "the creator's own words must be quoted, not paraphrased"
    assert "147/5" in body, "and the feed's number must be printed next to it"
    assert card.committed_by == "kernel.commit"
    assert card.source_badges["llm"] == "unavailable", \
        "the badge has to admit there was no model to write this card"


def test_the_verdict_that_labels_the_conflict_ran_before_any_model_turn(feed, rows, tmp_path):
    """`_prescreen` is the difference between 'the kernel verifies claims' and 'verifies the
    claims an agent happened to notice' - so the check must precede the episode itself."""
    sess = _run_one(feed, rows, DeadProvider(), tmp_path, "u07")
    kinds = [e["kind"] for e in sess.trace.events]
    assert "kernel_verifier" in kinds, json.dumps(kinds)
    assert kinds.index("kernel_verifier") < kinds.index("episode_start"), \
        "a verifier that runs after the supervisor's first turn cannot outrank it"
    v = _kinds(sess, "kernel_verifier")[0]["verdict"]
    assert VerdictKind(v["kind"]) is VerdictKind.CONFLICT
    card = sess.cards[0]
    assert v["id"] in card.cited_verdict_ids, "the card must cite the verdict that made its label"
    assert "ran before the first supervisor turn" in _kinds(sess, "kernel_verifier")[0]["note"]


def test_a_claim_the_feed_confirms_is_badged_verified_not_reported(feed, rows, tmp_path):
    sess = _run_one(feed, rows, DeadProvider(), tmp_path, "u04")
    card = sess.cards[0]
    assert card.truth_label is TruthLabel.VERIFIED
    assert card.kind == "insight"
    assert "142/4" in f"{card.headline} {card.body}"


def test_an_opinion_is_never_promoted_to_match_fact(feed, rows, tmp_path):
    """The same gate that forces a conflict must leave a judgement labelled as judgement."""
    sess = _replay_session(feed, rows, tmp_path, "replay_opinion")
    opinion = [c for c in sess.cards if "u02" in c.cited_utterance_ids]
    assert opinion, "the creator's judgement call is a card"
    for card in opinion:
        assert card.truth_label is TruthLabel.OPINION
        assert card.truth_label is not TruthLabel.VERIFIED


# --------------------------------------------------------------------------- #
# AC-13  the ledger may only settle against balls that have already happened
# --------------------------------------------------------------------------- #

def _log_a_prediction(ledger: Ledger, feed: Feed, t: float):
    """Ask the ledger for a prediction the way the pack does: code resolves over and bowler."""
    bowler = feed.next_over_bowler(t)
    cur = int(feed.state_at(t).over_label.split(".")[0])
    ex = PredictionExtraction(text="he will land two yorkers", bowler=bowler, over=cur + 1,
                              metric="yorker_count", threshold=1)
    return ledger.log(ex, "u_test", t, feed, bowler)


def test_an_incomplete_over_is_never_settled(feed, tmp_path):
    ledger = Ledger()
    t = 120.0
    p, why = _log_a_prediction(ledger, feed, t)
    assert p is not None, why
    over = p.over
    last = feed.events_in_over(over)[-1]
    before = ledger.settle_due(feed, last.t_wall - 0.001)
    assert before == [], "a settlement against a ball that has not been bowled is an invention"
    assert p.id in [x.id for x in ledger.open]
    assert ledger.settle_due(feed, last.t_wall), "the same over must settle once it is complete"


@pytest.mark.parametrize("upto", [133.0, 180.0, 240.0])
def test_a_settlement_only_ever_counts_balls_inside_the_fans_now(feed, tmp_path, upto):
    """Every event the ledger cites must already exist at the clock time of the settlement."""
    ledger = Ledger()
    p, why = _log_a_prediction(ledger, feed, 120.0)
    assert p is not None, why
    settled = ledger.settle_due(feed, upto)
    closed = ledger.closed[0] if settled else None
    if closed is None:
        assert not feed.over_complete(p.over, upto)
        return
    assert closed.t_settled == upto
    seen = [e.id for e in feed.events if e.t_wall <= upto]
    for eid in closed.settle_event_ids:
        assert eid in seen, f"{eid} was bowled after the clock said {upto}s"
    assert max(feed.by_id[e].t_wall for e in closed.settle_event_ids) <= closed.t_settled


def test_a_replayed_settlement_carries_the_ledgers_own_verdict(feed, rows, tmp_path):
    """The demo's prediction: settled by code, printed by a template, labelled from the status."""
    sess = _replay_session(feed, rows, tmp_path, "replay_pred")
    assert sess.ledger.closed, "the stream must produce at least one settled prediction"
    for p in sess.ledger.closed:
        assert p.status in ("hit", "miss", "void")
        assert max(sess.feed.by_id[e].t_wall for e in p.settle_event_ids) <= p.t_settled
    settlement = [c for c in sess.cards if c.kind == "settlement"]
    assert settlement, "a settled prediction is a card, not a shrug"
    card = settlement[0]
    expected = json.loads((FIXTURES / "gold_labels.json")
                          .read_text(encoding="utf-8"))["expected_settlement"]
    assert card.truth_label is {"hit": TruthLabel.SETTLED_HIT, "miss": TruthLabel.SETTLED_MISS,
                                "void": TruthLabel.SETTLED_VOID}[expected]
    assert card.cited_tally_ids, "the digits on a settlement card come from the ledger's tally"
    # The licence, rebuilt exactly the way the pack builds it: the tally, the utterance the
    # prediction was spoken in, and the ledger's own prediction line. `card.numbers` is not
    # consulted - a gate that grades its own homework is not a gate.
    parts = [sess.ledger.tallies[t] for t in card.cited_tally_ids if t in sess.ledger.tallies]
    for uid in card.cited_utterance_ids:
        u = next((x for x in sess.history if x.id == uid), None)
        if u is not None:
            parts.append(f"{fmt(u.t_start)} {u.t_start:.1f} {u.text}")
    for p in sess.ledger.closed + sess.ledger.open:
        parts.append(f"prediction {p.text} over {p.over} bowler {p.bowler or ''} "
                     f"{p.metric or ''}>={p.threshold if p.threshold is not None else ''} "
                     f"logged at {fmt(p.t_logged)} {p.t_logged:.1f}")
    licensed: set[str] = set()
    for text in parts:
        licensed |= numbers_in(text)
    printed = numbers_in(f"{card.headline} {card.body}")
    assert printed <= licensed, f"{sorted(printed - licensed)} printed on a settlement card"


# --------------------------------------------------------------------------- #
# AC-14  an ASR that cannot answer degrades to the cached transcript, badged
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mode", ["offline-ci", "cached-demo", "safe-demo"])
def test_every_mode_that_may_not_talk_to_a_provider_gets_the_cached_asr(mode):
    from backend.app.server import make_asr

    asr = make_asr(mode)
    assert asr.source == "cached", "a badge that lies about the transcript is a fabricated source"
    res = asr.transcribe(b"", ["Vikram Naidu"], hint_id="u04")
    assert res.source == "cached" and res.text.strip()


def test_the_cached_transcript_is_badged_on_the_event_the_ui_reads(feed, rows, tmp_path):
    from backend.adapters.asr import CachedASR

    events: list[dict] = []
    sess = _run_one(feed, rows, DeadProvider(), tmp_path, "u04",
                    asr=CachedASR(), events=events)
    utter = [e for e in events if e["type"] == "utterance"]
    assert utter and utter[-1]["badges"]["asr"] == "cached"
    assert sess.badges()["asr"] == "cached"
    assert sess.history[-1].asr_source == "cached"


def test_a_live_asr_is_only_ever_built_for_the_live_mode(monkeypatch):
    from backend.app.server import make_asr
    from backend.adapters.asr import GeminiASR

    # The key is injected here on purpose. This allocation rule is about *which transport a mode
    # picks*, not about whether this checkout happens to hold a key: asserting it against a
    # developer's .env made the suite fail on a fresh unzip, which is exactly the reviewer who
    # must never see a red test.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.delenv("ASR", raising=False)
    assert isinstance(make_asr("live-demo"), GeminiASR)
    assert not any(isinstance(make_asr(m), GeminiASR)
                   for m in ("offline-ci", "cached-demo", "safe-demo"))
    # And with no key at all, even the live mode must fall back to the recorded transcripts
    # rather than pretend it can transcribe.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not isinstance(make_asr("live-demo"), GeminiASR)


def test_a_low_confidence_segment_is_abstained_and_never_becomes_evidence(feed, rows, tmp_path):
    events: list[dict] = []
    sess = _run_one(feed, rows, DeadProvider(), tmp_path, "u03", events=events)
    assert sess.cards == [], "a transcript the ASR is unsure of must not become a card"
    abstain = _kinds(sess, "asr_abstain")
    assert len(abstain) == 1 and abstain[0]["utterance_id"] == "u03"
    assert abstain[0]["confidence"] < sess.cfg.asr_conf_floor
    assert [e for e in events if e["type"] == "abstain"]
    assert "u03" in sess.not_evidence
    hits = sess.transcript_search("stadium hawa chhaka", 4)
    assert all(h["id"] != "u03" for h in hits), \
        "retrieval must not undo the abstention the kernel already made"


# --------------------------------------------------------------------------- #
# AC-15  the HTTP contract: journal, answers, bytes
# --------------------------------------------------------------------------- #


def _wait_for(client, sid: str, predicate, timeout: float = 45.0) -> dict:
    """Poll the state endpoint rather than the stream: the stream is the thing under test."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/session/{sid}").json()
        if predicate(state):
            return state
        time.sleep(0.1)
    raise AssertionError(f"condition not met within {timeout}s: {state}")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The real app, with every knob that could reach the network or the repo turned off."""
    monkeypatch.setenv("RUN_MODE", "offline-ci")
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
    monkeypatch.setenv("SSE_FOLLOW_AFTER_DONE", "0")
    monkeypatch.setenv("ASK_TIMEOUT_S", "25")
    monkeypatch.setenv("LLM_CASSETTE_DIR", str(ROOT / "tests" / "cassettes"))
    from backend.app import server

    # Traces are the reviewer's evidence; a test run must not leave them in backend/out.
    monkeypatch.setattr(server, "OUT", tmp_path)
    server.STORE._sessions.clear()
    from fastapi.testclient import TestClient

    with TestClient(server.app) as tc:
        yield tc
    server.STORE._sessions.clear()


def _start(client, pack="gap", scenario="S1", mode="watch", run_mode="offline-ci",
           auto_answer=None, query=None) -> str:
    body = {"pack": pack, "scenario": scenario, "mode": mode, "run_mode": run_mode}
    if auto_answer is not None:
        body["auto_answer"] = auto_answer
    if query:
        body["query"] = query
    r = client.post("/api/session", json=body)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_health_names_the_run_mode_the_backend_and_the_cassette_count(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True
    assert h["run_mode"] == "offline-ci"
    assert h["llm_backend"] == "openrouter"
    assert h["cassettes"] > 0, "a reviewer must be able to see the replay corpus is real"
    assert h["asr"] == "cached", "any mode other than live-demo must admit ASR is cached"


def test_a_bare_post_resolves_the_scenario_per_pack(client):
    """`scenario` has no pack-neutral default, and a fixed one is a 400 for the other pack.

    The gap pack's entry point is S1; the creator pack's is the whole innings. A reviewer or a
    thin client that posts only {"pack": "creator"} must get a running session, not an error that
    names a gap id.
    """
    gap = client.post("/api/session", json={"mode": "watch"}).json()
    assert gap["scenario"] == "S1"
    creator = client.post("/api/session", json={"pack": "creator", "mode": "watch"}).json()
    assert creator["scenario"] == "ALL", creator
    bad = client.post("/api/session", json={"pack": "gap", "scenario": "S9"})
    assert bad.status_code == 400 and "S1" in bad.json()["known"]


@pytest.mark.slow
def test_a_late_subscriber_gets_the_whole_journal_from_the_beginning(client):
    sid = _start(client)
    _wait_for(client, sid, lambda s: s.get("done"))
    body = client.get(f"/api/session/{sid}/events").text
    types = [line.split("event: ")[1].strip() for line in body.splitlines()
             if line.startswith("event: ")]
    seqs = [int(line.split("id: ")[1]) for line in body.splitlines() if line.startswith("id: ")]
    state = client.get(f"/api/session/{sid}").json()
    assert seqs[0] == 1, "a subscriber that arrives late must still see the first frame"
    assert seqs == list(range(1, len(seqs) + 1)), "seq is the cursor the client resumes from"
    assert len(seqs) == state["frames"], "the replay must be the whole journal, not a tail"
    assert types[0] == "session" and "episode_start" in types
    assert "card" in types and types[-1] == "done"
    assert types.count("card") == 1, "one return, one receipt - over the wire too"
    assert state["cards"][0]["body"]


@pytest.mark.slow
def test_the_fans_answer_over_http_resolves_the_pending_ask(client):
    from backend.packs.gap.domain import GapFixtures

    answer = GapFixtures.load().scenarios["S1"].fan_answers["did you see"]
    sid = _start(client, run_mode="cached-demo", auto_answer=False)
    state = _wait_for(client, sid, lambda s: bool(s.get("pending_question")))
    assert state["pending_question"]["options"], "the ask must carry the boundary candidates"
    time.sleep(0.3)                      # the worker clears its event after publishing the ask
    r = client.post(f"/api/session/{sid}/answer", json={"text": answer})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    done = _wait_for(client, sid, lambda s: s.get("done"))
    assert len(done["cards"]) == 1, f"the fan answered and still got {len(done['cards'])} receipts"
    trace = client.get(f"/api/session/{sid}/trace").json()
    kinds = [e["kind"] for e in trace["events"]]
    assert "answer" in kinds or "boundary_from_fan" in kinds, json.dumps(kinds)
    assert trace["boundary"]["source"] == "fan", "the answer must move the boundary it was asked about"
    assert [a["text"] for a in trace["answers"]] == [answer]


@pytest.mark.slow
def test_an_answer_for_a_session_that_is_not_asking_is_refused_not_invented(client, monkeypatch):
    sid = _start(client)                  # offline-ci auto-answers; no ask is ever pending
    _wait_for(client, sid, lambda s: s.get("done"))
    r = client.post(f"/api/session/{sid}/answer", json={})
    assert r.status_code == 400, r.text
    assert client.post(f"/api/session/nope/answer", json={"text": "x"}).status_code == 404


@pytest.mark.slow
def test_quiet_mode_never_pays_a_byte_and_watch_pays_exactly_the_clip(client):
    from backend.app import server

    price_kb = 12 * 200                  # CLIP_SECONDS * BYTES_PER_SECOND_KB, the pack's own quote
    quiet_sid = _start(client, mode="quiet")
    _wait_for(client, quiet_sid, lambda s: s.get("done"))
    r = client.get("/api/media/clip/evt_068", params={"sid": quiet_sid})
    assert r.status_code == 403, r.text
    assert "quiet" in r.json()["why"]
    assert r.json()["charged_kb"] == 0.0 and r.json()["would_have_kb"] == pytest.approx(price_kb)
    quiet_web = server.STORE.get(quiet_sid)
    bytes_frames = [f for f in quiet_web.journal.frames if f["type"] == "bytes"]
    assert bytes_frames, "a refusal the counter does not show is a refusal that did not happen"
    assert bytes_frames[-1]["data"]["bytes_sent"] == 0
    assert client.get(f"/api/session/{quiet_sid}/bytes").json()["clip_loads"] == 0

    watch_sid = _start(client, mode="watch")
    _wait_for(client, watch_sid, lambda s: s.get("done"))
    w = client.get("/api/media/clip/evt_068", params={"sid": watch_sid})
    assert w.status_code == 200, w.text
    assert len(w.content) == price_kb * 1024, "the meter counts the bytes that actually left"
    assert w.headers["X-Clip-Kb"] == str(float(price_kb))
    snap = client.get(f"/api/session/{watch_sid}/bytes").json()
    assert snap["clip_loads"] == 1 and snap["kb"] == pytest.approx(price_kb, abs=0.2)
    watch_web = server.STORE.get(watch_sid)
    loaded = [f for f in watch_web.journal.frames if f["type"] == "bytes"
              and f["data"]["bytes_sent"] > 0]
    assert loaded, "the fan's own tap must appear in the fan's own journal"


@pytest.mark.slow
def test_the_media_route_enforces_the_resume_cursor(client):
    sid = _start(client, mode="watch")
    _wait_for(client, sid, lambda s: s.get("done"))
    past = client.get("/api/media/clip/evt_071", params={"sid": sid})
    assert past.status_code == 403
    assert "spoiler" in past.json()["why"], past.json()
    unknown = client.get("/api/media/clip/evt_999", params={"sid": sid})
    assert unknown.status_code == 404
    no_session = client.get("/api/media/clip/evt_068")
    assert no_session.status_code == 403, "bytes must be attributed to a session or not moved"


@pytest.mark.slow
def test_the_stream_carries_the_cards_the_run_committed_for_the_creator_pack(client):
    sid = _start(client, pack="creator", scenario="ALL", mode="quiet")
    _wait_for(client, sid, lambda s: s.get("done"), timeout=90.0)
    body = client.get(f"/api/session/{sid}/events").text
    types = [line.split("event: ")[1].strip() for line in body.splitlines()
             if line.startswith("event: ")]
    state = client.get(f"/api/session/{sid}").json()
    assert "episode_start" in types and types[-1] == "done"
    assert types.count("card") == len(state["cards"]) == len(
        [c for c in state["cards"] if c]), "a card the journal does not carry is a card the fan never saw"
    labels = {c["truth_label"] for c in state["cards"]}
    assert any("CONFLICT" in l for l in labels), json.dumps(sorted(labels))
    assert state["badges"]["asr"] == "cached"
