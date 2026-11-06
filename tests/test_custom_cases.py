"""The unscripted paths: a case nobody recorded, and the model pool that answers it.

These tests exist because the first build was fixture-deterministic - three scenarios, three
cards, and no answer at all to "what if a fan's situation is not one of mine?". Every claim here
is checked against the same fixture objects the demo uses, so a change that quietly re-introduces
a hard-coded case breaks a test instead of a demo.
"""
from __future__ import annotations

import pathlib

import pytest

from backend.app.custom import INTERRUPTIONS, ProvidedASR, creator_custom, gap_custom
from backend.packs.creator.predictions import PredictionExtraction, complete_from_text
from backend.packs.gap.domain import GapFixtures
from backend.kernel.llm import LLMError, UnavailableLLM
from backend.kernel.router import MultiLLM


@pytest.fixture(scope="module")
def fixtures() -> GapFixtures:
    return GapFixtures.load()


# --------------------------------------------------------------------- gap customs


@pytest.mark.parametrize("ball", ["evt_060", "evt_070", "evt_072", "evt_075", "evt_077"])
@pytest.mark.parametrize("interruption", sorted(INTERRUPTIONS))
def test_every_ball_and_interruption_builds_a_real_scenario(fixtures, ball, interruption):
    from backend.packs.gap import boundary as B

    sc = gap_custom(fixtures, ball, interruption)
    assert sc.id.startswith("custom")
    assert sc.resume_cursor == ball
    est = B.estimate(fixtures.feed, sc)
    # The window must never reach past the ball the fan came back on: that is the spoiler rule,
    # and it has to hold for situations nobody scripted too.
    resume_idx = fixtures.feed.index[ball]
    for cand in est.candidates:
        assert fixtures.feed.index[cand] < resume_idx or cand == ball
    assert sc.fan_answers, "a custom gap must be able to answer a question"


def test_unknown_ball_is_refused_by_name(fixtures):
    with pytest.raises(ValueError) as exc:
        gap_custom(fixtures, "evt_zzz", "ad")
    assert "evt_zzz" in str(exc.value)


def test_late_join_puts_the_window_in_the_fans_hands(fixtures):
    from backend.packs.gap import boundary as B

    sc = gap_custom(fixtures, "evt_070", "late")
    est = B.estimate(fixtures.feed, sc)
    # A late join has no playback before the resume ball, so the telemetry cannot localise the
    # boundary: the confidence has to say so rather than claim a clean position.
    assert est.confidence == 0.0
    assert est.needs_fan_input is True, "only the fan can scope a window with no playback"
    assert "only the fan" in " ".join(est.notes)


def test_no_interruption_label_ever_leaks_the_answer(fixtures):
    sc = gap_custom(fixtures, "evt_072", "ad")
    blob = f"{sc.blurb} {sc.name}".lower()
    for leak in ("142/4", "146/4", "147/5"):
        assert leak not in blob


# ----------------------------------------------------------------- creator customs


def test_creator_custom_carries_the_typed_text_through_triage():
    row = creator_custom("Aarav Sen agle over mein ek six maar dega", t_start=60.0)
    assert row["provided_text"].startswith("Aarav Sen")
    # The row is what makes the pack build a ProvidedASR: the text cannot be reported as a
    # transcription, because nothing was transcribed.
    assert row["wav"] == "" and row["speaker"] == "creator"


def test_provided_asr_reports_typed_not_transcribed():
    rows = [creator_custom("Koi na, mazaa aa raha hai", t_start=10.0)]
    asr = ProvidedASR(rows)
    res = asr.transcribe(b"\x00", [], hint_id=rows[0]["id"])
    assert res.text.startswith("Koi na")
    assert res.source == "typed"


# ------------------------------------------------------- prediction completion path


@pytest.mark.parametrize(
    "text,metric,threshold",
    [
        ("Aarav Sen agle over mein ek six maar dega", "boundary_count", 1),
        ("do wicket girengi is over mein", "wicket", 2),
        ("he will hit a boundary soon", "boundary_count", 1),
        ("thoda saaf khelo, koi prediction nahi", None, None),
    ],
)
def test_prediction_completion_reads_only_what_was_said(text, metric, threshold):
    ex, filled = complete_from_text(PredictionExtraction(), text)
    assert ex.metric == metric
    if metric is None:
        assert filled == [], "nothing stated means nothing filled - the ledger must refuse it"
    else:
        assert ex.threshold == threshold


