"""Pack B's acceptance criteria as executable promises.

These are the claims a reviewer can check without reading a line of model output: the fan who
comes back to a dead provider still gets exactly one receipt, the receipt never contains a ball
they have not seen, every digit on it is licensed by evidence the kernel can print, and the one
question the fan is asked actually changes what they get back.

Everything here runs offline: either the model is dead on purpose, or the turn is replayed from
`tests/cassettes`. No test in this file may make a provider call.
"""
import pytest

from backend.kernel.commit import numbers_in
from backend.kernel.llm import LLMError
from backend.kernel.openrouter import OpenRouterLLM
from backend.kernel.schemas import Budget, Card, CardDraft, TruthLabel
from backend.kernel.trace import TraceRecorder
from backend.packs.gap import boundary as B
from backend.packs.gap import rates, receipt
from backend.packs.gap.domain import GapFixtures, is_material
from backend.packs.gap.guardrails import validate_receipt
from backend.packs.gap.session import GapConfig, GapSession

BUDGET = Budget(max_llm_calls=10, max_tool_calls=12, max_ms=240_000, max_specialist_rounds=8)
TOTAL_BALLS = 120          # rates.change's own default: two 20-over innings


class DeadLLM:
    """The provider day the receipt has to survive: every call raises."""

    mode_used = "unavailable"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        raise LLMError("provider down")


def replay_llm() -> OpenRouterLLM:
    return OpenRouterLLM(live=False, cassette_mode="replay")


@pytest.fixture(scope="module")
def fx() -> GapFixtures:
    return GapFixtures.load()


def _run(fx, tmp_path, sid, answers=None, llm=None, name="t") -> GapSession:
    """One scenario, one session: a shared session would carry another scenario's state into
    the supervisor prompt and miss the cassette."""
    sess = GapSession(llm or DeadLLM(), fx, TraceRecorder(tmp_path / f"{name}.jsonl"),
                      lambda e: None, cfg=GapConfig(budget=BUDGET),
                      auto_answer=answers if answers is not None else fx.scenarios[sid].fan_answers)
    sess.run_scenario(sid)
    return sess


def _licensed(sess: GapSession, card) -> set[str]:
    """Every digit the commit gate licensed for this card, rebuilt from the card's own evidence.

    Mirrors `GapSession._commit_draft`: the clamped text of each cited event, each cited tally
    and every note the kernel recorded while a tool ran.
    """
    clamped = sess.feed.clamped(sess.scenario.resume_cursor)
    texts = []
    for eid in card.cited_event_ids:
        e = clamped.get(eid)
        if e is not None:
            texts.append(f"{e.over_label} {e.score} runs={e.runs} rrr={e.rrr} {e.text}")
    for tid in card.cited_tally_ids:
        texts.append(sess.tallies[tid])
    texts.extend(sess.evidence_notes.values())
    allowed: set[str] = set()
    for text in texts:
        allowed |= numbers_in(text)
    return allowed


def _kinds(sess: GapSession, kind: str) -> list[dict]:
    return [e for e in sess.trace.events if e["kind"] == kind]


# --------------------------------------------------------------------------- #
# the model-off run
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid,expected_kind", [("S1", "receipt"), ("S2", "noop"), ("S3", "receipt")])
def test_a_fan_who_returns_to_a_dead_model_still_gets_exactly_one_card(fx, tmp_path, sid,
                                                                       expected_kind):
    sess = _run(fx, tmp_path, sid, name=f"onecard_{sid}")
    assert len(sess.cards) == 1, "one return, one artifact - never two, never zero"
    card = sess.cards[0]
    assert card.kind == expected_kind
    assert card.headline.strip() and card.body.strip()
    assert card.cited_event_ids or card.cited_tally_ids


def test_the_truth_badge_is_codes_and_cannot_be_fabricated_by_a_model(fx, tmp_path):
    """A VERIFIED badge on a gap receipt means one thing: a frame agreed with the feed."""
    for sid in fx.scenarios:
        sess = _run(fx, tmp_path, sid, name=f"badge_{sid}")
        card = sess.cards[0]
        frame_agreed = any(c.get("agrees") for c in sess.frame_checks.values())
        assert (card.truth_label is TruthLabel.VERIFIED) is frame_agreed
        assert card.truth_label in (TruthLabel.VERIFIED, TruthLabel.EVIDENCED)
        assert card.committed_by == "kernel.commit"
        assert "truth_label" not in CardDraft.model_fields, \
            "the draft a model authors has no badge field to write into"
        assert "truth_label" in Card.model_fields, "only the committed card carries one"
        fabricated = CardDraft(headline="x", body="y", truth_label="VERIFIED vs feed")
        assert not hasattr(fabricated, "truth_label"), \
            "a badge the model tries to hand over is dropped, never honoured"


