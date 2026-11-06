"""The multi-agent kernel: a supervisor LLM that dispatches specialist agents dynamically.

Why this is agentic and not a pipeline
--------------------------------------
The supervisor reads the current observation plus a scratchpad of what specialists and
tools have returned so far, and *chooses* the next step: which specialist to wake, which
tool it may use, whether to ask the fan, whether to commit, or whether to stay quiet.
Two different episodes can therefore take different paths.

Why it is still bounded
-----------------------
* Specialists only see the tools and capabilities their role grants.
* The feed is always a `ClampedFeed`; the fan's future is unqueryable.
* `hooks.mandatory_check` runs deterministic verification the supervisor cannot skip.
* `hooks.veto_terminal` lets a pack refuse its own "skip" once, then commit the fallback.
* `require_critic_before_commit` makes self-review a wall, not a suggestion.
* Every card goes through `kernel.commit`, which re-derives the truth label and rejects
  any digit that is not in the cited evidence.
* Hard budgets on LLM calls, tool calls, specialist rounds, steps and milliseconds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from .llm import LLMError
from .registry import ToolRegistry
from .schemas import (Budget, BudgetUsed, Card, CardDraft, DispatchRecord, Episode,
                      PendingQuestion, SupervisorDecision, TerminalState, new_id, now)
from .trace import TraceRecorder


# --------------------------------------------------------------------------- #
# Structured turns
# --------------------------------------------------------------------------- #


class SpecialistTurn(BaseModel):
    """A specialist either asks for one tool, or returns its final structured result.

    Nested payloads travel as JSON strings: every model tier in the chain accepts flat
    string fields, and free-form `OBJECT` schemas are not portable across Gemini versions.
    """

    kind: Literal["tool", "final"]
    thought: str = Field(default="", max_length=300)
    tool: Optional[str] = None
    args_json: str = "{}"
    result_json: str = "{}"


@dataclass
class AgentSpec:
    role: str
    mission: str
    system: str
    output: type[BaseModel]
    tools: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    max_inner_calls: int = 3
    temperature: float = 0.0

    def catalogue_line(self, registry: ToolRegistry) -> str:
        tool_names = ", ".join(sorted(self.tools)) or "none"
        return f"- {self.role}: {self.mission} (tools: {tool_names})"


@dataclass
class Hooks:
    """Pack-supplied deterministic behaviour the supervisor cannot override."""

    mandatory_check: Optional[Callable[[str, Any, dict], Optional[str]]] = None
    before_commit: Optional[Callable[[CardDraft, dict], Optional[CardDraft]]] = None
    build_fallback: Optional[Callable[[dict], Optional[CardDraft]]] = None
    provide_answer: Optional[Callable[[PendingQuestion], Optional[str]]] = None
    wait_for_answer: Optional[Callable[[PendingQuestion, float], Optional[str]]] = None
    observation_context: Optional[Callable[[dict], str]] = None
    veto_terminal: Optional[Callable[[str, dict], Optional[str]]] = None


@dataclass
class EpisodeResult:
    episode: Episode
    terminal: TerminalState
    reason: str
    cards: list[Card] = field(default_factory=list)
    questions: list[PendingQuestion] = field(default_factory=list)
    scratch: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    llm_calls_by_agent: dict[str, int] = field(default_factory=dict)


SUP_CONTRACT = """You are the SUPERVISOR of a bounded multi-agent system.
You choose the next single step. You do not compute scores, you do not label truth, and
you do not write a number anywhere unless it was returned to you by a tool this episode.

Reply with ONE JSON object, no prose:
{"thought": "<one line citing the observed state>",
 "action": "dispatch|tool|ask_user|request_permission|commit|skip|stop",
 "agent": "<role, only when action=dispatch>",
 "tool": "<tool name, when action=tool or dispatch needs no agent>",
 "args_json": "{\\"over\\": 17}",
 "reason": "<why this step>",
 "card": {<CardDraft fields, only when action=commit>},
 "question": "<short fan-facing question, when ask_user/request_permission>",
 "options": ["a","b"]}

CardDraft fields: kind, insight_type, headline, body, salience,
cited_utterance_ids, cited_event_ids, cited_verdict_ids, cited_tally_ids, tags.