# ------------------------------------------------------------------ the model pool


class _Member:
    """A stand-in transport: no network, deterministic failures, records what it was asked."""

    backend = "test"

    def __init__(self, model: str, *, dead: bool = False):
        self.model = model
        self.dead = dead
        self.calls = 0
        self.live = True

    def complete(self, prompt, system="", schema=None, temperature=0.0,
                 max_output_tokens=1400, tag="", key_text=""):
        self.calls += 1
        if self.dead:
            raise LLMError("429", cassette_miss=False)
        return "ok", {"model": self.model}


def test_pool_rotates_across_healthy_members():
    a, b = _Member("model-a"), _Member("model-b")
    pool = MultiLLM([a, b], cooldown_s=0)
    for _ in range(4):
        pool.complete("hello")
    assert a.calls == 2 and b.calls == 2, "rotation must not pin every turn to one model"
    assert set(pool.served) == {"model-a", "model-b"}


def test_a_dead_member_is_skipped_after_one_failure():
    dead, alive = _Member("dead", dead=True), _Member("alive")
    pool = MultiLLM([dead, alive], cooldown_s=600)
    pool.complete("one")
    pool.complete("two")
    assert dead.calls == 1, "a cooling-down member must not be paid for twice"
    assert pool.route_status()["cooling_down"] == ["test:dead"]


def test_pool_reports_the_model_that_actually_served():
    pool = MultiLLM([_Member("a"), _Member("b")], cooldown_s=0)
    pool.complete("x")
    status = pool.route_status()
    assert status["served_last"] in {"a", "b"}
    assert status["served_models"] == [status["served_last"]]


def test_all_members_down_raises_instead_of_inventing_words():
    pool = MultiLLM([_Member("a", dead=True), _Member("b", dead=True)], cooldown_s=0)
    with pytest.raises(LLMError):
        pool.complete("x")
    assert pool.route_status()["served_models"] == [], "nothing served means nothing claimed"


def test_the_off_layer_never_returns_model_words():
    with pytest.raises(LLMError):
        UnavailableLLM().complete("x")


# ------------------------------------------------- spoken score shapes (live-found gap)


@pytest.mark.parametrize(
    "text,runs,wickets",
    [
        ("142/2", 142, 2),
        ("142 for 2, yeh match abhi bhi khula hai", 142, 2),
        ("142-2", 142, 2),
        ("score 142 for 2 hai", 142, 2),
        ("142 pe 2", 142, 2),
        ("142 runs for 2 wickets", 142, 2),
    ],
)
def test_every_way_a_hinglish_creator_says_a_score_is_detected(text, runs, wickets):
    """Found by testing a real line: "142 for 2" was invisible to a slash-only parser.

    If the detector misses the shape, the supervisor is free to call the line an opinion and
    skip it - which is how a wrong score reached the fan unchallenged in a live run.
    """
    from backend.packs.creator.verify import spoken_score_claim

    claim = spoken_score_claim(text)
    assert claim is not None, text
    assert (claim.runs, claim.wickets) == (runs, wickets)


@pytest.mark.parametrize(
    "text",
    ["kal mausam bahut accha tha", "3 for 2 in the third over", "1420 for 2", "over 17 mein 3 chahiye"],
)
def test_a_non_score_number_is_not_read_as_a_score(text):
    from backend.packs.creator.verify import spoken_score_claim

    assert spoken_score_claim(text) is None


# ------------------------------------------------------ router resilience (live-found gap)


def test_one_failure_does_not_park_the_only_healthy_model_for_the_whole_cooldown():
    pool = MultiLLM([_Member("flaky", dead=True)], cooldown_s=240)
    with pytest.raises(LLMError):
        pool.complete("x")
    # Graded cooldown: the first failure must not cost the full window.
    until = pool._dead_until[0]
    assert until - __import__("time").time() <= 60, "first failure cooled the member too long"