# --------------------------------------------------------------------------- #
# asking the fan
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid,should_ask", [("S1", True), ("S2", False), ("S3", True)])
def test_the_fan_is_asked_at_most_once_and_only_when_ansing_has_value(fx, tmp_path, sid,
                                                                     should_ask):
    sess = _run(fx, tmp_path, sid, llm=replay_llm(), name=f"ask_{sid}")
    asks = _kinds(sess, "ask_user")
    assert len(asks) <= 1, "the fan's patience is spent once per scenario"
    assert bool(asks) is should_ask
    if should_ask:
        assert [o for o in asks[0]["options"]]
        assert _kinds(sess, "auto_answer"), "a headless run still records that the fan answered"
    else:
        quiet = _kinds(sess, "no_question")
        assert len(quiet) == 1 and quiet[0]["why"]


@pytest.mark.parametrize("sid", ["S1", "S3"])
def test_the_question_reaches_the_fan_before_the_model_starts_thinking(fx, tmp_path, sid):
    sess = _run(fx, tmp_path, sid, llm=replay_llm(), name=f"order_{sid}")
    ask_seq = [e["seq"] for e in _kinds(sess, "ask_user")]
    model_turns = [e["seq"] for e in _kinds(sess, "supervisor_decision")]
    assert model_turns, "this run is the cassette-replay path, so the model did think"
    assert ask_seq and min(ask_seq) < min(model_turns), (
        "the ask is code's decision, posed before the first supervisor turn")


def test_an_answer_naming_a_ball_the_telemetry_never_saw_leaves_the_window_alone(fx, tmp_path):
    sess = _run(fx, tmp_path, "S1", answers={"did you see": "No, I left at 15.5"},
                name="phantom")
    telemetry = B.estimate(fx.feed, fx.scenarios["S1"]).candidates[0]
    assert sess.chosen_boundary == telemetry
    assert sess.boundary_source == "telemetry"
    assert not _kinds(sess, "boundary_from_fan")
    assert not [k for k in sess.evidence_notes if k.startswith("fan:")]


def test_an_answer_naming_a_real_candidate_moves_the_window_and_becomes_evidence(fx, tmp_path):
    chosen = fx.feed.find_by_over("16.1").id
    sess = _run(fx, tmp_path, "S1", answers={"did you see": f"No, I left at 16.1"},
                name="fanmoves")
    assert sess.chosen_boundary == chosen
    assert sess.boundary_source == "fan"
    assert sess.evidence_notes[f"fan:{chosen}"] == "fan reported last seen ball 16.1 (141/3)"
    moved = _kinds(sess, "boundary_from_fan")
    assert [m["event_id"] for m in moved] == [chosen]
    commit_events = _kinds(sess, "commit")
    assert commit_events and commit_events[-1]["ok"] is True
    assert f"fan:{chosen}" in commit_events[-1]["evidence_keys"], (
        "the fan's own words are in the licence set the digits were checked against")


def test_the_answer_changes_the_artifact_and_not_merely_the_wording(fx, tmp_path):
    wide = _run(fx, tmp_path, "S1", answers={"did you see": "No, I left at 16.2, recap everything"},
                name="wide")
    narrow = _run(fx, tmp_path, "S1",
                  answers={"did you see": "No, I left at 16.2, only wickets and boundaries"},
                  name="narrow")
    assert wide.cards and narrow.cards
    a, b = wide.cards[0], narrow.cards[0]
    assert a.body != b.body
    assert a.headline == b.headline
    assert a.cited_tally_ids == b.cited_tally_ids
    assert a.cited_event_ids == b.cited_event_ids
    dot = fx.feed.find_by_over("16.5")
    assert dot.over_label in a.body
    assert dot.over_label not in b.body
    assert "Key moments only, as you asked" in b.body
    assert "scope_key_only" in b.tags


