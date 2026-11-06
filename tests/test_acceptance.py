"""Executable acceptance criteria for the invariants this repo promises a reviewer.

The packs are unit-tested where they are clever; this file tests them where they are
load-bearing. Every check is a sentence from the product promise, run against a provider that
cannot answer at all, because "the demo worked" is only interesting if it worked with the model
dead. Nothing here touches the network: the LLM is either a script or a dead socket, and the
cassette layer is never asked to record.
"""
import re

import pytest

from backend.kernel.commit import Evidence, numbers_in
from backend.kernel.llm import LLMError
from backend.kernel.orchestrator import AgentSpec, Hooks, Orchestrator, SpecialistTurn
from backend.kernel.schemas import (Budget, Card, CardDraft, CommitResult, SupervisorDecision,
                                    TerminalState, TruthLabel)
from backend.kernel.trace import TraceRecorder
from backend.packs.gap import boundary as B
from backend.packs.gap import rates
from backend.packs.gap.agents import CriticOut
from backend.packs.gap.domain import GapFixtures
from backend.packs.gap.session import GapConfig, GapSession
from backend.packs.gap.tools import AskArgs, ClipArgs

SCENARIOS = ("S1", "S2", "S3")


class DeadLLM:
    """Every call raises: the provider day the fan must not pay for with a blank screen."""

    mode_used = "unavailable"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        raise LLMError("provider down")


class ScriptedLLM:
    """Turns in order, then repeats the last one forever when `loop` is set. No provider, no
    flakes: the kernel's own control flow is what is under test here."""

    mode_used = "stub"

    def __init__(self, *turns, loop: bool = False) -> None:
        self.plan = list(turns)
        self.loop = loop
        self.i = 0
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, prompt, system="", schema=None, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        if self.i >= len(self.plan):
            if not self.loop or not self.plan:
                raise LLMError("script exhausted")
            turn = self.plan[-1]
        else:
            turn = self.plan[self.i]
            self.i += 1
        if callable(turn):
            turn = turn(schema, prompt)
        return turn, {"source": "stub"}


@pytest.fixture(scope="module")
def fx() -> GapFixtures:
    return GapFixtures.load()


def _budget() -> Budget:
    return Budget(max_llm_calls=6, max_tool_calls=8, max_ms=60_000, max_specialist_rounds=4)


def _session(fx, tmp_path, scenario_id, events=None) -> GapSession:
    return GapSession(DeadLLM(), fx, TraceRecorder(tmp_path / f"{scenario_id}.jsonl"),
                      events.append if events is not None else (lambda e: None),
                      cfg=GapConfig(budget=_budget()),
                      auto_answer=fx.scenarios[scenario_id].fan_answers)


def _run(fx, tmp_path, scenario_id, answers, name="") -> GapSession:
    sess = GapSession(DeadLLM(), fx, TraceRecorder(tmp_path / f"{scenario_id}{name}.jsonl"),
                      lambda e: None, cfg=GapConfig(budget=_budget()), auto_answer=answers)
    sess.run_scenario(scenario_id)
    return sess


def _over(fx, label: str) -> str:
    return fx.feed.find_by_over(label).id


def _kinds(sess) -> list[str]:
    return [e["kind"] for e in sess.trace.events]


def _licensed_numbers(sess, card) -> set[str]:
    """Re-derive the digit licence from the card's own citations, exactly the way commit.py does.

    Rebuilt here rather than read off `card.numbers`, so a bug in the gate cannot certify
    itself: the same evidence the gate used is recomputed from the fixture feed and tallies.
    """
    ev = Evidence()
    clamped = sess.feed.clamped(sess.scenario.resume_cursor)
    for eid in card.cited_event_ids:
        e = clamped.get(eid)
        if e is None:
            continue
        ev.event_ids.add(eid)
        ev.add(eid, f"{e.over_label} {e.score} runs={e.runs} rrr={e.rrr} {e.text}")
    for tid in card.cited_tally_ids:
        ev.add(tid, sess.tallies[tid])
    for key, note in sess.evidence_notes.items():
        ev.add(key, note)
    return ev.numbers()