def test_a_cooling_member_still_gets_one_last_chance():
    """The live failure this fixes: `pool exhausted` was raised while a model answered in 0.4 s."""

    class _Recovers(_Member):
        def complete(self, *a, **kw):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("429")
            return "ok", {"model": self.model}

    m = _Recovers("recovers")
    pool = MultiLLM([m], cooldown_s=240)
    with pytest.raises(LLMError):
        pool.complete("first")            # the only member fails once and cools down
    out, meta = pool.complete("second")   # last chance, not a dead episode
    assert out == "ok"
    assert meta["served_by"] == "recovers"
    assert pool.route_status()["last_chance_used"] is True


# ------------------------------------------ fallback receipt cites the boundary ball


def test_a_justification_naming_a_real_ball_is_cited_not_rejected():
    """Live failure this fixes: the deterministic receipt was refused because the model's
    sentence cited the boundary ball, so the fan got no receipt at all."""
    from backend.kernel.llm import UnavailableLLM
    from backend.packs.gap import boundary as B
    from backend.packs.gap.domain import GapFixtures as GF
    from backend.packs.gap.session import GapSession

    class Trace:
        def record(self, *a, **k):
            pass

    fx = GF.load()
    sc = gap_custom(fx, "evt_071", "ad")
    fx2 = GF(fx.feed, fx.frames, list(fx.scenarios.values()) + [sc])
    sess = GapSession(UnavailableLLM(), fx2, trace=Trace(), on_event=lambda e: None,
                      auto_answer=dict(sc.fan_answers))
    sess.scenario = sc
    sess.boundary = B.estimate(fx.feed, sc)
    sess.chosen_boundary = sess.boundary.candidates[0]
    sess.boundary_source = "fan"
    for attr, val in dict(released_events=set(), frame_checks={}, clip_quotes={}, tallies={},
                          verdicts={}, evidence_notes={}, fan_pref="everything").items():
        setattr(sess, attr, val)
    sess._scratch = {"is_gap": True, "scenario_id": sc.id,
                     "strategist": {"why_it_matters":
                                    "the wicket at [evt_068] changes the chase [evt_069]"}}
    out = sess._commit_draft(sess.build_card("receipt", why="", clip_event=""))
    assert out.ok, [v.rule for v in out.violations]
    assert "evt_068" in out.card.cited_event_ids


def test_a_justification_naming_the_resume_ball_is_still_refused():
    from backend.packs.gap.guardrails import validate_receipt

    fx = GapFixtures.load()
    bad = validate_receipt(["evt_069"], "evt_071", fx.feed,
                           why="the run out at [evt_071] ended it", end_over="16.6")
    assert any(v.rule in {"spoiler", "cites_uncited_event", "beyond_resume"} for v in bad)


# ------------------------------------------------------------- quota-aware cooldown


def test_a_daily_quota_error_parks_the_member_for_hours_not_minutes():
    from backend.kernel.router import _quota_wait

    wait = _quota_wait("429 RESOURCE_EXHAUSTED. requests per day limit exceeded on Free Tier. "
                       "Please retry in 17h2m25.719157536s.")
    assert wait > 3600 * 17, "a spent daily quota must not be retried every few minutes"
    assert _quota_wait("Rate limit exceeded: free-models-per-day") >= 600
    assert _quota_wait("connection reset by peer") == 0


def test_capacity_says_quota_exhausted_rather_than_cooling_down():
    class _Quota(_Member):
        def complete(self, *a, **kw):
            self.calls += 1
            raise LLMError("429 RESOURCE_EXHAUSTED. requests per day limit exceeded. "
                           "Please retry in 5h0m0s.")

    pool = MultiLLM([_Quota("spent")], cooldown_s=240)
    with pytest.raises(LLMError):
        pool.complete("x")
    assert pool.capacity() == "quota_exhausted"
    assert pool.route_status()["quota_until"], "the page must be able to say when it recovers"