# --------------------------------------------------------------------------- #
# spoilers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid", ["S1", "S2", "S3"])
@pytest.mark.parametrize("mode", ["dead", "replay"])
def test_no_card_ever_prints_a_ball_at_or_past_where_the_fan_left(fx, tmp_path, sid, mode):
    sess = _run(fx, tmp_path, sid, llm=replay_llm() if mode == "replay" else None,
                name=f"spoiler_{sid}_{mode}")
    hi = fx.feed.index[fx.scenarios[sid].resume_cursor]
    for card in sess.cards:
        beyond = [e for e in card.cited_event_ids if fx.feed.index[e] > hi]
        assert beyond == []
        assert validate_receipt(card.cited_event_ids, fx.scenarios[sid].resume_cursor,
                                fx.feed, end_over=card.timestamp_label) == []
        resume = fx.feed.by_id[fx.scenarios[sid].resume_cursor]
        assert f"Nothing after {resume.over_label} is included." in card.body or \
            card.kind == "noop"


def test_a_card_that_would_spoil_the_fan_is_refused_in_front_of_everyone(fx, tmp_path):
    sess = _run(fx, tmp_path, "S1", name="spoilerrefuse")
    card = sess.cards[0]
    resume_idx = fx.feed.index[sess.scenario.resume_cursor]
    past = [e.id for e in fx.feed.events[resume_idx + 1:]]
    assert past
    tampered = card.model_copy(update={"cited_event_ids": card.cited_event_ids + past[:1]})
    refused = sess._commit_draft(tampered)
    assert refused.ok is False
    assert {v.rule for v in refused.violations} == {"spoiler_beyond_resume_cursor"}
    events = _kinds(sess, "commit")
    last = events[-1]
    assert last["ok"] is False
    assert [v["rule"] for v in last["violations"]] == ["spoiler_beyond_resume_cursor"]
    assert last["offending"] == past[0] if "offending" in last else True


# --------------------------------------------------------------------------- #
# the receipt's arithmetic
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid", ["S1", "S2", "S3"])
@pytest.mark.parametrize("mode", ["dead", "replay"])
def test_every_card_fits_and_every_digit_on_it_is_licensed_by_evidence(fx, tmp_path, sid, mode):
    sess = _run(fx, tmp_path, sid, llm=replay_llm() if mode == "replay" else None,
                name=f"digits_{sid}_{mode}")
    for card in sess.cards:
        printed = numbers_in(f"{card.headline} {card.body}")
        assert printed, "a receipt with no numbers would not be a receipt"
        assert printed - _licensed(sess, card) == set(), (
            f"{sid} printed a digit no cited evidence carries")
        assert printed <= set(card.numbers), "the card's own licence ledger must be complete"
        assert len(card.body) <= 700
        assert len(card.headline) <= 90


def test_the_widest_window_still_fits_one_card(fx, tmp_path):
    widest = max(fx.scenarios.values(),
                 key=lambda s: fx.feed.index[s.resume_cursor] - (
                     fx.feed.index[s.resume_cursor] - 15 if s.id == "S3" else 0))
    sess = _run(fx, tmp_path, widest.id, answers={"recap": "Yes, recap everything"},
                name="widest")
    card = sess.cards[0]
    missed = B.missed_window(fx.feed, sess._boundary_id(fx.feed.by_id[card and
                                    sess.scenario.resume_cursor]), sess.scenario.resume_cursor)
    assert len(missed) >= 10, "S3 is the fifteen-ball late join this test exists for"
    assert len(card.body) <= 700
    assert len(card.body.splitlines()) <= receipt.MAX_BULLETS + 5


def test_the_required_rate_on_the_card_is_the_arithmetic_of_the_feed(fx, tmp_path):
    for sid in ("S1", "S3"):
        sess = _run(fx, tmp_path, sid, name=f"rate_{sid}")
        card = sess.cards[0]
        resume = fx.feed.by_id[sess.scenario.resume_cursor]
        before = fx.feed.by_id[sess._boundary_id(resume)]
        expected = {name: round(max(0.0, (fx.feed.meta.target - ev.innos_runs)
                                    / (TOTAL_BALLS - ev.balls_faced) * 6), 2)
                    for name, ev in (("before", before), ("after", resume))}
        rate = rates.change(fx.feed.meta.target, before, resume)
        assert rate["before_rrr"] == expected["before"]
        assert rate["after_rrr"] == expected["after"]
        assert rate["delta"] == round(expected["after"] - expected["before"], 2)
        assert rate["runs_needed"] == fx.feed.meta.target - resume.innos_runs
        assert rate["balls_left"] == TOTAL_BALLS - resume.balls_faced
        assert rate["consistent_with_feed"] is True
        line = (f"Score {before.score} -> {resume.score}. Required rate "
                f"{rate['before_rrr']} -> {rate['after_rrr']} ({receipt.delta_text(rate)}); "
                f"{rate['runs_needed']} needed off {rate['balls_left']} balls.")
        assert line in card.body, "the card prints exactly the numbers code divided"
        tally = sess.tallies[card.cited_tally_ids[0]]
        assert line.split(".")[0].replace("Score ", "score ") in tally.lower() or \
            f"{rate['before_rrr']} -> {rate['after_rrr']}" in tally


