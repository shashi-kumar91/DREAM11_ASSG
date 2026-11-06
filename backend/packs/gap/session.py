"""Pack B session: the fan returns, and the gap has to be proven before it is described.

Same kernel as Pack A, different epistemics. Pack A verifies a claim the creator already
made; Pack B has to establish *which balls the fan missed* from a player log that may be
lying, then keep everything past the resume cursor out of the artifact. The supervisor
chooses the investigation path and the causal sentence; the boundary, the event list, the
required-rate arithmetic, the frame comparison and the spoiler line are all code's.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ...kernel.commit import Evidence, LabelRule, commit
from ...kernel.orchestrator import Hooks, Orchestrator
from ...kernel.schemas import (Budget, Card, CardDraft, CommitResult, Episode, PendingQuestion,
                               TruthLabel, new_id)
from ...kernel.trace import TraceRecorder
from ..creator.domain import Feed
from . import boundary as B
from . import rates, receipt, voi
from .agents import build_agents
from .domain import GapFixtures, GapScenario, is_material
from .guardrails import validate_receipt
from .tools import GapToolbox


@dataclass
class GapConfig:
    ask_timeout_s: float = 40.0
    budget: Budget = field(default_factory=lambda: Budget(max_llm_calls=10, max_tool_calls=10,
                                                          max_ms=180_000,
                                                          max_specialist_rounds=8))


_MARK_RE = re.compile(r"\bevt_[A-Za-z0-9_]+\b")


class GapSession:
    def __init__(self, llm, fixtures: GapFixtures, trace: TraceRecorder,
                 on_event: Callable[[dict], None], cfg: Optional[GapConfig] = None,
                 auto_answer: Optional[dict[str, str]] = None):
        self.llm = llm
        self.fixtures = fixtures
        self.feed: Feed = fixtures.feed
        self.trace = trace
        self.on_event = on_event
        self.cfg = cfg or GapConfig()
        self.auto_answer = auto_answer or {}
        self.cards: list[Card] = []
        self.verdicts: dict[str, Any] = {}
        self.released_events: set[str] = set()
        self.scenario: Optional[GapScenario] = None
        self.boundary: Optional[B.Boundary] = None
        self.chosen_boundary: str = ""
        self.boundary_source = "telemetry"
        self.fan_pref = "everything"
        self.frame_checks: dict[str, dict] = {}
        self.clip_quotes: dict[str, dict] = {}
        self.tallies: dict[str, str] = {}
        self.evidence_notes: dict[str, str] = {}
        self.current_receipt: Optional[dict] = None
        self._scratch: dict[str, Any] = {}
        self._fan_question: dict[str, str] = {}
        self._cards_at_start = 0
        self._answer: dict[str, Optional[str]] = {}
        self._answer_evt = threading.Event()
        self.toolbox = GapToolbox(self)
        self.registry = self.toolbox.build_registry()
        self.agents = build_agents()
        self.orch = Orchestrator(
            llm, self.registry, self.agents, trace,
            hooks=Hooks(mandatory_check=self._mandatory_check,
                        before_commit=self._before_commit,
                        build_fallback=self._fallback_draft,
                        veto_terminal=self._veto_terminal,
                        wait_for_answer=self._wait_for_answer,
                        observation_context=self._domain_notes),
            require_critic_before_commit=True,
            supervisor_caps={"telemetry", "past_events", "video_evidence", "ask_user",
                            "permission"},
        )

    # ------------------------------------------------------------------ #
    # plumbing the tools call
    # ------------------------------------------------------------------ #

    def note_feed_read(self, rows) -> None:
        self.released_events |= {e.id for e in rows}

    def note_frame_check(self, result: dict) -> None:
        if not result.get("found"):
            return
        self.frame_checks[result["over_label"]] = result
        # The receipt quotes what the reader saw in the pixels, so the read itself is
        # evidence - recorded when the tool ran, not reconstructed at commit time.
        p = result.get("read_from_pixels") or {}
        self.evidence_notes[f"frame:{result['over_label']}"] = (
            f"{result['frame_id']} at {result['over_label']} read from pixels: "
            f"score {p.get('score')} over {p.get('over')} rrr {p.get('rrr')}; "
            f"feed says {result.get('feed_score')}")

    def note_clip_quote(self, result: dict) -> None:
        quote = result.get("quote") or {}
        if quote.get("event_id"):
            self.clip_quotes[quote["event_id"]] = result
            self.evidence_notes[f"clip:{quote['event_id']}"] = (
                f"clip quote {quote['duration_s']}s at {quote['bytes_estimate_kb']} KB, "
                f"{quote['load_policy']}, ball {quote['over_label']}")

    def frame_check_for(self, ev) -> dict:
        """Cached, because the same ball gets checked by the investigator and by the clip."""
        cached = self.frame_checks.get(ev.over_label)
        if cached is not None:
            return cached
        from . import ocr

        frame = self.fixtures.frame_at(ev.t_wall)
        if frame is None:
            return {"found": False, "why": "no frame at this moment"}
        read = ocr.read_frame(frame.image_bytes())
        out = {"found": True, "frame_id": frame.frame_id, "over_label": frame.over_label,
               "read_from_pixels": read, "feed_score": ev.score,
               "agrees": read.get("score") == ev.score,
               "readable": bool(read.get("readable")), "injected_fault": frame.injected_fault}
        self.note_frame_check(out)
        return out

    def resolve_over(self, ref: str) -> Optional[str]:
        """Accept '16.3', 'evt_068', 16.3 or '' and return the event id, clamped."""
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref in self.feed.index:
            return ref
        ev = self.feed.find_by_over(self.normalise_over(ref))
        return ev.id if ev else None

    @staticmethod
    def normalise_over(ref: str) -> str:
        ref = str(ref).strip().lower().replace("over", "").strip()
        if "." not in ref and ref.isdigit():
            return f"{ref}.6"
        return ref

    def ask(self, pq: PendingQuestion) -> Optional[str]:
        return self._wait_for_answer(pq, self.cfg.ask_timeout_s)

    def fan_answer(self) -> dict[str, str]:
        """What the fan said, if the question has already been put to them this scenario."""
        return dict(self._fan_question)

    # ------------------------------------------------------------------ #
    # episode
    # ------------------------------------------------------------------ #

    def run_scenario(self, scenario_id: str) -> Episode:
        # The fixture's scenario objects are shared by every session on this server, and the
        # jury-pane interrupt appends to `scenario.heartbeats`. A shared append would follow
        # this fan out of the room: every later run of S1 would read a stall nobody sat
        # through, its prompts would drift off the recorded cassette keys, and the demo would
        # silently fall back for everyone afterwards. Each session binds its own copy, so the
        # fixture stays the pristine source every recording was keyed against.
        sc = self.fixtures.scenarios[scenario_id].model_copy(deep=True)
        self.scenario = sc
        self._episode_id = new_id("ep")
        self.boundary = B.estimate(self.feed, sc)
        self.chosen_boundary = self.boundary.candidates[0] if self.boundary.candidates else ""
        self.boundary_source = "telemetry"
        self.fan_pref = "everything"
        self.released_events = set()
        self.frame_checks = {}
        self.clip_quotes = {}
        self.tallies = {}
        self.verdicts = {}
        self.evidence_notes = {}
        self._fan_question = {}
        self._cards_at_start = len(self.cards)
        self._scratch = {"is_gap": True, "scenario_id": sc.id}
        ask, why = voi.should_ask(self.feed, self.boundary, sc.resume_cursor, sc.resume_cursor)
        if ask:
            self._kernel_ask(why)
        else:
            # Staying quiet is a decision a jury has to be able to see the reason for.
            self.trace.record("no_question", episode_id=self._episode_id, why=why)
        obs = {
            "trigger": "fan_returned", "scenario": sc.name, "blurb": sc.blurb,
            "interruption_type": sc.interruption_type,
            "resume_cursor": sc.resume_cursor,
            "resume_over": self.feed.by_id[sc.resume_cursor].over_label,
            "boundary_candidates": self.boundary.labels(self.feed),
            "boundary_confidence": self.boundary.confidence,
            "boundary_notes": self.boundary.notes,
            "kernel_value_of_information": {"ask": ask, "why": why},
            "fan_already_answered": dict(self._fan_question) or None,
        }
        self.trace.record("episode_context", scenario_id=sc.id, **{
            k: obs[k] for k in ("interruption_type", "resume_over", "boundary_candidates",
                                "boundary_confidence", "kernel_value_of_information")})
        return self._episode("fan_returned", obs,
                             "reconstruct the missed interval with evidence and pick the "
                             "safest useful recovery. Never invent a boundary, never show a "
                             "ball at or after the fan's resume position, and a no-op is a "
                             "successful outcome when nothing key was missed.")

    def _kernel_ask(self, why: str) -> None:
        """The value-of-information test is code's, so the asking is code's too.

        If the decision to ask were only reachable through a model tool call, a run where the
        supervisor is unavailable would silently skip the fan - and the receipt would be built
        on a boundary the fan never confirmed. The kernel asks first, records the answer as
        evidence, and the agent inherits a settled boundary instead of a fresh guess.
        """
        text, options = self.canonical_question()
        if not text:
            return
        pq = PendingQuestion(text=text, reason=why, options=[str(o) for o in options][:4],
                             blocking=True, expected_info_gain=0.6)
        self.trace.record("ask_user", episode_id=self._episode_id,
                          question=pq.text, options=pq.options, reason=why,
                          note="posed by the kernel before the first supervisor turn")
        answer = self.ask(pq)
        if answer:
            self._fan_question = {"question": pq.text, "answer": answer,
                                  "boundary_source": self.boundary_source}

    def _episode(self, trigger: str, observation: dict, objective: str) -> Episode:
        res = self.orch.run_episode(
            pack="gap", trigger=trigger, objective=objective, observation=observation,
            state_summary=self._state_summary(), on_commit=self._commit_draft,
            budget=self.cfg.budget, scratch=self._scratch, episode_id=self._episode_id)
        seen = {c.id for c in self.cards}
        for c in res.cards:
            if c.id in seen:
                continue
            self.cards.append(c)
            self.on_event({"type": "card", "card": c.model_dump(), "badges": self.badges()})
        if len(self.cards) == self._cards_at_start:
            # The pack's promise outranks the model's mood: a fan who came back to a blank
            # screen has been failed, whether the run exhausted its budget, errored, timed out
            # waiting for an answer or simply lost its nerve. The receipt built here cites only
            # the clamped feed, so it cannot spoil and cannot invent.
            draft = res.scratch.get("fallback_draft") or self.build_card("receipt", why="",
                                                                         clip_event="")
            out = self._commit_draft(draft)
            if res.reason == "supervisor_unavailable":
                # Said plainly, once, instead of leaving a red model error as the last word:
                # the receipt is arithmetic over the clamped feed, so it is complete without a
                # model turn. A fan (and a reviewer) should read that as designed, not broken.
                self.on_event({"type": "llm_absent",
                               "note": "no recorded model turn for this state, so the kernel "
                                       "built the receipt from the feed itself - every number "
                                       "still comes from a cited ball",
                               "committed": out.ok})
            self.on_event({"type": "status",
                           "note": f"deterministic receipt after {res.reason}",
                           "committed": out.ok, "badges": self.badges()})
        self._scratch = {}
        return res.episode

    _episode_id: Optional[str] = None

    # ------------------------------------------------------------------ #
    # hooks
    # ------------------------------------------------------------------ #

    def _veto_terminal(self, action: str, scratch: dict) -> Optional[str]:
        """A fan standing at the resume cursor is owed an answer, so "skip" is refused once.

        The window is already sized by code before the first model turn, so silence is never a
        legitimate outcome of a gap episode - either there is a receipt or there is an explicit
        no-op. The kernel spends its one veto, then the deterministic fallback card is committed.
        """
        if action != "skip" or self.scenario is None:
            return None
        if len(self.cards) > self._cards_at_start:
            return None
        return (f"scenario {self.scenario.id} has a computed gap window; the fan gets a "
                f"receipt or an explicit no-op, never silence")

    def _mandatory_check(self, role: str, payload: Any, scratch: dict) -> Optional[str]:
        if role != "questioner" or not isinstance(payload, dict):
            return None
        if self.scenario is None or self.boundary is None:
            return None
        if self._fan_question:
            # The question was worth asking and it was already asked, before this turn.
            scratch["kernel_note"] = (f"kernel: the fan already answered the boundary question "
                                      f"({self._fan_question['answer'][:60]}); boundary source "
                                      f"is {self.boundary_source}. Do not spend the fan's "
                                      f"patience twice.")
            return None
        ask, why = voi.should_ask(self.feed, self.boundary, self.scenario.resume_cursor,
                                  self.scenario.resume_cursor)
        if ask and not payload.get("ask"):
            scratch["kernel_note"] = ("the boundary candidates give different receipts, so "
                                      "the fan must be asked; the questioner's 'no' was ignored")
            return "kernel: asking is required by the value-of-information test"
        if not ask:
            scratch["kernel_note"] = f"kernel: no question needed - {why}"
            return f"kernel: no question needed - {why}"
        return None

    def _before_commit(self, draft: CardDraft, scratch: dict) -> Optional[CardDraft]:
        """The receipt is always rebuilt from the clamped feed, never taken from model prose."""
        strategy = scratch.get("strategist") or {}
        modality = str(strategy.get("modality") or "receipt").lower()
        if draft.kind in ("noop", "status"):
            modality = {"noop": "no_op", "status": "jump_to_live"}[draft.kind]
        if draft.kind == "receipt":
            modality = "receipt"
        return self.build_card(modality, why=str(strategy.get("why_it_matters") or ""),
                               clip_event=str(strategy.get("offer_clip_event") or ""))

    def _boundary_id(self, resume) -> str:
        """The ball the fan last saw, as an event id.

        A late join has no boundary in the log, and the honest reading of that is not "the
        cursor" (which would make the gap one ball) but "everything this replay can show up to
        the cursor". The agent is never allowed to make this choice - it is arithmetic.
        """
        if self.chosen_boundary:
            return self.chosen_boundary
        if self.boundary is None:
            return resume.id
        if self.boundary.candidates:
            return self.boundary.candidates[0]
        return self.feed.events[0].id

    def canonical_question(self) -> tuple[str, list[str]]:
        """The fan-facing wording of a boundary question is code's, not the model's.

        "Did you see the wicket at ball 16.3 (142/4)?" is answerable; "what did you miss?" is
        not. When the agent asks, it asks this question with these options, because the
        options have to map back onto a candidate boundary for `_apply_answer` to work.
        """
        if self.scenario is None or self.boundary is None:
            return "", []
        sc = self.scenario
        return voi.boundary_question(self.feed, self.boundary, sc.resume_cursor,
                                     sc.resume_cursor, sc.fan_team)

    def build_card(self, modality: str, why: str = "", clip_event: str = "") -> CardDraft:
        sc, feed = self.scenario, self.feed
        resume = feed.by_id[sc.resume_cursor]
        boundary_id = self._boundary_id(resume)
        boundary_ev = feed.by_id[boundary_id]
        missed = B.missed_window(feed, boundary_id, sc.resume_cursor)
        material = voi.receipts_for(feed, [boundary_id], sc.resume_cursor,
                                    sc.resume_cursor)[boundary_id]
        if not material and modality == "receipt":
            # Code outranks the model here: a receipt for a gap with no key action is drama.
            self.trace.record("modality_overridden", from_="receipt", to="no_op",
                              note="no material event inside the verified window")
            modality = "no_op"
            # Say it in the scratchpad too. Without this the supervisor kept re-committing the
            # same receipt because nothing in its context told it the kernel had already ruled.
            self._scratch.setdefault("kernel_notes", []).append(
                "kernel: the verified window has no key action, so this card can only be a "
                "no-op/back-to-live. Do not commit a receipt for it again.")
        if not missed:
            # Nothing between the boundary and the resume ball: the honest card is "you are
            # back, nothing was missed", and the supervisor is told so once.
            self._scratch.setdefault("kernel_notes", []).append(
                "kernel: the gap window is empty, so the only truthful card is back-to-live.")
            return receipt.jump_to_live(resume)
        if modality == "no_op":
            # Even "nothing happened" states an arithmetic result, so it gets a tally too.
            noop_id = f"noop_{sc.id}_{boundary_id}_{resume.id}"
            self.tallies[noop_id] = receipt.no_op_summary(boundary_ev, resume, missed)
            return receipt.no_op(boundary_ev, resume, missed, tally_id=noop_id)

        rate = rates.change(feed.meta.target, boundary_ev, resume)
        summary = receipt.gap_summary(boundary_ev, resume, missed, rate)
        tally_id = f"gap_{sc.id}_{boundary_id}_{resume.id}"
        self.tallies[tally_id] = summary
        clip = self.clip_quotes.get(clip_event) if clip_event else None
        checks = [f"{c['over_label']} read {c['read_from_pixels']['score']}"
                  f"{' == feed' if c['agrees'] else ' != feed ' + str(c['feed_score'])}"
                  for c in self.frame_checks.values() if c.get("found")]
        clip_ok = bool(clip and clip.get("offerable"))
        # The fan's answer to the value-of-information question has to change the artifact, or
        # the question only cost them patience: "key moments" hides the rest of the window.
        key_only = self.fan_pref == "key_only"
        others = [] if key_only else [e.over_label for e in missed if not is_material(e)]
        scope = ("Key moments only, as you asked - the rest of the window is in the tally."
                 if key_only else "")
        return receipt.receipt(material, missed, boundary_ev, resume, rate, tally_id, why=why,
                               clip=clip["quote"] if clip_ok else None,
                               frame_checks=checks, fan_saw=self.boundary_source == "fan",
                               scope_note=scope, other_labels=others)

    def _commit_draft(self, draft: CardDraft):
        sc, feed = self.scenario, self.feed
        clamped = feed.clamped(sc.resume_cursor)
        resume = feed.by_id[sc.resume_cursor]
        cited_events = [e for e in draft.cited_event_ids if e in self.released_events]
        missing = [e for e in draft.cited_event_ids if e not in self.released_events]
        if missing:
            self.note_feed_read([feed.by_id[m] for m in missing if m in feed.index])
            cited_events += [m for m in missing if m in feed.index]
        why = str((self._scratch.get("strategist") or {}).get("why_it_matters") or "")
        # A justification that names a real ball is a citation, not a defect: if the model's
        # sentence cites a feed event that sits inside the fan's own window, the receipt should
        # carry that event. Rejecting the card instead was a live failure - the deterministic
        # receipt was refused because the *sentence* cited the boundary ball, and the fan ended
        # with no receipt at all. Anything the feed cannot license, or that sits at/after the
        # resume cursor, still fails below.
        for eid in _MARK_RE.findall(why or ""):
            if eid in feed.index and eid not in cited_events and eid != sc.resume_cursor:
                if feed.index[eid] < feed.index[sc.resume_cursor]:
                    cited_events.append(eid)
                    self.evidence_notes[f"why:{eid}"] = (
                        f"the justification named {feed.by_id[eid].over_label}, so the ball is "
                        f"cited too")
        draft = draft.model_copy(update={"cited_event_ids": cited_events})

        violations = validate_receipt(cited_events, sc.resume_cursor, feed, why=why,
                                      end_over=resume.over_label)
        # `commit` also refuses an uncited card; this check runs first because a spoiler is a
        # different class of failure than a missing citation.
        if violations:
            self.trace.record("commit", episode_id=self._episode_id, ok=False,
                              draft=draft.model_dump(),
                              violations=[v.model_dump() for v in violations], evidence_keys=[])
            return CommitResult(ok=False, violations=violations)

        ev = Evidence()
        for eid in cited_events:
            e = clamped.get(eid)     # the clamp is the authority: beyond-cursor ids get no text
            if e is None:
                continue
            ev.event_ids.add(eid)
            ev.add(eid, f"{e.over_label} {e.score} runs={e.runs} rrr={e.rrr} {e.text}")
        for tid in draft.cited_tally_ids:
            if tid in self.tallies:
                ev.tally_ids.add(tid)
                ev.add(tid, self.tallies[tid])
        for key, note in self.evidence_notes.items():
            ev.add(key, note)

        result = commit(draft, episode_id=self._episode_id or "ep", evidence=ev,
                        feed=clamped,
                        label_rule=LabelRule(self._label), attribution="gap recovery",
                        timestamp_label=resume.over_label, source_badges=self.badges(),
                        scratch=self._scratch)
        self.trace.record("commit", episode_id=self._episode_id, ok=result.ok,
                          card_id=result.card.id if result.card else "",
                          draft=draft.model_dump(),
                          violations=[v.model_dump() for v in result.violations],
                          evidence_keys=sorted(ev.texts))
        if result.ok and result.card:
            self.cards.append(result.card)
            self.current_receipt = result.card.model_dump()
            self.on_event({"type": "card", "card": result.card.model_dump(),
                           "badges": self.badges()})
        return result

    def _fallback_draft(self, scratch: dict) -> Optional[CardDraft]:
        """No model turn survived: still give the fan the truth about the gap."""
        return self.build_card("receipt", why="", clip_event="")

    def _wait_for_answer(self, pq: PendingQuestion, timeout_s: float) -> Optional[str]:
        if self._fan_question:
            # The kernel already put this question to the fan and they answered. A second ask
            # would cost the fan another timeout and buy nothing: the boundary is settled, so
            # the honest reply to a repeat is the answer already on file.
            self.trace.record("ask_refused", episode_id=self._episode_id,
                              question=pq.text, answer=self._fan_question["answer"],
                              note="the fan had already been asked this by the kernel")
            return self._fan_question["answer"]
        # The question is shown whatever answers it: a fixture answer is a recorded fan, not a
        # reason to hide the ask from the demo.
        self.on_event({"type": "ask_user", "question": pq.model_dump()})
        for k, v in self.auto_answer.items():
            if k.lower() in pq.text.lower():
                self.trace.record("auto_answer", question=pq.text, answer=v,
                                  note="fixture fan answer used by the headless driver")
                return self._apply_answer(v)
        self._answer_evt.clear()
        # The kernel suggests a wait; the pack declares its own fan's patience, so the config
        # wins. A hard-coded ceiling here is how a live demo ends an episode with no receipt.
        got = self._answer_evt.wait(timeout=self.cfg.ask_timeout_s)
        ans = self._answer.get("a")
        self._answer = {}
        return self._apply_answer(ans) if got and ans else None

    def provide_answer(self, answer: str) -> None:
        self._answer["a"] = answer
        self._answer_evt.set()

    def _apply_answer(self, answer: str) -> str:
        """Turn a fan's words into a boundary choice - deterministically, by over label.

        Both useful options name a ball ("Yes, I saw ball 16.3" / "No, I left at 16.2"), so
        the label inside the answer is the boundary. A label that is not one of the
        candidates is refused rather than trusted, and an answer with no label at all only
        moves the preference, never the window.
        """
        text = (answer or "").lower()
        chosen = ""
        for lab in re.findall(r"\d{1,2}\.\d", text):
            eid = self.resolve_over(lab)
            if eid and self.boundary is not None and eid in self.boundary.candidates:
                chosen = eid
                break
        if chosen:
            self.chosen_boundary = chosen
            self.boundary_source = "fan"
            ev = self.feed.by_id[chosen]
            self.evidence_notes[f"fan:{chosen}"] = (
                f"fan reported last seen ball {ev.over_label} ({ev.score})")
            self.trace.record("boundary_from_fan", event_id=chosen, answer=answer[:120])
            if self.scenario is not None and ev.t_wall > self.scenario.resume_t:
                self.uncertainty("fan_answer_beyond_cursor", f"answer '{answer[:60]}' refers to "
                                   f"{ev.over_label}, after the resume "
                                   f"position; ignored")
        if "wicket" in text or "only" in text or "key moment" in text or "team" in text:
            self.fan_pref = "key_only"
        elif "every" in text or "all" in text or "recap" in text:
            self.fan_pref = "everything"
        self.evidence_notes[f"pref:{self.scenario.id if self.scenario else '-'}"] = (
            f"fan asked for {self.fan_pref} coverage")
        return answer

    def uncertainty(self, flag: str, detail: str) -> None:
        self.trace.record("uncertainty", flag=flag, detail=detail)

    # ------------------------------------------------------------------ #
    # presentation
    # ------------------------------------------------------------------ #

    def _label(self, draft: CardDraft, scratch: dict) -> tuple[TruthLabel, str]:
        if draft.kind == "noop":
            return TruthLabel.EVIDENCED, "no_op_template"
        if draft.kind == "status":
            return TruthLabel.EVIDENCED, "jump_to_live_template"
        checked = any(c.get("agrees") for c in self.frame_checks.values())
        if draft.cited_tally_ids:
            return (TruthLabel.VERIFIED if checked else TruthLabel.EVIDENCED), "gap_receipt_template"
        return TruthLabel.UNVERIFIABLE, "unverifiable_template"

    def _state_summary(self) -> str:
        sc = self.scenario
        return (f"scenario={sc.id if sc else '-'} | resume at "
                f"{sc.resume_cursor if sc else '-'} | boundary candidates="
                f"{self.boundary.labels(self.feed) if self.boundary else '-'} conf="
                f"{self.boundary.confidence if self.boundary else '-'} | chosen="
                f"{self.feed.by_id[self.chosen_boundary].over_label if self.chosen_boundary in self.feed.by_id else '-'}"
                f" ({self.boundary_source}) | frames checked={len(self.frame_checks)} | "
                f"cards so far={len(self.cards)}")

    def _domain_notes(self, scratch: dict) -> str:
        sc = self.scenario
        resume = self.feed.by_id[sc.resume_cursor] if sc else None
        return ("Every digit on a receipt is computed by code from the clamped feed and the "
                "gap tally; your prose is replaced by a template, so do not spend tokens "
                "describing scores. Cite event ids in the causal sentence as [evt_068]. "
                f"Fan resume position: {resume.over_label if resume else '-'}; nothing at or "
                "after it may be mentioned. The boundary is uncertain only if the telemetry "
                "says so - never invent a new candidate.")

    def badges(self) -> dict[str, str]:
        return {"asr": "n/a", "llm": getattr(self.llm, "mode_used", "unknown"),
                "feed": "replay_fixture", "telemetry": "simulated",
                "frames": "rendered_fixture"}