# --------------------------------------------------------------------------- #
# the guardrails promise a card, and must actually deliver one
# --------------------------------------------------------------------------- #
def test_a_critic_that_refuses_twice_still_leaves_the_fan_with_a_receipt():
    """Live failure this fixes: a real S1 run ended `stopped_noop` with **no card at all**.

    The supervisor kept committing a receipt whose justification cited a ball past the fan's
    cursor; the critic refused it twice (correctly), the guardrail fired, and the episode
    ended. The trace promised "the pack's deterministic card will be used" and then committed
    nothing, because a fallback is only owed to the fan when the guardrail says so.
    """
    from backend.kernel.schemas import Budget
    from backend.kernel.trace import TraceRecorder
    from backend.packs.gap.domain import GapFixtures as GF
    from backend.packs.gap.session import GapConfig, GapSession

    class StubbornCriticLLM:
        """Commits the same card forever; the critic never approves it."""

        mode_used = "scripted"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt, system="", schema=None, tag="", **kw):
            self.calls += 1
            import json as _json
            payload = ({"kind": "final", "thought": "reject",
                        "result_json": _json.dumps({"approve": False,
                                                    "reason": "citations are wrong",
                                                    "notes": ["fix it"]})}
                       if "critic" in tag else
                       {"action": "commit", "thought": "commit the receipt",
                        "agent": None, "tool": None, "reason": "",
                        "card": {"kind": "receipt", "headline": "You missed the window",
                                 "body": "- 16.3 WICKET: out (142/3 -> 142/4)",
                                 "cited_event_ids": ["evt_068"], "salience": 1.0}})
            # The real adapters hand back a validated model instance, so the stub must too.
            out = schema.model_validate(payload) if schema is not None else payload
            return out, {"usage": {}, "model": "scripted"}

    fx = GF.load()
    trace = TraceRecorder(None)
    sess = GapSession(StubbornCriticLLM(), fx, trace, lambda e: None,
                      cfg=GapConfig(budget=Budget(max_llm_calls=8, max_tool_calls=4,
                                                  max_ms=60_000, max_specialist_rounds=4),
                                    ask_timeout_s=1.0),
                      auto_answer={"did you see": "No, I left at 16.2, recap everything"})
    ep = sess.run_scenario("S1")
    rules = [e.get("rule") for e in trace.events if e["kind"] == "guardrail"]
    assert "critic_loop_bound" in rules, rules
    assert sess.cards, f"the guardrail promised a card and left the fan with none ({ep.terminal.value})"
    card = sess.cards[0]
    assert card.kind in ("receipt", "noop")
    resume = fx.feed.by_id[sess.scenario.resume_cursor]
    assert resume.id not in card.cited_event_ids


def test_the_fallback_card_drops_the_prose_that_could_not_be_checked():
    """The deterministic card is code's, so it never carries the model's unchecked sentence."""
    from backend.kernel.llm import UnavailableLLM
    from backend.packs.gap.domain import GapFixtures as GF
    from backend.packs.gap.session import GapConfig, GapSession
    from backend.kernel.schemas import Budget
    from backend.kernel.trace import TraceRecorder

    fx = GF.load()
    sess = GapSession(UnavailableLLM(), fx, TraceRecorder(None), lambda e: None,
                      cfg=GapConfig(budget=Budget(max_llm_calls=2, max_tool_calls=2,
                                                  max_ms=30_000, max_specialist_rounds=1),
                                    ask_timeout_s=1.0),
                      auto_answer={"did you see": "No, I left at 16.2"})
    sess.run_scenario("S1")
    draft = sess._fallback_draft({"is_gap": True})
    assert draft is not None
    assert "Why it matters" not in draft.body


def test_the_pool_does_not_claim_no_capacity_while_it_has_capacity():
    """"the model pool had no capacity for this step (available)" shipped once - a contradiction.

    A 429 from one member while another is healthy is a rate limit, and the page has to say
    that; "no capacity" is reserved for a pool that reports itself exhausted or cooling.
    """
    import re
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "backend" / "app" / "server.py").read_text()
    assert '"no capacity for this step"' in src or "no capacity for this step" in src
    # The qualifier is only reachable when the pool reports exhaustion or cooldown.
    assert 'cap in ("quota_exhausted", "cooling_down")' in src
    assert re.search(r'rate-limited', src)