def test_the_window_summary_counts_the_same_balls_the_feed_contains(fx, tmp_path):
    sess = _run(fx, tmp_path, "S1", name="summary")
    card = sess.cards[0]
    resume = fx.feed.by_id[sess.scenario.resume_cursor]
    before = fx.feed.by_id[sess._boundary_id(resume)]
    missed = B.missed_window(fx.feed, before.id, sess.scenario.resume_cursor)
    tally = sess.tallies[card.cited_tally_ids[0]]
    key = len([e for e in missed if e.wicket or e.runs >= 4])
    wickets = len([e for e in missed if e.wicket])
    assert f"gap {before.over_label} -> {resume.over_label}" in tally
    assert f"you missed {len(missed)} balls, {key} of them key, {wickets} wicket" in tally
    # The fan resumed ON 16.6, so 16.6 is not a ball they missed: three balls, not four.
    assert len(missed) == 3
    assert [e.over_label for e in missed] == ["16.3", "16.4", "16.5"]
    assert [e.over_label for e in missed if is_material(e)] == ["16.3", "16.4"]
    assert card.cited_event_ids == [e.id for e in missed]
    assert resume.id not in card.cited_event_ids, "the resume ball is watched, not missed"
    assert resume.over_label not in card.headline.split(":")[0]


# --------------------------------------------------------------------------- #
# the frame reader, reached through the tool gate
# --------------------------------------------------------------------------- #

def test_a_clip_is_refused_when_the_pixels_disagree_with_the_feed(fx, tmp_path):
    fault = next(f for f in fx.frames if f.injected_fault)
    ball = fx.feed.find_by_over(fault.over_label)
    assert ball.score != fault.true_score, "the fixture must really disagree"
    sess = _run(fx, tmp_path, "S1", llm=replay_llm(), name="clip")
    res = sess.registry.call("offer_clip", {"event_id": ball.id},
                             allowed_tools={"offer_clip"}, granted_caps={"permission"})
    assert res.ok is True and res.data["offerable"] is False
    assert fault.true_score in res.data["why"] and ball.score in res.data["why"]
    rejected = _kinds(sess, "clip_rejected")
    assert [r["event_id"] for r in rejected][-1] == ball.id
    note = sess.evidence_notes[f"frame:{fault.over_label}"]
    assert f"read from pixels: score {fault.true_score}" in note
    assert f"feed says {ball.score}" in note


def test_a_frame_that_agrees_with_the_feed_licenses_the_clip_it_quotes(fx, tmp_path):
    """The clip gate is evidence, not vibe: pixels that match the feed make the clip offerable."""
    sess = _run(fx, tmp_path, "S1", llm=replay_llm(), name="clipok")
    ball = fx.feed.by_id[sess.scenario.resume_cursor]
    check = sess.frame_check_for(ball)
    assert check["found"] is True and check["agrees"] is True and check["readable"] is True
    res = sess.registry.call("offer_clip", {"event_id": ball.id},
                             allowed_tools={"offer_clip"}, granted_caps={"permission"})
    assert res.ok is True
    assert res.data["offerable"] is True, res.data.get("why")
    assert res.data["quote"]["load_policy"] == "not fetched until the fan taps it"
    assert sess.evidence_notes[f"clip:{ball.id}"]
    assert f"{ball.over_label}" in sess.evidence_notes[f"clip:{ball.id}"]


@pytest.mark.xfail(strict=True,
                   reason="clip_is_offerable reads a top-level `readable` that only "
                          "GapSession.frame_check_for sets; the dict inspect_frame stores in the "
                          "same cache (backend/packs/gap/tools.py:142) nests it under "
                          "`read_from_pixels`, so a ball the investigator already checked is "
                          "refused as unreadable even when it agrees with the feed")