Decide from state, not habit. If the reference is ambiguous, dispatch `perceiver` or ask.
The `analyst` is the only role that produces a structured claim. When it classifies an
utterance as a factual claim the kernel verifies that claim itself and puts the verdict in
the scratchpad - so dispatching `verifier` before `analyst` verifies nothing, and a verdict
must already exist in the scratchpad before you commit.
If nothing is worth the fan's attention, `skip` - a quiet agent is a correct agent.
`skip` deletes the insight; committing only to be collapsed by the surface policy keeps it in
the fan's timeline. So when a specialist found real information that is merely not
interruption-worthy, `commit` it and let the policy demote it - reserve `skip` for filler,
restatements and utterances that carry nothing.
If you cannot say anything true, `stop`. Do not pad."""


class Orchestrator:
    def __init__(
        self,
        llm,
        registry: ToolRegistry,
        agents: dict[str, AgentSpec],
        trace: TraceRecorder,
        hooks: Optional[Hooks] = None,
        require_critic_before_commit: bool = True,
        critic_role: str = "critic",
        supervisor_caps: Optional[set[str]] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.agents = agents
        self.trace = trace
        self.hooks = hooks or Hooks()
        self.require_critic = require_critic_before_commit
        self.critic_role = critic_role
        self.supervisor_caps = supervisor_caps if supervisor_caps is not None else set()

    # ------------------------------------------------------------------ #

    def run_episode(
        self,
        *,
        pack: str,
        trigger: str,
        objective: str,
        observation: dict[str, Any],
        state_summary: str,
        on_commit: Callable[[CardDraft], Any],
        budget: Optional[Budget] = None,
        scratch: Optional[dict[str, Any]] = None,
        episode_id: Optional[str] = None,
    ) -> EpisodeResult:
        ep = Episode(id=episode_id or new_id("ep"), pack=pack, trigger=trigger,
                     budget=budget or Budget())
        used = ep.used
        scratch = scratch if scratch is not None else {}
        result = EpisodeResult(ep, TerminalState.FAILED, "not_finished", scratch=scratch)
        scratch.setdefault("notes", [])
        scratch.setdefault("questions", [])
        committed: list[Card] = []

        self.trace.record(
            "episode_start", episode_id=ep.id, pack=pack, trigger=trigger,
            objective=objective[:200], state_summary=state_summary[:300],
            observation=_trim(observation), budget=ep.budget.model_dump(),
        )

        steps = 0
        max_steps = ep.budget.max_llm_calls + 4
        critic_ok_for: Optional[str] = None
        veto_used = False
        critic_rejections = 0

        while True:
            steps += 1
            used.touch()
            wall = self._wall_reason(ep, used, steps, max_steps)
            if wall:
                return self._finish(result, committed, TerminalState.EXHAUSTED, wall)

            decision = self._supervise(objective, state_summary, observation, scratch, ep, used)
            if decision is None:
                return self._finish(result, committed, TerminalState.FAILED, "supervisor_unavailable")

            act = decision.action
            self.trace.record(
                "supervisor_decision", episode_id=ep.id, step=steps, action=act,
                agent=decision.agent, tool=decision.tool, thought=decision.thought,
                reason=decision.reason, remaining=self._remaining(ep, used),
            )

            # ---- commit path ------------------------------------------------ #
            if act == "commit":
                if decision.card is None:
                    # A commit with no card is not a decision, it is a malformed one. Ignoring
                    # it silently let a live run repeat the same empty commit three times and
                    # spend the budget it needed for the real card.
                    empty_commits = scratch.get("empty_commits", 0) + 1
                    scratch["empty_commits"] = empty_commits
                    if empty_commits >= 2:
                        # `kernel_must_commit` is what makes the promise in this message true:
                        # _finish() only owes the fan a pack-built card when it is set.
                        scratch["kernel_must_commit"] = True
                        self.trace.record("guardrail", episode_id=ep.id,
                                          rule="empty_commit_twice",
                                          why="the supervisor asked to commit without a card "
                                              "twice; the pack's deterministic card will be used")
                        return self._finish(result, committed, TerminalState.STOPPED_NOOP,
                                            "commit without a card")
                    self._note(scratch, "commit needs a `card` object: attach one, or use stop")
                    continue
                draft = decision.card
                if self.hooks.before_commit:
                    draft = self.hooks.before_commit(draft, scratch) or draft
                fingerprint = json.dumps(draft.model_dump(), sort_keys=True, default=str)
                if (self.require_critic and self.critic_role in self.agents
                        and critic_ok_for != fingerprint):
                    critique = self._dispatch(self.agents[self.critic_role],
                                              f"Review this proposed card against the objective "
                                              f"and the rules.\ncard_json={draft.model_dump_json()}"
                                              f"\nscratch={_dump(scratch)}",
                                              ep, used)
                    if critique is None:
                        self._note(scratch, "critic unavailable; validators still run at commit")
                    elif not critique.get("approve", True):
                        scratch["critique"] = critique
                        critic_ok_for = None
                        critic_rejections += 1
                        self._note(scratch, f"critic rejected: {str(critique.get('reason'))[:120]}")
                        if critic_rejections >= 2:
                            # The critic is entitled to refuse, but it is not entitled to refuse
                            # forever: two rejections in one episode means the pack's own
                            # deterministic card is the better answer, so the episode ends and
                            # the pack commits that instead of burning the fan's budget.
                            scratch["kernel_must_commit"] = True
                            self.trace.record("guardrail", episode_id=ep.id,
                                              rule="critic_loop_bound",
                                              why="the critic refused the same card twice; the "
                                                  "pack's deterministic card will be used")
                            return self._finish(result, committed, TerminalState.STOPPED_NOOP,
                                                "critic refused twice")
                        continue
                    critic_ok_for = fingerprint
                outcome = on_commit(draft)
                if getattr(outcome, "ok", False) and getattr(outcome, "card", None):
                    committed.append(outcome.card)
                    result.cards.append(outcome.card)
                    ep.card_ids.append(outcome.card.id)
                    return self._finish(result, committed, TerminalState.COMMITTED,
                                        decision.reason or "card committed")
                violations = getattr(outcome, "violations", [])
                scratch["last_rejections"] = [f"{v.rule}: {v.detail} [{v.offending}]" for v in violations]
                self.trace.record("commit_rejected", episode_id=ep.id,
                                  violations=[v.model_dump() for v in violations])
                self._note(scratch, "commit rejected by validators: "
                                    + ", ".join(v.rule for v in violations))
                continue

            # ---- terminal-ish actions --------------------------------------- #
            if act in ("skip", "stop"):
                if self.hooks.veto_terminal and not veto_used:
                    refuse = self.hooks.veto_terminal(act, scratch)
                    if refuse:
                        veto_used = True
                        scratch["kernel_must_commit"] = True
                        self.trace.record("terminal_vetoed", episode_id=ep.id, action=act,
                                          reason=refuse)
                        self._note(scratch, f"KERNEL VETO of {act}: {refuse}. Commit the card "
                                            f"the kernel evidence supports, or stop.")
                        continue
                term = TerminalState.SKIPPED if act == "skip" else TerminalState.STOPPED_NOOP
                reason = decision.reason or decision.thought or act
                if decision.thought:
                    self._note(scratch, f"decision: {decision.thought[:200]}")
                return self._finish(result, committed, term, reason)

            # ---- human in the loop ------------------------------------------ #
            if act in ("ask_user", "request_permission"):
                pq = PendingQuestion(
                    text=decision.question or "Can you clarify?",
                    reason=decision.reason or "",
                    options=decision.options or [],
                    blocking=act == "request_permission",
                    expected_info_gain=0.6,
                )
                self.trace.record("ask_user", episode_id=ep.id, action=act,
                                  question=pq.text, options=pq.options, reason=pq.reason)
                answer = None
                if self.hooks.wait_for_answer:
                    answer = self.hooks.wait_for_answer(pq, 40.0)
                elif self.hooks.provide_answer:
                    answer = self.hooks.provide_answer(pq)
                if answer is None:
                    result.questions.append(pq)
                    scratch["questions"].append(pq.model_dump())
                    return self._finish(result, committed, TerminalState.ASKED, "awaiting fan answer")
                pq.answer = answer
                pq.t_answered = now()
                scratch.setdefault("user_answers", []).append({"q": pq.text, "a": answer})
                self.trace.record("user_answer", episode_id=ep.id, question=pq.text, answer=answer)
                continue

            # ---- direct tool ------------------------------------------------ #
            if act == "tool":
                if not decision.tool:
                    self._note(scratch, "tool action without a tool name")
                    continue
                args = _loads(decision.args_json)
                res = self.registry.call(decision.tool, args, granted_caps=self._union_caps(None))
                used.tool_calls += 1
                self.trace.record("tool_call", episode_id=ep.id, tool=decision.tool,
                                  args=args, ok=res.ok, preview=res.preview(), ms=res.ms,
                                  caller="supervisor")
                scratch.setdefault("tool_results", []).append({"tool": decision.tool,
                                                               "ok": res.ok,
                                                               "data": _trim(res.data if res.ok else res.error)})
                if res.ok and self.hooks.mandatory_check:
                    extra = self.hooks.mandatory_check("supervisor", {"tool": decision.tool, "data": res.data}, scratch)
                    if extra:
                        self._note(scratch, extra)
                continue

            # ---- dispatch specialist ----------------------------------------- #
            if act == "dispatch":
                role = decision.agent
                spec = self.agents.get(role or "")
                if spec is None:
                    self._note(scratch, f"unknown agent role '{role}'. Available: "
                                        f"{', '.join(sorted(self.agents))}")
                    continue
                prompt = (
                    f"OBJECTIVE: {objective}\n"
                    f"CURRENT STATE:\n{state_summary}\n"
                    f"OBSERVATION:\n{_dump(observation)}\n"
                    f"SCRATCHPAD SO FAR:\n{_dump(_compact_scratch(scratch))}\n"
                    + (f"SUPERVISOR INSTRUCTION: {decision.reason or decision.thought}\n"
                       if (decision.reason or decision.thought) else "")
                )
                out = self._dispatch(spec, prompt, ep, used, args_hint=decision.args_json)
                if out is None:
                    self._note(scratch, f"agent {role} returned nothing usable")
                    continue
                scratch[role] = out
                self.trace.record("agent_result", episode_id=ep.id, agent=role,
                                  result=_trim(out), llm_calls=used.llm_calls)
                if self.hooks.mandatory_check:
                    extra = self.hooks.mandatory_check(role, out, scratch)
                    if extra:
                        self._note(scratch, extra)
                continue

            self._note(scratch, f"unhandled action '{act}'")

    # ------------------------------------------------------------------ #

    def _dispatch(self, spec: AgentSpec, prompt: str, ep: Episode, used: BudgetUsed,
                  args_hint: str = "{}") -> Optional[dict]:
        """Run one specialist with its own bounded tool loop."""
        used.specialist_rounds += 1
        catalogue = self.registry.describe(allowed=spec.tools or None)
        base = (
            f"{spec.system}\n\nYOU MAY USE THESE TOOLS:\n{catalogue or '(none - reason from the scratchpad only)'}\n\n"
            "Reply with ONE JSON object:\n"
            '{"kind":"tool","thought":"<why>","tool":"<name>","args_json":"{...}"}\n'
            'or\n'
            '{"kind":"final","thought":"<why>","result_json":"<your result object>"}\n'
            "You must not invent facts, numbers, names or events. If a tool result is missing, "
            "say so in the field and mark it low confidence."
        )
        convo = prompt
        # A retry's reply is the model's answer to the question it was actually asked, so it
        # records under the base prompt: on replay the same answer arrives without the nudge.
        key_text = ""
        for inner in range(spec.max_inner_calls + 1):
            if used.llm_calls >= ep.budget.max_llm_calls:
                self.trace.record("budget_block", episode_id=ep.id, where=f"agent:{spec.role}")
                return None
            try:
                turn, meta = self.llm.complete(convo, system=base, schema=SpecialistTurn,
                                               key_text=key_text,
                                               temperature=spec.temperature, tag=f"{spec.role}:{inner}")
                key_text = ""
            except LLMError as exc:
                if "unparseable JSON" in str(exc) and inner < spec.max_inner_calls:
                    # Usually a truncated completion: the model narrated a long `thought` and
                    # ran out of output tokens mid-object. One tighter retry is cheaper than
                    # losing the specialist, and the retry budget is the same schema retries pay.
                    convo = (prompt + "\n\nYOUR LAST REPLY WAS NOT VALID JSON (likely cut off). "
                                      "Return kind=final with a result_json object and keep "
                                      "`thought` under 15 words.")
                    key_text = prompt
                    self.trace.record("agent_unparseable_retry", episode_id=ep.id, agent=spec.role,
                                      error=str(exc)[:160])
                    continue
                self.trace.record("agent_error", episode_id=ep.id, agent=spec.role, error=str(exc)[:200])
                return None
            used.llm_calls += 1
            used.prompt_tokens += int(meta.get("usage", {}).get("prompt_tokens", 0) or 0)
            used.output_tokens += int(meta.get("usage", {}).get("output_tokens", 0) or 0)
            ep.llm_calls_by_agent[spec.role] = ep.llm_calls_by_agent.get(spec.role, 0) + 1
            used.touch()

            if turn.kind == "final":
                payload = _loads(turn.result_json)
                try:
                    validated = spec.output.model_validate(payload)
                except ValidationError as exc:
                    convo = (prompt + f"\n\nYOUR LAST result_json FAILED SCHEMA:\n{exc.errors()[:4]}\n"
                                      f"Return kind=final with a valid result_json.")
                    self.trace.record("agent_schema_retry", episode_id=ep.id, agent=spec.role,
                                      errors=str(exc)[:200])
                    continue
                rec = DispatchRecord(step=used.llm_calls, action="dispatch", agent=spec.role,
                                     thought=turn.thought, result_preview=_dump(validated.model_dump())[:240],
                                     ms=round((now() - ep.started_at) * 1000, 1),
                                     budget_after=self._remaining(ep, used))
                ep.dispatches.append(rec)
                return validated.model_dump()

            tool = turn.tool or ""
            args = _loads(turn.args_json)
            res = self.registry.call(tool, args, allowed_tools=spec.tools, granted_caps=self._union_caps(spec))
            used.tool_calls += 1
            self.trace.record("tool_call", episode_id=ep.id, tool=tool, args=args, ok=res.ok,
                              preview=res.preview(), ms=res.ms, caller=spec.role,
                              blocked=res.blocked, error=res.error)
            convo += (f"\n\nTOOL {tool} -> ok={res.ok} "
                      f"result={_dump(res.data if res.ok else {'error': res.error})}")

        self.trace.record("agent_no_final", episode_id=ep.id, agent=spec.role)
        return None

    def _supervise(self, objective: str, state_summary: str, observation: dict, scratch: dict,
                   ep: Episode, used: BudgetUsed, tail: str = "") -> Optional[SupervisorDecision]:
        agents_cat = "\n".join(spec.catalogue_line(self.registry) for spec in self.agents.values())
        tools_cat = self.registry.describe()
        log = [f"{d.step}. {d.action} {d.agent or d.tool or ''} :: {d.thought[:70]}" for d in ep.dispatches[-8:]]
        # `ms` is wall clock, so it never prints: a prompt that changes between two runs of the
        # same state has no cassette key, no replay and no reproducible eval.
        remaining = {k: v for k, v in self._remaining(ep, used).items() if k != "ms"}
        prompt = (
            f"OBJECTIVE: {objective}\n\n"
            f"STATE:\n{state_summary}\n\n"
            f"OBSERVATION:\n{_dump(observation)}\n\n"
            f"SCRATCHPAD:\n{_dump(_compact_scratch(scratch))}\n\n"
            f"AGENTS AVAILABLE:\n{agents_cat}\n\n"
            f"TOOLS AVAILABLE:\n{tools_cat}\n\n"
            f"BUDGET REMAINING: {json.dumps(remaining)}\n\n"
            f"YOUR STEPS SO FAR: {json.dumps(log)}\n\n"
            "Choose the single next step."
        )
        if self.hooks.observation_context:
            extra = self.hooks.observation_context(scratch)
            if extra:
                prompt += f"\n\nDOMAIN NOTES:\n{extra}"
        # `prompt` is what the model sees; `key_text` is what the cassette is keyed on. The
        # recordings on disk were made with the budget line included, so it stays included:
        # changing the key here silently invalidates every recorded supervisor turn and drops
        # every replay run onto the deterministic fallback.
        base_prompt = prompt
        prompt += tail
        try:
            decision, meta = self.llm.complete(prompt, system=SUP_CONTRACT,
                                               schema=SupervisorDecision, tag="supervisor",
                                               key_text=base_prompt, max_output_tokens=2000)
        except LLMError as exc:
            # A call the provider truncated is still a call the demo paid for.
            used.llm_calls += 1
            used.touch()
            if "unparseable JSON" in str(exc) and not scratch.get("_sup_retry"):
                scratch["_sup_retry"] = True
                self.trace.record("supervisor_unparseable_retry", episode_id=ep.id,
                                  error=str(exc)[:160])
                return self._supervise(objective, state_summary, observation, scratch, ep, used,
                                       tail="\n\nYOUR LAST REPLY WAS CUT OFF IN THE MIDDLE OF "
                                            "THE JSON. Reply again with ONE complete JSON "
                                            "object and keep \"thought\" under 12 words.")
            self.trace.record("supervisor_error", episode_id=ep.id, error=str(exc)[:200])
            return None
        used.llm_calls += 1
        used.prompt_tokens += int((meta.get("usage") or {}).get("prompt_tokens", 0) or 0)
        used.output_tokens += int((meta.get("usage") or {}).get("output_tokens", 0) or 0)
        used.touch()
        scratch.pop("_sup_retry", None)
        return decision

    # ------------------------------------------------------------------ #

    def _union_caps(self, spec: Optional[AgentSpec]) -> Optional[set[str]]:
        caps = set(self.supervisor_caps)
        if spec:
            caps |= spec.capabilities
        return caps or None

    @staticmethod
    def _remaining(ep: Episode, used: BudgetUsed) -> dict[str, float]:
        used.touch()
        return ep.budget.remaining(used)

    def _wall_reason(self, ep: Episode, used: BudgetUsed, steps: int, max_steps: int) -> Optional[str]:
        used.touch()
        if used.llm_calls >= ep.budget.max_llm_calls:
            return "budget:max_llm_calls"
        if used.tool_calls >= ep.budget.max_tool_calls:
            return "budget:max_tool_calls"
        if used.specialist_rounds >= ep.budget.max_specialist_rounds:
            return "budget:max_specialist_rounds"
        if used.elapsed_ms >= ep.budget.max_ms:
            return f"budget:max_ms ({int(used.elapsed_ms)})"
        if steps >= max_steps:
            return "budget:max_steps"
        return None

    def _finish(self, result: EpisodeResult, cards: list[Card], term: TerminalState,
                reason: str) -> EpisodeResult:
        ep = result.episode
        result.terminal = term
        result.reason = reason
        ep.terminal = term
        ep.terminal_reason = reason
        ep.used.touch()
        owed = term in (TerminalState.EXHAUSTED, TerminalState.FAILED) or (
            term is TerminalState.SKIPPED and bool(result.scratch.get("kernel_must_commit")))
        if owed and not cards and self.hooks.build_fallback:
            fb = self.hooks.build_fallback(result.scratch)
            if fb is not None:
                result.notes.append(f"deterministic fallback used because {reason}")
                result.scratch["fallback_draft"] = fb
        self.trace.record("episode_end", episode_id=ep.id, terminal=term.value, reason=reason[:200],
                          cards=[c.id for c in cards], llm_calls=ep.used.llm_calls,
                          tool_calls=ep.used.tool_calls, elapsed_ms=round(ep.used.elapsed_ms, 1),
                          by_agent=ep.llm_calls_by_agent)
        return result

    @staticmethod
    def _note(scratch: dict, text: str) -> None:
        scratch.setdefault("notes", []).append(text[:300])


# --------------------------------------------------------------------------- #


def _loads(raw: str) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except Exception:
        return {}
    return val if isinstance(val, dict) else {}


def _dump(obj: Any, limit: int = 1800) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    return text[:limit]


def _trim(obj: Any, limit: int = 900) -> Any:
    if isinstance(obj, str):
        return obj[:limit]
    if isinstance(obj, dict):
        return {k: _trim(v, limit) for k, v in list(obj.items())[:24]}
    if isinstance(obj, list):
        return [_trim(v, limit) for v in obj[:12]]
    return obj


def _compact_scratch(scratch: dict) -> dict:
    keep: dict[str, Any] = {}
    for k, v in scratch.items():
        if k in ("notes", "questions", "tool_results", "user_answers", "history"):
            keep[k] = _trim(v[-6:] if isinstance(v, list) else v)
        elif isinstance(v, dict):
            keep[k] = _trim(v)
        else:
            keep[k] = _trim(v)
    return keep
