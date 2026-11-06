"""Pack B's deterministic layer, tested where the model cannot help.

Every assertion here is about a claim the fan can check: which balls the gap actually
contains, whether the fan was asked when the answer had value, whether a ball past their
position leaked, and whether a clip was refused because the pixels disagreed with the feed.
The model-off runs are the load-bearing ones - a demo where the provider is down still owes
the fan a truthful receipt.
"""
import pytest

from backend.kernel.llm import LLMError, UnavailableLLM
from backend.kernel.schemas import TruthLabel
from backend.kernel.trace import TraceRecorder
from backend.packs.gap import boundary as B
from backend.packs.gap import guardrails, ocr, rates, voi
from backend.packs.gap.domain import GapFixtures
from backend.packs.gap.session import GapConfig, GapSession


class DeadLLM:
    """Every call raises: the provider day that must not cost the fan their receipt."""

    mode_used = "unavailable"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        raise LLMError("provider down")


@pytest.fixture(scope="module")
def fx() -> GapFixtures:
    return GapFixtures.load()


def _session(fx, tmp_path, scenario_id, events=None) -> GapSession:
    sess = GapSession(DeadLLM(), fx, TraceRecorder(tmp_path / "t.jsonl"),
                      events.append if events is not None else (lambda e: None),
                      cfg=GapConfig(budget=_budget()),
                      auto_answer=fx.scenarios[scenario_id].fan_answers)
    return sess


def _run(fx, tmp_path, scenario_id, answers) -> GapSession:
    """The same scenario with a different fan answer, for A/B-ing what asking buys."""
    sess = GapSession(DeadLLM(), fx, TraceRecorder(tmp_path / f"{scenario_id}.jsonl"),
                      lambda e: None, cfg=GapConfig(budget=_budget()), auto_answer=answers)
    sess.run_scenario(scenario_id)
    return sess


def _budget():
    from backend.kernel.schemas import Budget
    return Budget(max_llm_calls=6, max_tool_calls=8, max_ms=60_000, max_specialist_rounds=4)


def _over(fx, label: str) -> str:
    return fx.feed.find_by_over(label).id


# --------------------------------------------------------------------------- #
# boundary: where the fan lost the stream
# --------------------------------------------------------------------------- #

def test_ad_gap_boundary_is_the_last_rendered_ball(fx):
    b = B.estimate(fx.feed, fx.scenarios["S1"])
    assert b.candidates[0] == _over(fx, "16.2")
    assert b.labels(fx.feed) == ["16.2", "16.1", "16.3"]
    assert b.clean_last_position is False
    assert b.confidence == pytest.approx(0.65)          # 0.95 - 0.30 (ad) - 0.00 (has resume)


def test_restart_markers_are_not_watched_balls(fx):
    """The bug this test exists for: reading `ad_end`/`resume` as the last-seen ball.

    Those rows say where the player *restarted*. Trusting them collapses every gap to zero
    balls and turns the whole product into a no-op, so the missed window has to be non-empty.
    """
    sc = fx.scenarios["S1"]
    b = B.estimate(fx.feed, sc)
    assert b.candidates[0] != sc.resume_cursor
    missed = B.missed_window(fx.feed, b.candidates[0], sc.resume_cursor)
    # Strictly before the resume ball: 16.6 is the ball the fan came back on.
    assert [e.over_label for e in missed] == ["16.3", "16.4", "16.5"]


def test_stall_boundary_keeps_the_stalled_ball_in_the_gap(fx):
    b = B.estimate(fx.feed, fx.scenarios["S2"])
    assert b.candidates == [_over(fx, "16.6")]
    assert b.confidence == pytest.approx(0.90)
    assert any("17.1" in n for n in b.notes)
    assert any("never rendered" in n for n in b.notes)


def test_late_join_has_no_boundary_at_all(fx):
    b = B.estimate(fx.feed, fx.scenarios["S3"])
    assert b.candidates == []
    assert b.confidence == 0.0
    assert b.needs_fan_input is True


def test_candidates_are_never_past_the_resume_cursor(fx):
    sc = fx.scenarios["S1"]
    b = B.estimate(fx.feed, sc)
    hi = fx.feed.index[sc.resume_cursor]
    assert all(fx.feed.index[c] <= hi for c in b.candidates)


# --------------------------------------------------------------------------- #
# value of information: ask only when the answer changes the receipt
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid,expected", [("S1", True), ("S2", False), ("S3", True)])
def test_should_ask_matches_the_fixture_verdict(fx, sid, expected):
    sc = fx.scenarios[sid]
    b = B.estimate(fx.feed, sc)
    ask, why = voi.should_ask(fx.feed, b, sc.resume_cursor, sc.resume_cursor)
    assert ask is expected
    assert why