def test_a_poisoned_recording_is_not_deleted_before_its_replacement_exists():
    """A delete-then-re-record can delete the only copy: it did, on a spent daily quota.

    The rule now: an entry that fails to parse is flagged for replacement and overwritten by
    the next successful recording of the same key. If the re-record fails (no quota, no
    network), the original file is still there, so the store does not silently lose a turn.
    """
    import json, pathlib, tempfile
    from backend.kernel.llm import Cassette
    from backend.kernel.schemas import Budget  # noqa: F401  (schema import sanity)

    with tempfile.TemporaryDirectory() as d:
        c = Cassette(pathlib.Path(d), "replay")
        key = c.key("sys", "prompt", "{}", "m", 0.0)
        c.path(key).write_text(json.dumps({"text": "{not json", "model": "recorded"}))
        assert c.path(key).exists()
        # A replay that cannot parse must not destroy the file it cannot use.
        from backend.kernel.openrouter import OpenRouterLLM
        from pydantic import BaseModel
        from typing import Optional

        class Out(BaseModel):
            ok: Optional[bool] = None

        llm = OpenRouterLLM(model="m", live=False, cassette_mode="replay",
                            cassette_dir=str(d))
        try:
            llm.complete("prompt", system="sys", schema=Out, tag="t")
        except Exception:
            pass
        assert c.path(key).exists(), "the un-replayable recording was deleted before replacement"


# ------------------------------------------------------- jury pane isolation


def test_a_jury_pane_heartbeat_never_reaches_a_later_session(fixtures, tmp_path):
    """POST /api/session/{sid}/interrupt appends to `scenario.heartbeats`.

    The fixture's scenario objects are shared by every session on the server, so if the
    append lands on the shared object it follows this fan out of the room: every later run
    of S1 would read a stall nobody sat through, its supervisor prompts would drift off the
    recorded cassette keys, and the demo would silently fall back for everyone afterwards.
    Each session must bind its own copy, leaving the fixture byte-identical.
    """
    from backend.kernel.trace import TraceRecorder
    from backend.packs.gap.domain import HeartbeatRow
    from backend.packs.gap.session import GapConfig, GapSession

    pristine = list(fixtures.scenarios["S1"].heartbeats)

    def fresh(name: str) -> GapSession:
        sess = GapSession(UnavailableLLM(), fixtures,
                          TraceRecorder(tmp_path / f"{name}.jsonl"), lambda e: None,
                          cfg=GapConfig(),
                          auto_answer=dict(fixtures.scenarios["S1"].fan_answers))
        sess.run_scenario("S1")
        return sess

    first = fresh("a")
    # exactly what the interrupt endpoint does, on this session's own scenario object:
    first.scenario.heartbeats.append(
        HeartbeatRow(t=0.01, event_id="evt_070", kind="stall", note="jury pane"))
    assert len(first.scenario.heartbeats) == len(pristine) + 1, "the session sees its own"

    # the fixture every future session reads is untouched...
    assert fixtures.scenarios["S1"].heartbeats == pristine
    # ...so the next fan's telemetry - and therefore its cassette key - is byte-identical.
    second = fresh("b")
    assert second.scenario.heartbeats == pristine


# ------------------------------------------------- provenance honesty (the note)


def test_the_failure_note_replaces_the_optimistic_layer_note():
    """A custom case starts on `layer=live` with the note "the live model pool was used".

    If the pool then dies on a 429, that sentence is false - and the run ships a
    deterministic fallback card. The truthful failure note must REPLACE the layer note
    (the old `not self.llm_note` guard let the optimistic note win and the UI read
    "answered by live models" over a badge that said `unavailable`), and it must be
    said once rather than re-opened by every later episode in the session.
    """
    from backend.app.server import WebSession

    web = WebSession(id="s_note", pack="gap", scenario_id="S1", mode="watch",
                     run_mode="cached-demo", llm=None, session=None)
    web.llm_note = "custom case: no recording exists, so the live model pool was used"

    web.on_trace({"kind": "supervisor_error",
                  "error": "pool exhausted for tag=supervisor: openrouter:x -> "
                           "429 RESOURCE_EXHAUSTED"})

    assert "live model pool was used" not in web.llm_note, "a dead pool may not claim it was used"
    assert "rate-limited" in web.llm_note
    assert "kernel answered from the feed" in web.llm_note

    first = web.llm_note
    web.on_trace({"kind": "supervisor_error", "error": "another 429"})
    assert web.llm_note == first, "the reason is said once"