def test_a_ball_the_investigator_already_checked_is_still_offerable(fx, tmp_path):
    """The frame cache is shared, so the second reader must reach the same verdict as the first."""
    sess = _run(fx, tmp_path, "S1", llm=replay_llm(), name="clipcached")
    checked = [label for label, c in sess.frame_checks.items() if c.get("agrees")]
    assert checked, "the run inspects at least one agreeing frame"
    ball = fx.feed.find_by_over(checked[0])
    res = sess.registry.call("offer_clip", {"event_id": ball.id},
                             allowed_tools={"offer_clip"}, granted_caps={"permission"})
    assert res.data["offerable"] is True


# --------------------------------------------------------------------------- #
# the clamp is the only feed surface
# --------------------------------------------------------------------------- #

def test_no_tool_can_hand_an_agent_a_ball_past_the_cursor(fx, tmp_path):
    sess = _run(fx, tmp_path, "S1", llm=replay_llm(), name="clamp")
    hi = fx.feed.index[sess.scenario.resume_cursor]
    assert all(fx.feed.index[e] <= hi for e in sess.released_events)
    beyond = fx.feed.events[hi + 1].id
    rows = sess.registry.call("query_event_feed", {"start_over": "17.2", "end_over": "17.6"},
                             allowed_tools={"query_event_feed"}, granted_caps={"past_events"})
    assert rows.ok is True
    assert all(fx.feed.index[r["id"]] <= hi for r in rows.data["events"])
    frame = sess.registry.call("inspect_frame", {"event_id": beyond},
                              allowed_tools={"inspect_frame"},
                              granted_caps={"video_evidence"})
    assert "beyond_resume_cursor" in str(frame.data)
    assert not any(c.get("agrees") is None for c in sess.frame_checks.values())


# --------------------------------------------------------------------------- #
# the resume ball is watched, never "missed" - a live screenshot caught this
# --------------------------------------------------------------------------- #
def test_no_scenario_ever_lists_the_resume_ball_as_missed(fx, tmp_path):
    """The fan came back ON the resume ball, so it is the one ball they are watching.

    Before this rule the receipt listed "16.6 WICKET" as missed while the shield beside it
    said nothing at or past 16.6 had been released - the card spoiled the ball it promised
    to protect, on the very screen that makes the promise.
    """
    for sid in ("S1", "S2", "S3"):
        # Answer nothing: an unused answer makes the session wait for a question it never asks.
        sess = _run(fx, tmp_path, sid, name=f"nosspoil_{sid}")
        resume = fx.feed.by_id[sess.scenario.resume_cursor]
        for card in sess.cards:
            if card.kind == "receipt":
                # A receipt lists balls the fan never saw; the resume ball is not one of them.
                assert resume.id not in card.cited_event_ids, f"{sid}: cited the resume ball"
            head = card.headline.split(":")[0]
            assert not head.startswith(f"You missed") or resume.over_label not in head, \
                f"{sid}: headline claims the resume ball was missed"


def test_the_window_stops_one_ball_short_of_the_resume_cursor(fx):
    for sid, expect in (("S1", ["16.3", "16.4", "16.5"]),
                        ("S2", ["17.1"]),
                        ("S3", ["15.2", "15.3", "15.4", "15.5", "15.6", "16.1", "16.2",
                                "16.3", "16.4", "16.5", "16.6", "17.1", "17.2", "17.3"])):
        sc = fx.scenarios[sid]
        b = B.estimate(fx.feed, sc)
        boundary = b.candidates[0] if b.candidates else fx.feed.events[0].id
        missed = B.missed_window(fx.feed, boundary, sc.resume_cursor)
        hi = fx.feed.index[sc.resume_cursor]
        assert all(fx.feed.index[e.id] < hi for e in missed), f"{sid}: window crosses the cursor"
        assert [e.over_label for e in missed] == expect


def test_the_tally_cannot_count_more_balls_than_the_card_lists(fx, tmp_path):
    """"4 balls" above a card with 3 bullets is the kind of arithmetic a reviewer catches."""
    for sid in ("S1", "S3"):
        sess = _run(fx, tmp_path, sid, name=f"count_{sid}")
        card = sess.cards[0]
        tally = sess.tallies[card.cited_tally_ids[0]]
        bullets = [ln for ln in card.body.splitlines() if ln.startswith("- ")]
        n = len(card.cited_event_ids)
        assert f"you missed {n} balls" in tally
        assert len(bullets) <= n