def test_an_uncertain_but_immaterial_boundary_stays_quiet(fx):
    """Two candidates that miss the same key events are the same receipt - no question.

    S2's stall covers two dot balls, so widening the boundary buys nothing and the fan is
    not asked; the trace has to carry that reason rather than a silent no.
    """
    sc = fx.scenarios["S2"]
    b = B.estimate(fx.feed, sc)
    b.candidates = [_over(fx, "16.6"), _over(fx, "17.1")]
    b.confidence = 0.4
    assert voi.boundary_is_ambiguous(b) is True
    ask, why = voi.should_ask(fx.feed, b, sc.resume_cursor, sc.resume_cursor)
    assert ask is False
    assert "same key events" in why


def test_late_join_question_names_the_span_and_the_team(fx):
    sc = fx.scenarios["S3"]
    b = B.estimate(fx.feed, sc)
    text, options = voi.boundary_question(fx.feed, b, sc.resume_cursor, sc.resume_cursor,
                                         sc.fan_team)
    assert "15.1-17.4" in text
    assert any(sc.fan_team in o for o in options)
    assert any("live" in o.lower() for o in options)


def test_required_rate_is_recomputed_and_agrees_with_the_feed(fx):
    r = rates.change(fx.feed.meta.target, fx.feed.find_by_over("16.2"),
                     fx.feed.find_by_over("16.6"))
    assert r["consistent_with_feed"] is True
    assert r["delta"] == round(r["after_rrr"] - r["before_rrr"], 2)
    assert r["runs_needed"] == fx.feed.meta.target - fx.feed.find_by_over("16.6").innos_runs


# --------------------------------------------------------------------------- #
# the frame reader and the clip guardrail
# --------------------------------------------------------------------------- #

def test_ocr_reads_the_injected_fault_out_of_the_pixels(fx):
    bad = next(f for f in fx.frames if f.injected_fault)
    read = ocr.read_frame(bad.image_bytes())
    assert read["readable"] is True
    assert read["score"] == bad.true_score            # 143/4: what the picture actually says
    assert read["score"] != fx.feed.find_by_over(bad.over_label).score


def test_a_frame_that_agrees_is_a_real_check(fx):
    good = fx.by_frame_id["frm_16_3"]
    read = ocr.read_frame(good.image_bytes())
    assert (read["score"], read["over"]) == ("142/4", "16.3")