def _ok_commit(draft: CardDraft) -> CommitResult:
    return CommitResult(ok=True, card=Card(id="card_test", episode_id="ep_test",
                                           truth_label=TruthLabel.EVIDENCED,
                                           attribution="acceptance",
                                           **draft.model_dump()))


# --------------------------------------------------------------------------- #
# AC-1  a dead model still ships exactly one card, every scenario, never silence
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid", SCENARIOS)
def test_every_gap_scenario_ends_with_exactly_one_card_when_the_model_is_dead(fx, tmp_path, sid):
    events: list[dict] = []
    sess = _session(fx, tmp_path, sid, events)
    ep = sess.run_scenario(sid)
    assert len(sess.cards) == 1, f"{sid} left the fan with {len(sess.cards)} cards"
    card = sess.cards[0]
    assert len(card.body) <= 700
    assert len(card.headline) <= 90
    assert 1 <= ep.used.llm_calls <= sess.cfg.budget.max_llm_calls

    hi = fx.feed.index[fx.scenarios[sid].resume_cursor]
    assert all(fx.feed.index[e] <= hi for e in card.cited_event_ids)
    # The fan's own channel saw it: a card that only exists in the trace is still silence.
    assert [e["type"] for e in events].count("card") == 1
    assert sess.badges()["llm"] == "unavailable"


@pytest.mark.parametrize("sid", SCENARIOS)
def test_the_dead_model_receipt_prints_nothing_its_citations_do_not_license(fx, tmp_path, sid):
    sess = _session(fx, tmp_path, sid)
    sess.run_scenario(sid)
    card = sess.cards[0]
    printed = numbers_in(f"{card.headline} {card.body}")
    stray = sorted(printed - _licensed_numbers(sess, card))
    assert not stray, f"{sid} prints digits no citation carries: {stray}"
    assert set(card.numbers) == printed, "the gate records exactly the digits it licensed"