def test_clip_is_refused_when_the_pixels_disagree(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    res = sess.toolbox.offer_clip(_clip_args(_over(fx, "16.4")))
    assert res["offerable"] is False
    assert "143/4" in res["why"] and "146/4" in res["why"]
    assert [e for e in sess.trace.events if e["kind"] == "clip_rejected"]


def test_clip_is_quoted_not_loaded(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    res = sess.toolbox.offer_clip(_clip_args(_over(fx, "16.3")))
    assert res["offerable"] is True
    assert res["quote"]["load_policy"] == "not fetched until the fan taps it"
    assert res["quote"]["bytes_estimate_kb"] == 12 * 200


def _clip_args(event_id: str):
    from backend.packs.gap.tools import ClipArgs
    return ClipArgs(event_id=event_id)


def test_guardrails_reject_a_spoiler_and_uncited_causality(fx):
    feed, cursor = fx.feed, _over(fx, "16.6")
    v = guardrails.validate_receipt([_over(fx, "17.1")], cursor, feed)
    assert [x.rule for x in v] == ["spoiler_beyond_resume_cursor"]
    v = guardrails.validate_receipt([cursor], cursor, feed, end_over="17.2")
    assert "spoiler_end_over" in {x.rule for x in v}
    v = guardrails.validate_receipt([cursor], cursor, feed,
                                    why="Two wickets in four balls. [evt_999]")
    assert "cites_uncited_event" in {x.rule for x in v}
    v = guardrails.validate_receipt([cursor], cursor, feed, why="Two wickets in four balls.")
    assert [x.rule for x in v] == ["uncited_causality"]
    # Every causal sentence has to carry its own citation - a marked tail does not license
    # the unmarked sentence before it.
    assert guardrails.validate_receipt([cursor], cursor, feed,
                                       why=f"Two wickets in four balls [{cursor}].") == []
    v = guardrails.validate_receipt([cursor], cursor, feed,
                                    why=f"[{cursor}] fell. Two wickets in four balls.")
    assert [x.rule for x in v] == ["uncited_causality"]


# --------------------------------------------------------------------------- #
# the model-off run: the fan still gets asked, and still gets a receipt
# --------------------------------------------------------------------------- #

def test_no_model_still_asks_the_fan_and_commits_a_receipt(fx, tmp_path):
    events: list[dict] = []
    sess = _session(fx, tmp_path, "S1", events)
    sess.run_scenario("S1")

    asks = [e for e in sess.trace.events if e["kind"] == "ask_user"]
    assert len(asks) == 1, "the kernel must ask exactly once, before any model turn"
    assert "16.3" in asks[0]["question"]
    assert any(e["type"] == "ask_user" for e in events)

    assert sess.chosen_boundary == _over(fx, "16.2")
    assert sess.boundary_source == "fan"
    card = sess.cards[0]
    assert card.kind == "receipt"
    assert card.truth_label is TruthLabel.EVIDENCED
    assert card.cited_tally_ids, "the rate digits are only licensed by the tally"
    assert {_over(fx, "16.3"), _over(fx, "16.4"), _over(fx, "16.5")} <= set(card.cited_event_ids)
    assert _over(fx, "16.6") not in card.cited_event_ids, \
        "the ball the fan resumed on must never be listed as missed"
    assert "Nothing after 16.6 is included." in card.body


def test_the_off_layer_commits_a_receipt_without_a_single_model_turn(fx, tmp_path):
    """`--llm off` has to mean no model turn at all, not a recorded one under another name.

    A live=False transport on a real cassette still serves every recorded turn and badges
    `cassette`, so the dead layer needs its own class - and an empty model budget, because a
    transport that raises only proves the kernel never got a turn after trying for one.
    """
    dead = UnavailableLLM()
    with pytest.raises(LLMError):
        dead.complete("anything", tag="supervisor")
    assert dead.calls == 1 and dead.mode_used == "unavailable"

    from backend.kernel.schemas import Budget
    budget = Budget(max_llm_calls=0, max_tool_calls=8, max_ms=60_000, max_specialist_rounds=4)
    events: list[dict] = []
    sess = GapSession(UnavailableLLM(), fx, TraceRecorder(tmp_path / "off.jsonl"), events.append,
                      cfg=GapConfig(budget=budget),
                      auto_answer=fx.scenarios["S1"].fan_answers)
    sess.run_scenario("S1")

    assert sess.llm.calls == 0, "the off layer must not even attempt a model turn"
    assert sess.badges()["llm"] == "unavailable", "the badge names the layer that made the card"
    card = sess.cards[0]
    assert card.kind == "receipt" and card.cited_tally_ids
    assert any(e["type"] == "status" and "deterministic" in str(e.get("note", ""))
               for e in events), "no silence, and the run says which path built the receipt"


def test_no_op_scenario_asks_nothing_and_still_sends_a_card(fx, tmp_path):
    sess = _session(fx, tmp_path, "S2")
    sess.run_scenario("S2")
    assert not [e for e in sess.trace.events if e["kind"] == "ask_user"]
    quiet = [e for e in sess.trace.events if e["kind"] == "no_question"]
    assert len(quiet) == 1
    assert "same key events" not in quiet[0]["why"]      # here it is the confidence, not the tie
    assert quiet[0]["why"]
    card = sess.cards[0]
    assert card.kind == "noop"
    assert set(card.cited_event_ids) == {_over(fx, "16.6"), _over(fx, "17.1"), _over(fx, "17.2")}


def test_late_join_gets_the_whole_clamped_window(fx, tmp_path):
    sess = _session(fx, tmp_path, "S3")
    sess.run_scenario("S3")
    card = sess.cards[0]
    assert card.kind == "receipt"
    first, cursor = fx.feed.events[0].id, fx.scenarios["S3"].resume_cursor
    assert card.cited_tally_ids == [f"gap_S3_{first}_{cursor}"]
    assert _over(fx, "15.3") in card.cited_event_ids
    assert fx.feed.events[-1].id not in card.cited_event_ids


def test_the_fan_is_never_asked_twice(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.run_scenario("S1")
    before = len([e for e in sess.trace.events if e["kind"] == "ask_user"])
    from backend.packs.gap.tools import AskArgs
    res = sess.toolbox.ask_fan(AskArgs(question="Did you see the wicket at ball 16.3?"))
    assert res["answered"] is True
    assert len([e for e in sess.trace.events if e["kind"] == "ask_user"]) == before


def test_a_non_candidate_answer_never_moves_the_boundary(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    sess.chosen_boundary = sess.boundary.candidates[0]      # what run_scenario seeds from telemetry
    sess._apply_answer("No, I left at 15.5")
    assert sess.chosen_boundary == sess.boundary.candidates[0]
    assert sess.boundary_source == "telemetry"


def test_a_candidate_answer_does_move_the_boundary(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    sess.chosen_boundary = sess.boundary.candidates[0]
    sess._apply_answer(f"No, I left at {fx.feed.by_id[_over(fx, '16.1')].over_label}")
    assert sess.chosen_boundary == _over(fx, "16.1")
    assert sess.boundary_source == "fan"
    assert f"fan:{_over(fx, '16.1')}" in sess.evidence_notes


def test_the_fans_answer_changes_the_receipt(fx, tmp_path):
    """If the answer changed nothing, the question wasted the fan's patience.

    Same scenario, same settled boundary: "recap everything" lists the whole window, "only
    wickets and boundaries" lists the key actions and says why the rest is missing.
    """
    wide = _run(fx, tmp_path, "S1", {"did you see": "No, I left at 16.2, recap everything"})
    narrow = _run(fx, tmp_path, "S1",
                  {"did you see": "No, I left at 16.2, only wickets and boundaries please"})
    dot = _over(fx, "16.5")
    assert wide.fan_pref == "everything" and narrow.fan_pref == "key_only"
    assert "Other balls in the window: 16.5" in wide.cards[0].body
    assert "16.5" not in narrow.cards[0].body
    assert "Key moments only, as you asked" in narrow.cards[0].body
    assert "scope_key_only" in narrow.cards[0].tags
    assert wide.cards[0].headline == narrow.cards[0].headline
    assert wide.cards[0].cited_tally_ids == narrow.cards[0].cited_tally_ids
    # Both cards may cite the whole window; only the "everything" card prints its spare balls.
    assert dot in wide.cards[0].cited_event_ids and dot in narrow.cards[0].cited_event_ids


def test_a_wide_window_still_fits_the_card(fx, tmp_path):
    """S3's late join spans fifteen balls; the template has to collapse, not overflow.

    A receipt that breaks the card length limit is a commit rejection, and a rejected receipt
    means the fan gets nothing - so the fit is part of the template's contract.
    """
    wide = _run(fx, tmp_path, "S3", {"recap": "Yes, recap everything"})
    body = wide.cards[0].body
    assert len(body) <= 700
    assert "Other balls in the window" in body or "not key actions" in body
    assert all(len(c.body) <= 700 for c in wide.cards)


def test_nothing_past_the_cursor_is_ever_released_to_an_agent(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.run_scenario("S1")
    hi = fx.feed.index[fx.scenarios["S1"].resume_cursor]
    assert all(fx.feed.index[e] <= hi for e in sess.released_events)
    assert all(fx.feed.index[e] <= hi for c in sess.cards for e in c.cited_event_ids)


class ChattySupervisor:
    """A supervisor that only ever asks a question - the shape of the HTTP bug.

    Over SSE the kernel's ask was answered, then the model asked its own second question, no
    fan was left on the line to answer it, and the episode ended `awaiting_user` with no card:
    silence, which is the one outcome this pack promises never to ship.
    """

    mode_used = "live"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt, system="", schema=None, **kwargs):
        self.calls += 1
        return schema(thought="I should check what the fan actually saw.", action="ask_user",
                      question="Which ball did you last see before the screen went?",
                      reason="the boundary is still unclear to me"), {}


def _chatty(fx, tmp_path, name, answers, timeout_s):
    return GapSession(ChattySupervisor(), fx, TraceRecorder(tmp_path / f"{name}.jsonl"),
                      lambda e: None,
                      cfg=GapConfig(ask_timeout_s=timeout_s, budget=_budget()),
                      auto_answer=answers)


def test_a_model_question_after_the_kernels_does_not_bother_the_fan(fx, tmp_path):
    events: list[dict] = []
    sess = GapSession(ChattySupervisor(), fx, TraceRecorder(tmp_path / "twice.jsonl"),
                      events.append,
                      cfg=GapConfig(ask_timeout_s=10.0, budget=_budget()),
                      auto_answer=fx.scenarios["S1"].fan_answers)
    sess.run_scenario("S1")
    assert len([e for e in events if e["type"] == "ask_user"]) == 1
    refused = [e for e in sess.trace.events if e["kind"] == "ask_refused"]
    assert refused and refused[0]["answer"] == "No, I left at 16.2"
    assert sess.cards, "a settled boundary plus a chatty model still owes the fan a receipt"


def test_an_unanswered_question_still_sends_the_deterministic_receipt(fx, tmp_path):
    sess = _chatty(fx, tmp_path, "silent", {}, 0.2)
    ep = sess.run_scenario("S1")
    assert ep.terminal.value == "awaiting_user"
    assert len(sess.cards) == 1
    card = sess.cards[0]
    assert card.kind == "receipt"
    hi = fx.feed.index[fx.scenarios["S1"].resume_cursor]
    assert all(fx.feed.index[e] <= hi for e in card.cited_event_ids)