def test_the_receipt_names_its_boundary_and_cites_no_ball_past_the_cursor(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.run_scenario("S1")
    sc = fx.scenarios["S1"]
    card = sess.cards[0]
    assert card.cited_event_ids, "a receipt with no evidence is not a receipt"
    assert f"Nothing after {fx.feed.by_id[sc.resume_cursor].over_label} is included." in card.body


# --------------------------------------------------------------------------- #
# AC-2  the fan is asked at most once, and the kernel asks before any model turn
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid,asks", [("S1", 1), ("S2", 0), ("S3", 1)])
def test_the_fan_is_asked_at_most_once_and_before_the_first_supervisor_turn(fx, tmp_path,
                                                                            sid, asks):
    sess = _session(fx, tmp_path, sid)
    sess.run_scenario(sid)
    kinds = _kinds(sess)
    assert kinds.count("ask_user") == asks
    if asks:
        # With the model dead the trace has no `supervisor_decision` at all, so the ordering is
        # asserted against the first turn the supervisor did get: ask first, model second.
        first_model_turn = min(i for i, k in enumerate(kinds) if k.startswith("supervisor_"))
        assert kinds.index("ask_user") < first_model_turn, \
            "the kernel's question must precede the model's, or the answer is a second ask"
        assert kinds.index("ask_user") == 0, "nothing may be decided before the fan is asked"
    else:
        assert kinds.count("no_question") == 1, "staying quiet has to carry its reason"


def test_the_kernels_ask_precedes_the_first_supervisor_decision_by_index(fx, tmp_path):
    """The same promise measured against a supervisor that does answer."""
    sess = GapSession(ScriptedLLM(SupervisorDecision(action="stop", thought="nothing to add")),
                      fx, TraceRecorder(tmp_path / "order.jsonl"), lambda e: None,
                      cfg=GapConfig(budget=_budget()),
                      auto_answer=fx.scenarios["S1"].fan_answers)
    sess.run_scenario("S1")
    kinds = _kinds(sess)
    assert kinds.count("ask_user") == 1 == kinds.count("supervisor_decision")
    assert kinds.index("ask_user") < kinds.index("supervisor_decision")
    assert len(sess.cards) == 1


@pytest.mark.parametrize("sid", ("S1", "S3"))
def test_no_path_can_ask_a_fan_who_has_already_answered(fx, tmp_path, sid):
    sess = _session(fx, tmp_path, sid)
    sess.run_scenario(sid)
    before = _kinds(sess).count("ask_user")
    res = sess.toolbox.ask_fan(AskArgs(question="Did you see the wicket at ball 16.3?"))
    assert res["answered"] is True
    assert _kinds(sess).count("ask_user") == before
    assert "ask_fan_refused" in _kinds(sess)


# --------------------------------------------------------------------------- #
# AC-3  a fan answer only moves the boundary when it names a real candidate
# --------------------------------------------------------------------------- #

def test_an_answer_naming_a_non_candidate_leaves_the_boundary_where_the_telemetry_put_it(
        fx, tmp_path):
    sess = _run(fx, tmp_path, "S1", {"did you see": "No, I left at 15.5, recap everything"},
                name="noncandidate")
    assert sess.boundary_source == "telemetry"
    assert sess.chosen_boundary == sess.boundary.candidates[0]
    assert not [k for k in sess.evidence_notes if k.startswith("fan:")]
    assert _over(fx, "16.3") in sess.cards[0].cited_event_ids
    assert "fan_set_boundary" not in sess.cards[0].tags


def test_an_answer_naming_a_candidate_moves_the_boundary_and_becomes_evidence(fx, tmp_path):
    cand = _over(fx, "16.1")
    sess = _run(fx, tmp_path, "S1", {"did you see": "No, I left at 16.1, recap everything"},
                name="candidate")
    assert sess.chosen_boundary == cand
    assert sess.boundary_source == "fan"
    assert f"fan:{cand}" in sess.evidence_notes
    assert fx.feed.by_id[cand].over_label in sess.evidence_notes[f"fan:{cand}"]
    assert "fan_set_boundary" in sess.cards[0].tags
    # 16.2 was outside the gap while the fan was still watching; it is inside it now.
    assert _over(fx, "16.2") in sess.cards[0].cited_event_ids
    assert numbers_in(f"{sess.cards[0].headline} {sess.cards[0].body}") <= \
        _licensed_numbers(sess, sess.cards[0])


# --------------------------------------------------------------------------- #
# AC-4  the answer changes the artifact, or the question cost the fan nothing but time
# --------------------------------------------------------------------------- #

def test_recap_everything_and_key_moments_only_differ_in_body_share_headline_and_tally(
        fx, tmp_path):
    wide = _run(fx, tmp_path, "S1", {"did you see": "No, I left at 16.2, recap everything"},
                name="wide")
    narrow = _run(fx, tmp_path, "S1",
                  {"did you see": "No, I left at 16.2, only wickets and boundaries please"},
                  name="narrow")
    a, b = wide.cards[0], narrow.cards[0]
    assert wide.fan_pref == "everything" and narrow.fan_pref == "key_only"
    assert a.headline == b.headline
    assert a.cited_tally_ids == b.cited_tally_ids
    assert a.cited_event_ids == b.cited_event_ids
    assert a.body != b.body
    assert "Other balls in the window" in a.body
    assert "Other balls in the window" not in b.body
    assert "Key moments only, as you asked" in b.body
    assert "scope_key_only" in b.tags
    for sess in (wide, narrow):
        card = sess.cards[0]
        assert numbers_in(f"{card.headline} {card.body}") <= _licensed_numbers(sess, card)


# --------------------------------------------------------------------------- #
# AC-5  the required-rate line is the fixture's own arithmetic, not a model's guess
# --------------------------------------------------------------------------- #

_RATE_RE = re.compile(r"Required rate ([\d.]+) -> ([\d.]+) \(([+-][\d.]+)\); "
                      r"(\d+) needed off (\d+) balls")


def test_the_rate_on_the_card_equals_rates_change_recomputed_from_the_fixture(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.run_scenario("S1")
    m = _RATE_RE.search(sess.cards[0].body)
    assert m, f"the card must state the rate line in full: {sess.cards[0].body}"
    r = rates.change(fx.feed.meta.target, fx.feed.by_id[sess.chosen_boundary],
                     fx.feed.by_id[fx.scenarios["S1"].resume_cursor])
    assert (float(m.group(1)), float(m.group(2)), float(m.group(3))) == \
        (r["before_rrr"], r["after_rrr"], r["delta"])
    assert (int(m.group(4)), int(m.group(5))) == (r["runs_needed"], r["balls_left"])
    assert r["consistent_with_feed"] is True, "the fixture's own rrr column must agree"


# --------------------------------------------------------------------------- #
# AC-6  a clip whose pixels disagree with the feed is refused and never reaches the card
# --------------------------------------------------------------------------- #

def test_a_clip_whose_pixels_disagree_with_the_feed_is_refused_and_stays_off_the_card(
        fx, tmp_path):
    faulty = next(f for f in fx.frames if f.injected_fault)
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    res = sess.toolbox.offer_clip(ClipArgs(event_id=_over(fx, faulty.over_label)))
    assert res["offerable"] is False
    assert "clip_rejected" in _kinds(sess)
    assert faulty.true_score in res["why"]
    assert fx.feed.find_by_over(faulty.over_label).score in res["why"]
    # A refused clip cannot be quoted: the receipt has to say it loaded nothing.
    card = sess.build_card("receipt", why="", clip_event=_over(fx, faulty.over_label))
    assert "No clip loaded" in card.body
    assert "Clip offered" not in card.body


# --------------------------------------------------------------------------- #
# AC-7  kernel: capability scope
# --------------------------------------------------------------------------- #

def test_a_specialist_cannot_reach_a_tool_its_capabilities_do_not_cover(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    res = sess.registry.call("offer_clip", {"event_id": _over(fx, "16.3")},
                             allowed_tools={"offer_clip"}, granted_caps={"telemetry"})
    assert res.ok is False and res.blocked is True
    assert res.error == "capability_denied:permission"
    assert sess.clip_quotes == {}, "a blocked tool must have no effect"


def test_a_tool_a_specialist_was_never_given_is_blocked_and_traced(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    llm = ScriptedLLM(
        SupervisorDecision(action="dispatch", agent="investigator", thought="check the log"),
        SpecialistTurn(kind="tool", thought="want the clip", tool="offer_clip",
                       args_json=f'{{"event_id": "{_over(fx, "16.3")}"}}'),
        SpecialistTurn(kind="final", thought="nothing more",
                       result_json='{"sources": [], "coverage_ok": true}'),
        SupervisorDecision(action="stop", thought="done"),
    )
    orch = Orchestrator(llm, sess.registry, {"investigator": sess.agents["investigator"]},
                        sess.trace, hooks=Hooks())
    out = orch.run_episode(pack="gap", trigger="probe", objective="probe the wall",
                           observation={}, state_summary="test",
                           on_commit=lambda d: CommitResult(ok=False), budget=_budget())
    assert out.terminal is TerminalState.STOPPED_NOOP
    blocked = [e for e in sess.trace.events if e["kind"] == "tool_call" and e.get("blocked")]
    assert len(blocked) == 1, "the refusal is traced, not swallowed"
    assert blocked[0]["caller"] == "investigator"
    assert blocked[0]["error"] == "tool_not_granted:offer_clip"
    assert sess.clip_quotes == {}, "a blocked call has no effect, only a footprint"
    assert out.episode.used.tool_calls == 1, "a blocked call still costs the budget it spent"


def test_naming_an_agent_the_pack_never_declared_dispatches_nothing(fx, tmp_path):
    """Capability scoping cannot be walked around by inventing a role."""
    sess = _session(fx, tmp_path, "S1")
    llm = ScriptedLLM(
        SupervisorDecision(action="dispatch", agent="critic", thought="pretend to be a reader"),
        SupervisorDecision(action="stop", thought="ok"),
    )
    orch = Orchestrator(llm, sess.registry, {"investigator": sess.agents["investigator"]},
                        sess.trace, hooks=Hooks())
    out = orch.run_episode(pack="gap", trigger="probe", objective="probe the role wall",
                           observation={}, state_summary="test",
                           on_commit=lambda d: CommitResult(ok=False), budget=_budget())
    assert out.terminal is TerminalState.STOPPED_NOOP
    assert any("unknown agent role" in n for n in out.scratch["notes"])
    assert not [e for e in sess.trace.events if e["kind"] == "agent_result"]


# --------------------------------------------------------------------------- #
# AC-8  kernel: budgets are enforced and the run terminates
# --------------------------------------------------------------------------- #

def _bare_orchestrator(fx, tmp_path, llm, budget):
    sess = GapSession(llm, fx, TraceRecorder(tmp_path / "orch.jsonl"), lambda e: None,
                      cfg=GapConfig(budget=budget))
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    orch = Orchestrator(llm, sess.registry, {"investigator": sess.agents["investigator"]},
                        sess.trace, hooks=Hooks())
    return sess, orch


def test_the_llm_budget_stops_the_episode_and_says_where_in_the_trace(fx, tmp_path):
    budget = Budget(max_llm_calls=4, max_tool_calls=50, max_ms=60_000, max_specialist_rounds=50)
    llm = ScriptedLLM(
        SupervisorDecision(action="dispatch", agent="investigator", thought="one more look"),
        SpecialistTurn(kind="tool", thought="the window", tool="query_event_feed",
                       args_json='{"start_over": "16.1", "end_over": "16.6"}'),
        SpecialistTurn(kind="final", thought="ok", result_json='{"sources": [], "coverage_ok": true}'),
        SupervisorDecision(action="dispatch", agent="investigator", thought="and again"),
        loop=True,
    )
    sess, orch = _bare_orchestrator(fx, tmp_path, llm, budget)
    out = orch.run_episode(pack="gap", trigger="probe", objective="spend the budget",
                           observation={}, state_summary="test",
                           on_commit=lambda d: CommitResult(ok=False), budget=budget)
    assert out.terminal is TerminalState.EXHAUSTED
    assert out.reason == "budget:max_llm_calls"
    blocks = [e for e in sess.trace.events if e["kind"] == "budget_block"]
    assert blocks and blocks[0]["where"] == "agent:investigator"
    assert out.episode.used.llm_calls <= budget.max_llm_calls


def test_the_tool_budget_stops_the_episode(fx, tmp_path):
    budget = Budget(max_llm_calls=30, max_tool_calls=2, max_ms=60_000, max_specialist_rounds=50)
    llm = ScriptedLLM(SupervisorDecision(action="tool", tool="query_event_feed",
                                         thought="read the window",
                                         args_json='{"start_over": "16.1"}'), loop=True)
    sess, orch = _bare_orchestrator(fx, tmp_path, llm, budget)
    out = orch.run_episode(pack="gap", trigger="probe", objective="spend the tool budget",
                           observation={}, state_summary="test",
                           on_commit=lambda d: CommitResult(ok=False), budget=budget)
    assert out.terminal is TerminalState.EXHAUSTED
    assert out.reason == "budget:max_tool_calls"
    assert out.episode.used.tool_calls <= budget.max_tool_calls


def test_a_run_that_exhausts_its_budget_still_owes_the_fan_a_card(fx, tmp_path):
    """The owed-fallback is the last line of defence, so it has to fire on the budget wall."""
    budget = Budget(max_llm_calls=1, max_tool_calls=2, max_ms=60_000, max_specialist_rounds=4)
    sess = GapSession(ScriptedLLM(SupervisorDecision(action="dispatch", agent="investigator",
                                                      thought="again"), loop=True),
                      fx, TraceRecorder(tmp_path / "owed.jsonl"), lambda e: None,
                      cfg=GapConfig(budget=budget),
                      auto_answer=fx.scenarios["S1"].fan_answers)
    ep = sess.run_scenario("S1")
    assert ep.terminal is TerminalState.EXHAUSTED
    assert len(sess.cards) == 1
    assert sess.cards[0].kind == "receipt"


# --------------------------------------------------------------------------- #
# AC-9  kernel: the commit gate, with every rejection visible in the trace
# --------------------------------------------------------------------------- #

def _receipt_draft(fx, **over):
    base = dict(kind="receipt", headline="Catch up", body="You missed a wicket at 16.3.",
                salience=1.0, cited_event_ids=[_over(fx, "16.3")],
                cited_tally_ids=[], cited_utterance_ids=[], cited_verdict_ids=[], tags=[])
    base.update(over)
    return CardDraft(**base)


@pytest.mark.parametrize("case,rule", [
    ("uncited", "evidence_required"),
    ("unlicensed", "unlicensed_number"),
    ("over_length", "over_length"),
    ("spoiler", "spoiler_beyond_resume_cursor"),
])
def test_the_commit_gate_refuses_each_broken_card_and_the_trace_carries_the_violations(
        fx, tmp_path, case, rule):
    sess = _session(fx, tmp_path, "S1")
    sess.scenario = fx.scenarios["S1"]
    sess.boundary = B.estimate(fx.feed, sess.scenario)
    sess.released_events = set()

    if case == "uncited":
        draft = _receipt_draft(fx, cited_event_ids=[], body="You missed a wicket.")
    elif case == "unlicensed":
        draft = _receipt_draft(fx, body="You missed 999 runs.")
    elif case == "spoiler":
        draft = _receipt_draft(fx, cited_event_ids=[_over(fx, "17.1")])
    else:
        # CardDraft caps its own body at 700, so an over-length proposal can only be built by
        # bypassing validation - which is exactly what a hostile model would do.
        draft = CardDraft.model_construct(kind="receipt", headline="Catch up",
                                          body="x" * 721, salience=1.0,
                                          cited_event_ids=[_over(fx, "16.3")],
                                          cited_tally_ids=[], cited_utterance_ids=[],
                                          cited_verdict_ids=[], tags=[])

    res = sess._commit_draft(draft)
    assert res.ok is False
    assert rule in {v.rule for v in res.violations}, [v.model_dump() for v in res.violations]
    rejected = [e for e in sess.trace.events if e["kind"] == "commit" and e["ok"] is False]
    assert rejected, "a rejection the fan cannot see is a rejection that did not happen"
    assert rule in {v["rule"] for v in rejected[-1]["violations"]}
    assert not sess.cards, "a refused card never reaches the fan"


def test_the_commit_gate_accepts_the_card_it_should_accept(fx, tmp_path):
    sess = _session(fx, tmp_path, "S1")
    sess.run_scenario("S1")
    accepted = [e for e in sess.trace.events if e["kind"] == "commit" and e["ok"]]
    assert len(accepted) == 1
    assert accepted[0]["card_id"] == sess.cards[0].id
    assert accepted[0]["evidence_keys"], "the licence must be listed, not assumed"


# --------------------------------------------------------------------------- #
# AC-10  kernel: the critic is a wall, not a suggestion
# --------------------------------------------------------------------------- #

def _critic_spec() -> AgentSpec:
    return AgentSpec("critic", "reject a receipt that would spoil or mislead",
                     "review the card", CriticOut, {"query_event_feed"}, {"past_events"},
                     max_inner_calls=1)


def _run_with_critic(fx, tmp_path, llm, require_critic: bool, on_commit):
    sess = GapSession(llm, fx, TraceRecorder(tmp_path / "critic.jsonl"), lambda e: None,
                      cfg=GapConfig(budget=_budget()))
    orch = Orchestrator(llm, sess.registry, {"critic": _critic_spec()}, sess.trace,
                        hooks=Hooks(), require_critic_before_commit=require_critic,
                        supervisor_caps={"telemetry", "past_events"})
    out = orch.run_episode(pack="gap", trigger="probe", objective="commit a card",
                           observation={}, state_summary="test", on_commit=on_commit,
                           budget=_budget())
    return sess, out


def _critic_reviews(sess) -> list[int]:
    """Indices (1-based call numbers) of the turns that were critic reviews."""
    return [i + 1 for i, p in enumerate(sess.llm.prompts) if "card_json=" in p]


_APPROVE = SpecialistTurn(kind="final", result_json='{"approve": true, "reason": "ok"}')
_REFUSE = SpecialistTurn(kind="final", result_json='{"approve": false, "reason": "too vague"}')


def test_a_commit_is_never_allowed_before_the_critic_has_reviewed_it(fx, tmp_path):
    draft = _receipt_draft(fx, body="You missed a wicket.")
    seen: list[tuple[str, int]] = []
    llm = ScriptedLLM(SupervisorDecision(action="commit", card=draft, thought="ship it"),
                      _APPROVE)
    sess, out = _run_with_critic(fx, tmp_path, llm, True,
                                 lambda d: (seen.append((d.body, llm.calls)), _ok_commit(d))[1])
    assert seen == [("You missed a wicket.", 2)], \
        "the commit hook may only run once the critic has already answered"
    assert _critic_reviews(sess) == [2]
    assert out.episode.llm_calls_by_agent.get("critic") == 1
    assert out.terminal is TerminalState.COMMITTED
    end = [e for e in sess.trace.events if e["kind"] == "episode_end"][-1]
    assert end["by_agent"].get("critic") == 1, "the review must be visible in the trace too"


def test_a_critic_that_refuses_does_not_let_the_same_card_through(fx, tmp_path):
    draft = _receipt_draft(fx, body="You missed a wicket.")
    seen: list[tuple[str, int]] = []
    llm = ScriptedLLM(SupervisorDecision(action="commit", card=draft, thought="ship it"),
                      _REFUSE,
                      SupervisorDecision(action="commit", card=draft, thought="trying again"),
                      _APPROVE)
    sess, out = _run_with_critic(fx, tmp_path, llm, True,
                                 lambda d: (seen.append((d.body, llm.calls)), _ok_commit(d))[1])
    assert _critic_reviews(sess) == [2, 4], \
        "a refused card must be reviewed again before it can commit"
    assert out.episode.llm_calls_by_agent.get("critic") == 2
    assert seen == [("You missed a wicket.", 4)], "the commit hook ran once, after the second review"
    assert any(n.startswith("critic rejected") for n in out.scratch.get("notes", []))
    assert out.terminal is TerminalState.COMMITTED


def test_the_critic_wall_is_the_flag_that_builds_it(fx, tmp_path):
    draft = _receipt_draft(fx, body="You missed a wicket.")
    seen: list[tuple[str, int]] = []
    llm = ScriptedLLM(SupervisorDecision(action="commit", card=draft, thought="ship it"))
    sess, out = _run_with_critic(fx, tmp_path, llm, False,
                                 lambda d: (seen.append((d.body, llm.calls)), _ok_commit(d))[1])
    assert seen == [("You missed a wicket.", 1)]
    assert _critic_reviews(sess) == [], "with the wall down the kernel must not spend a critic turn"
    assert out.episode.llm_calls_by_agent.get("critic") is None
    assert out.terminal is TerminalState.COMMITTED


def test_a_dead_critic_does_not_silence_the_card(fx, tmp_path):
    """The critic is a model turn like any other: when it cannot answer, the validators run."""
    draft = _receipt_draft(fx, body="You missed a wicket.")
    sess, out = _run_with_critic(fx, tmp_path,
                                 ScriptedLLM(SupervisorDecision(action="commit", card=draft,
                                                                 thought="ship it"),
                                             DeadLLMRefusal()),
                                 True, lambda d: _ok_commit(d))
    assert out.terminal is TerminalState.COMMITTED
    assert any("critic unavailable" in n for n in out.scratch.get("notes", []))
    assert [e for e in sess.trace.events if e["kind"] == "commit"] == [], \
        "the bare probe commits outside the pack, so nothing is recorded as a card"
    assert any(e["kind"] == "agent_error" and e["agent"] == "critic"
               for e in sess.trace.events), "a swallowed critic failure is not a critic wall"


class DeadLLMRefusal:
    """Raises for the critic turn only: the supervisor already answered from the script."""

    def __call__(self, schema, prompt):
        if schema is SpecialistTurn:
            raise LLMError("critic tier down")
        raise AssertionError("unexpected critic-shaped turn for the supervisor")


# --------------------------------------------------------------------------- #
# AC-11  labelling is code's, and the run is bounded
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sid,kind", [("S1", "receipt"), ("S2", "noop"), ("S3", "receipt")])
def test_the_card_kind_and_label_are_what_the_deterministic_layer_says(fx, tmp_path, sid, kind):
    sess = _session(fx, tmp_path, sid)
    ep = sess.run_scenario(sid)
    card = sess.cards[0]
    assert card.kind == kind
    assert card.truth_label in (TruthLabel.EVIDENCED, TruthLabel.VERIFIED,
                                TruthLabel.UNVERIFIABLE)
    assert card.committed_by == "kernel.commit"
    assert card.template_used, "every shipped card names the template that wrote it"
    assert ep.used.elapsed_ms >= 0
    assert ep.used.tool_calls <= sess.cfg.budget.max_tool_calls
