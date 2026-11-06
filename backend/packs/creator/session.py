"""Pack A session: clock, memory, agent wiring and the commit path.

One episode = one perception unit (an utterance, a fan query, or a prediction settlement).
The supervisor decides the path; this class owns the state, the evidence store and the
deterministic gates.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ...kernel.commit import Evidence, LabelRule, commit
from ...kernel.orchestrator import Hooks, Orchestrator
from ...kernel.schemas import (Budget, Card, CardDraft, Episode, InsightType, MatchMeta,
                               Prediction, TerminalState, TruthLabel, Uncertainty, Utterance,
                               Verdict, VerdictKind, new_id)
from ...kernel.trace import TraceRecorder
from .agents import build_agents
from .domain import Feed
from .policy import FanContext, SurfacePolicy
from .predictions import Ledger, Settlement
from .templates import (conflict, fmt, prediction_card, query_answer, settlement_card,
                        quote_card, unverifiable, verified)
from .tools import CreatorToolbox
from .triage import content_tokens, triage
from .verify import mk_verdict

SYNONYMS = {
    "bowling": ["bowler", "bowls", "bowled", "spin", "pace", "overs", "death"],
    "bowler": ["bowling", "bowl", "spin", "pace"],
    "death": ["overs", "last", "end", "yorker", "spell"],
    "weather": ["rain", "sky", "clouds", "drizzle"],
    "field": ["boundary", "inner", "circle", "slip"],
    "review": ["drs", "umpire", "tracking", "decision"],
}


@dataclass
class SessionConfig:
    asr_conf_floor: float = 0.60
    salience_floor: float = 0.45
    auto_answer: Optional[dict[str, str]] = None
    answer_wait_s: float = 40.0
    budget: Budget = field(default_factory=lambda: Budget(max_llm_calls=12, max_tool_calls=8,
                                                          max_ms=30_000))


class CreatorSession:
    def __init__(self, llm, feed: Feed, asr, transcript_rows: list[dict],
                 trace: TraceRecorder, on_event: Callable[[dict], None],
                 cfg: Optional[SessionConfig] = None, meta: Optional[MatchMeta] = None):
        self.llm = llm
        self.feed = feed
        self.asr = asr
        self.trace = trace
        self.on_event = on_event
        self.cfg = cfg or SessionConfig()
        self.rows = transcript_rows
        self.t: float = 0.0
        self.history: list[Utterance] = []
        self.ledger = Ledger()
        self.cards: list[Card] = []
        self.surface_times: list[float] = []
        self.uncertainties: list[Uncertainty] = []
        self.fan = FanContext()
        self.verdicts: dict[str, Verdict] = {}
        self.released_events: set[str] = set()
        self.current_utterance: Optional[Utterance] = None
        self.not_evidence: set[str] = set()
        self.pending: dict[str, Any] = {}
        self._answer: dict[str, Optional[str]] = {}
        self._answer_evt = threading.Event()
        self._scratch: dict[str, Any] = {}
        self.mode = "watch"
        self.clip_grants = 0
        self.toolbox = CreatorToolbox(self)
        self.registry = self.toolbox.build_registry()
        self.agents = build_agents()
        self.policy = SurfacePolicy(salience_floor=self.cfg.salience_floor)
        self.orch = Orchestrator(
            llm, self.registry, self.agents, trace,
            hooks=Hooks(mandatory_check=self._mandatory_check,
                        before_commit=self._before_commit,
                        build_fallback=self._fallback_draft,
                        wait_for_answer=self._wait_for_answer,
                        observation_context=self._domain_notes,
                        veto_terminal=self._veto_terminal),
            require_critic_before_commit=True,
        )

    # ------------------------------------------------------------------ #
    # feed / memory plumbing used by tools
    # ------------------------------------------------------------------ #

    def clamped(self):
        st = self.feed.state_at(self.t)
        return self.feed.clamped(st.last_event_id if st else self.feed.events[0].id)

    def note_feed_read(self, rows) -> None:
        self.released_events |= {e.id for e in rows}

    def store_verdict(self, v: Verdict) -> None:
        self.verdicts[v.id] = v

    def store_utterance(self, u: Utterance) -> None:
        self.history.append(u)

    def transcript_search(self, query: str, limit: int = 4) -> list[dict]:
        qtok = content_tokens(query)
        expanded = set(qtok)
        for tok in qtok:
            expanded |= set(SYNONYMS.get(tok, []))
            if len(tok) >= 4:
                expanded.add(tok[:4])
        scored: list[tuple[float, Utterance, str]] = []
        for u in self.history:
            # A segment tier-0 threw away, or that ASR itself was unsure of, is not evidence
            # for an answer. Retrieval must not undo the abstention decision.
            if u.id in self.not_evidence:
                continue
            ut = content_tokens(u.text)
            base = len(expanded & ut) + len(qtok & ut)
            if base == 0:
                continue
            snippet = best_sentence(expanded | qtok, u.text)
            bonus = 1.5 if snippet.lower() != u.text.lower() else 0.0
            scored.append((base + bonus + 0.4 * len(u.text.split()) ** 0.5 / 4, u, snippet))
        scored.sort(key=lambda x: -x[0])
        return [{"id": u.id, "t_start": round(u.t_start, 1), "timestamp": fmt(u.t_start),
                 "excerpt": snip, "speaker": u.speaker, "asr_confidence": u.asr_confidence,
                 "verbatim": True}
                for _, u, snip in scored[:max(1, limit)] if _ >= 1.0]

    # ------------------------------------------------------------------ #
    # ingestion
    # ------------------------------------------------------------------ #

    def ingest(self, row: dict) -> Utterance:
        """Feed one creator audio segment through ASR and store the result."""
        audio = row.get("audio_bytes")
        res = self.asr.transcribe(audio or b"", self.feed.meta.keyterms, hint_id=row["id"])
        u = Utterance(id=row["id"], t_start=float(row["t_start"]), t_end=float(row["t_end"]),
                      text=res.text.strip(), speaker=self.feed.meta.creator,
                      asr_confidence=round(float(res.confidence), 3), asr_source=res.source,
                      keyterms_applied=self.feed.meta.keyterms[:6],
                      raw_provider_text=res.text)
        self.store_utterance(u)
        self.on_event({"type": "utterance", "utterance": u.model_dump(),
                       "asr_model": res.model, "uncertain_words": res.uncertain_words,
                       "badges": self.badges()})
        # A blank transcript is not evidence, whatever confidence the provider attached to it.
        # Gemini returns empty text with confidence 1.0 on clipped or noisy audio, so a gate
        # that only reads the number would hand the supervisor an utterance of nothing - and a
        # run that "verified" nothing is exactly the fabrication this kernel exists to stop.
        blank = not u.text.strip()
        low = res.confidence < self.cfg.asr_conf_floor
        if blank or low:
            reason = "asr_empty_transcript" if blank else "asr_confidence_below_floor"
            self.uncertainties.append(Uncertainty(kind="asr_empty" if blank else "asr_low_conf",
                                                  detail=f"{u.id} conf={res.confidence:.2f} "
                                                         f"'{res.text[:40]}' ({reason})",
                                                  raised_by_agent="kernel"))
            self.not_evidence.add(u.id)
            self.trace.record("asr_abstain", utterance_id=u.id, confidence=res.confidence,
                              transcript=res.text[:80], reason=reason,
                              action="no episode run; nothing invented")
            self.on_event({"type": "abstain", "utterance_id": u.id, "reason": reason,
                           "confidence": res.confidence, "transcript": res.text[:80]})
            return u
        return u

    def advance(self, t: float) -> None:
        """Move the clock and settle any prediction whose over has just completed."""
        self.t = max(self.t, t)
        for s in self.ledger.settle_due(self.feed, self.t):
            pred = next((p for p in self.ledger.closed if p.id == s.prediction_id), None)
            if pred is not None:
                self.run_settlement(pred, s)

    def process(self, row: dict) -> Optional[Episode]:
        """ASR -> abstain gate -> Tier-0 -> agent episode. Returns None when nothing ran."""
        u = self.ingest(row)
        if not u.text.strip() or u.asr_confidence < self.cfg.asr_conf_floor:
            return None
        return self.run_utterance(u)

    def run_utterance(self, u: Utterance) -> Episode:
        """Tier-0 first; only utterances that survive get an agent episode."""
        self.current_utterance = u
        self.released_events = set()
        self._scratch = {}
        out = triage(u, self.history[:-1], self.cfg.asr_conf_floor)
        self.trace.record("triage", utterance_id=u.id, skip=out.skip, reason=out.reason,
                          distinctive=out.distinctive, asr_confidence=u.asr_confidence)
        if out.skip:
            ep = Episode(id=new_id("ep"), pack="creator", trigger=f"triage:{u.id}",
                         terminal=TerminalState.SKIPPED, terminal_reason=out.reason)
            self.not_evidence.add(u.id)
            self.trace.record("episode_skipped_tier0", episode_id=ep.id, utterance_id=u.id,
                              reason=out.reason)
            self.on_event({"type": "tier0_skip", "utterance_id": u.id, "reason": out.reason,
                           "note": "no LLM call was made for this utterance"})
            return ep
        obs = {"trigger": "creator_utterance", "utterance_id": u.id, "text": u.text,
               "spoken_at": fmt(u.t_start), "t": round(self.t, 1),
               "asr_confidence": u.asr_confidence, "asr_source": u.asr_source,
               "triage_distinctive_terms": out.distinctive,
               "match_state": self._state_dict()}
        precheck = self._prescreen(u)
        if precheck:
            obs["kernel_verdict_before_any_model_call"] = precheck
        return self._episode("creator_utterance", obs,
                            "maximise useful, attributed creator insight per fan attention and "
                            "byte budget without inventing facts, leaking spoilers or turning "
                            "opinion into fact. Staying silent is a valid outcome.")

    def _prescreen(self, u: Utterance) -> Optional[dict]:
        """Verify a spoken score before any model is allowed to decide it is unimportant.

        Placing this here rather than in `mandatory_check` is the difference between
        "the kernel verifies claims" and "the kernel verifies claims the analyst happened
        to notice". The conflict card is the product's whole credibility, so a score shape
        in the transcript is checked on sight.
        """
        from .verify import spoken_score_claim
        from .verify import verify as dverify

        claim = spoken_score_claim(u.text)
        if claim is None or "verdict" in self._scratch:
            return None
        v = dverify(claim, u.text, self.feed, self.feed.state_at(self.t))
        self.store_verdict(v)
        self._scratch["verdict"] = v.model_dump()
        self.trace.record("kernel_verifier", utterance_id=u.id, verdict=v.model_dump(),
                          note="ran before the first supervisor turn, because the transcript "
                               "literally contains a score; no model had to notice it")
        return v.model_dump()

    def run_query(self, query: str) -> Episode:
        self.current_utterance = None
        self.released_events = set()
        self._scratch = {"is_query": True, "query": query}
        hits = self.transcript_search(query, 4)
        obs = {"trigger": "fan_query", "query": query, "t": round(self.t, 1),
               "segments_stored": len(self.history),
               "deterministic_retrieval": {"matches": len(hits), "hits": hits},
               "match_state": self._state_dict()}
        return self._episode("fan_query", obs,
                             "answer the fan only from the creator's own attributed transcript. "
                             "If the creator never said it, say so - never synthesise an answer. "
                             "The retrieval result in the observation is code's own search of "
                             "the transcript; treat a zero-match search as settled and commit "
                             "the honest no-answer card.")

    def run_settlement(self, pred: Prediction, s: Settlement) -> Episode:
        u = next((x for x in self.history if x.id == pred.utterance_id), None)
        self.current_utterance = u
        self.released_events = set()
        self._scratch = {"settlement": s.model_dump(), "prediction": pred.model_dump(),
                         "tally_text": s.tally_text, "tally_id": s.tally_id}
        obs = {"trigger": "prediction_settled", "prediction_id": pred.id,
               "status": s.status, "tally": s.tally_text, "over": pred.over,
               "bowler": pred.bowler, "t": round(self.t, 1),
               "creator_prediction_words": (u.text if u else ""),
               "match_state": self._state_dict()}
        self.trace.record("prediction_settled", prediction_id=pred.id, status=s.status,
                          tally=s.tally_text, event_ids=s.event_ids)
        return self._episode("prediction_settled", obs,
                             "a creator prediction just settled against the feed. Decide whether "
                             "the fan needs to be told now. The tally is computed by code.")

    # ------------------------------------------------------------------ #
    # episode execution
    # ------------------------------------------------------------------ #

    def _episode(self, trigger: str, observation: dict, objective: str) -> Episode:
        self._episode_id = new_id("ep")
        res = self.orch.run_episode(
            pack="creator", trigger=trigger, objective=objective, observation=observation,
            state_summary=self._state_summary(), on_commit=self._commit_draft,
            budget=self.cfg.budget, scratch=self._scratch, episode_id=self._episode_id)
        # The kernel decides when a card is owed (exhausted, failed, or a vetoed skip that
        # still ended empty); this side only has to honour it.
        if res.scratch.get("fallback_draft"):
            out = self._commit_draft(res.scratch["fallback_draft"])
            if out.ok and out.card:
                res.cards.append(out.card)
            self.on_event({"type": "status", "note": f"deterministic fallback after {res.reason}",
                           "badges": self.badges()})
        for c in res.cards:
            self._surface(c, trigger)
        self._scratch = {}
        return res.episode

    _episode_id: Optional[str] = None

    def _commit_draft(self, draft: CardDraft):
        scratch = self._scratch
        u = self.current_utterance
        ids = list(draft.cited_utterance_ids)
        if u and u.id not in ids:
            ids.append(u.id)
        cited_verdicts = [vid for vid in draft.cited_verdict_ids if vid in self.verdicts]
        if scratch.get("verdict") and scratch["verdict"]["id"] not in cited_verdicts:
            cited_verdicts.append(scratch["verdict"]["id"])
        cited_tallies = [t for t in draft.cited_tally_ids if t in self.ledger.tallies]
        if scratch.get("tally_id") and scratch["tally_id"] not in cited_tallies:
            cited_tallies.append(scratch["tally_id"])
        cited_events = [e for e in draft.cited_event_ids if e in self.released_events]
        for vid in cited_verdicts:
            v = self.verdicts[vid]
            if v.matched_event_id and v.matched_event_id in self.released_events \
                    and v.matched_event_id not in cited_events:
                cited_events.append(v.matched_event_id)
        feed = self.clamped()
        if cited_verdicts:
            # Verified and conflict cards quote the position the fan is actually at, so the
            # event behind the current clamped state is part of that claim's evidence. Adding
            # it here keeps the over label on the card code-derived instead of model-authored.
            st = self.feed.state_at(self.t)
            if st and st.last_event_id and st.last_event_id not in cited_events \
                    and feed.get(st.last_event_id) is not None:
                cited_events.append(st.last_event_id)
        draft = draft.model_copy(update={"cited_utterance_ids": ids,
                                         "cited_verdict_ids": cited_verdicts,
                                         "cited_tally_ids": cited_tallies,
                                         "cited_event_ids": cited_events})

        ev = Evidence()
        for uid in ids:
            row = self._utterance_by_id(uid)
            if row:
                ev.add(uid, f"{fmt(row.t_start)} {row.t_start:.1f} {row.text}")
        for eid in cited_events:
            e = feed.get(eid)          # the clamp is the authority; beyond-cursor ids drop out
            if e is None:
                continue
            ev.event_ids.add(eid)
            ev.add(eid, f"{e.over_label} {e.score} runs={e.runs} rrr={e.rrr} {e.text}")
        for vid in cited_verdicts:
            v = self.verdicts[vid]
            ev.verdict_ids.add(vid)
            ev.add(vid, f"claimed {v.claimed} observed {v.observed} at {v.matched_over or ''} "
                        f"age {v.age_balls} balls {v.detail}")
        for tid in cited_tallies:
            ev.tally_ids.add(tid)
            ev.add(tid, self.ledger.tallies[tid])
        if scratch.get("prediction"):
            # The prediction card quotes the ledger's own scope ("over 17", "2+ yorkers").
            # Those digits are code's, extracted and validated before this episode, so the
            # ledger entry is evidence - without it the gate rejects its own template.
            p = Prediction.model_validate(scratch["prediction"])
            ev.add(p.id, f"prediction {p.text} over {p.over} bowler {p.bowler or ''} "
                         f"{p.metric or ''}>={p.threshold if p.threshold is not None else ''} "
                         f"logged at {fmt(p.t_logged)} {p.t_logged:.1f}")

        result = commit(draft, episode_id=self._episode_id or "ep", evidence=ev, feed=feed,
                        label_rule=LabelRule(self._label), attribution=self.feed.meta.creator,
                        timestamp_label=fmt(u.t_start) if u else fmt(self.t),
                        source_badges=self.badges(), scratch=scratch)
        self.trace.record("commit", episode_id=self._episode_id, ok=result.ok,
                          card_id=result.card.id if result.card else "",
                          draft=draft.model_dump(),
                          violations=[v.model_dump() for v in result.violations],
                          evidence_keys=sorted(ev.texts))
        if result.ok and result.card:
            self.cards.append(result.card)
            self.on_event({"type": "card", "card": result.card.model_dump(),
                           "badges": self.badges()})
        return result

    # ------------------------------------------------------------------ #
    # hooks
    # ------------------------------------------------------------------ #

    def _veto_terminal(self, action: str, scratch: dict) -> Optional[str]:
        """A contradiction may not be skipped. Everything else may stay quiet."""
        v = scratch.get("verdict") or {}
        if v.get("kind") == VerdictKind.CONFLICT.value:
            return (f"the deterministic verifier already found that this utterance contradicts "
                    f"the feed (claimed {v.get('claimed')}, observed {v.get('observed')}). "
                    f"A creator stating a *different* score is the most valuable line in the "
                    f"feed; it must be committed as the conflict card, not skipped")
        return None

    def _mandatory_check(self, role: str, payload: Any, scratch: dict) -> Optional[str]:
        if role != "analyst" or not isinstance(payload, dict):
            return None
        it = payload.get("insight_type")
        u = self.current_utterance
        if it == InsightType.factual_claim.value and u is not None and "verdict" not in scratch:
            from .verify import StructuredClaim
            from .verify import verify as dverify
            claim = StructuredClaim.model_validate(payload.get("claim") or {})
            v = dverify(claim, u.text, self.feed, self.feed.state_at(self.t))
            self.store_verdict(v)
            scratch["verdict"] = v.model_dump()
            self.trace.record("kernel_verifier", utterance_id=u.id, verdict=v.model_dump(),
                              note="ran deterministically because the analyst called this a "
                                   "factual claim; the supervisor could not skip it")
            return f"kernel ran the deterministic verifier: {v.kind.value}"
        if it == InsightType.prediction.value and u is not None and not scratch.get("prediction"):
            from .predictions import PredictionExtraction, complete_from_text
            ex = PredictionExtraction.model_validate(payload.get("prediction") or {})
            # The creator's sentence often states the count in Hinglish ("ek six") while the
            # model leaves `threshold` empty. Reading it back out of the quoted evidence is a
            # completion, not a guess, and it is reported so nobody mistakes it for model output.
            ex, filled = complete_from_text(ex, u.text)
            if filled:
                self.trace.record("prediction_completed", utterance_id=u.id, filled=filled,
                                  note="fields read out of the creator's own sentence")
            nxt = self.feed.next_over_bowler(self.t)
            if not (ex.bowler or "").strip() and nxt:
                ex = ex.model_copy(update={"bowler": nxt})
            if not (ex.text or "").strip():
                ex = ex.model_copy(update={"text": u.text[:300]})
            p, why = self.ledger.log(ex, u.id, self.t, self.feed, nxt)
            if p:
                self.record_prediction(p)
                return (f"kernel logged prediction {p.id} ({why})"
                        + (f"; completed from the sentence: {', '.join(filled)}" if filled else ""))
            return (f"prediction rejected by ledger validation: {why}; next over bowler is "
                    f"{nxt or 'unknown'}, fan clock "
                    f"{(self.feed.state_at(self.t).over_label if self.feed.state_at(self.t) else '?')}")
        return None

    def _before_commit(self, draft: CardDraft, scratch: dict) -> Optional[CardDraft]:
        """Cards that carry numbers are generated from templates, not from model prose."""
        u = self.current_utterance
        v = scratch.get("verdict")
        if draft.kind == "settlement" and scratch.get("settlement") and u:
            pred = Prediction.model_validate(scratch["prediction"] or {})
            s = Settlement.model_validate(scratch["settlement"])
            return settlement_card(u, pred, s)
        if draft.insight_type == InsightType.factual_claim and v and u:
            return self._verdict_card(draft, v, u)
        if v and u and v.get("kind") in {VerdictKind.CONFIRMED.value, VerdictKind.CONFIRMED_LAG.value,
                                        VerdictKind.CONFLICT.value}:
            # The supervisor labelled this a quote even though the kernel already verified a
            # factual claim in this episode. A verdict cannot be downgraded into prose: the
            # label is code's to assign, and the digits only exist inside the template.
            return self._verdict_card(draft, v, u)
        if draft.insight_type == InsightType.prediction and u:
            p = scratch.get("prediction")
            if p:
                return prediction_card(u, Prediction.model_validate(p))
        if draft.insight_type == InsightType.quote and u:
            return quote_card(u, draft.body[:100])
        if draft.kind == "query_answer" and self._scratch.get("is_query"):
            return self._query_card(self._scratch["query"])
        return draft

    def _query_card(self, query: str) -> CardDraft:
        """Extractive answer, or a cited record of having found nothing.

        The abstain card has no utterance to point at, so it cites the deterministic search
        verdict itself - "I searched N stored segments and none match" is evidence, and the
        commit gate should be able to check that claim like any other.
        """
        hits = self.transcript_search(query, 4)
        rows = [(self._utterance_by_id(h["id"]), h["excerpt"]) for h in hits
                if self._utterance_by_id(h["id"])]
        if rows:
            return query_answer(query, rows)
        existing = next((v for v in self.verdicts.values()
                         if v.claimed == query and v.detail.startswith("no attributed")), None)
        if existing is not None:
            return query_answer(query, []).model_copy(
                update={"cited_verdict_ids": [existing.id]})
        v = mk_verdict(kind=VerdictKind.UNVERIFIABLE, claimed=query,
                    observed=f"{len(self.history)} stored segments searched",
                    detail="no attributed transcript segment matches this query; "
                           "nothing was invented to fill the gap")
        self.store_verdict(v)
        self.trace.record("query_no_evidence", query=query, verdict=v.model_dump(),
                          segments_searched=len(self.history))
        return query_answer(query, []).model_copy(update={"cited_verdict_ids": [v.id]})

    def _verdict_card(self, draft: CardDraft, v: dict, u: Utterance) -> CardDraft:
        if v["id"] not in self.verdicts:
            self.store_verdict(Verdict.model_validate(v))
        verdict = self.verdicts[v["id"]]
        over = self._state_dict().get("over_label", "")
        if verdict.kind == VerdictKind.CONFLICT:
            return conflict(u, verdict, over)
        if verdict.kind in (VerdictKind.CONFIRMED, VerdictKind.CONFIRMED_LAG):
            return verified(u, verdict, over)
        return unverifiable(u, verdict)

    def _fallback_draft(self, scratch: dict) -> Optional[CardDraft]:
        """The deterministic safety net when the supervisor ran out of budget or provider.

        It may only ever emit a card whose numbers came from code, so this is the template
        path and nothing else. Silence is preferred over a card we cannot license.
        """
        u = self.current_utterance
        v = scratch.get("verdict")
        if scratch.get("is_query"):
            # The answer to a fan query is retrieved by code, so the card can be too. A
            # provider that dies mid-episode must not turn "we found nothing" into silence.
            return self._query_card(scratch.get("query", ""))
        if v and u and v.get("kind") in {VerdictKind.CONFIRMED.value,
                                        VerdictKind.CONFIRMED_LAG.value,
                                        VerdictKind.CONFLICT.value}:
            return self._verdict_card(CardDraft(headline="", body="", insight_type=
                                                InsightType.factual_claim, kind="insight"), v, u)
        p = scratch.get("prediction")
        if p and u:
            return prediction_card(u, Prediction.model_validate(p))
        return None

    def _wait_for_answer(self, pq, timeout_s: float) -> Optional[str]:
        if self.cfg.auto_answer:
            for k, v in self.cfg.auto_answer.items():
                if k.lower() in pq.text.lower():
                    self.trace.record("auto_answer", question=pq.text, answer=v,
                                      note="fixture answer used by the test harness")
                    return v
        self.pending["question"] = pq
        self.on_event({"type": "ask_user", "question": pq.model_dump()})
        self._answer_evt.clear()
        got = self._answer_evt.wait(timeout=timeout_s)
        ans = self._answer.get("a")
        self._answer = {}
        return ans if got else None

    def provide_answer(self, answer: str) -> None:
        self._answer["a"] = answer
        self._answer_evt.set()

    def record_prediction(self, p: Prediction) -> None:
        """A prediction the ledger accepted is evidence for *this* episode.

        Both routes into the ledger have to do this. When only `_mandatory_check` did, a
        supervisor that logged the prediction through the tool committed a card quoting
        "over 17" with nothing in the evidence carrying 17 - and the gate correctly refused
        the pack's own template.
        """
        self._scratch["prediction"] = p.model_dump()
        self.publish_prediction(p)

    def publish_prediction(self, p: Prediction) -> None:
        self.on_event({"type": "prediction", "prediction": p.model_dump()})

    # ------------------------------------------------------------------ #
    # surfacing
    # ------------------------------------------------------------------ #

    def _surface(self, c: Card, trigger: str) -> None:
        draft = CardDraft(**{k: v for k, v in c.model_dump().items()
                            if k in CardDraft.model_fields})
        d = self.policy.decide(draft, self.t, self.surface_times, self.fan)
        if d.mode == "card":
            self.surface_times.append(self.t)
            self.on_event({"type": "surface", "card_id": c.id, "mode": "card", "reason": d.reason})
        elif d.mode == "collapsed":
            self.on_event({"type": "surface", "card_id": c.id, "mode": "collapsed",
                           "reason": d.reason})
        else:
            self.trace.record("stored_not_surfaced", card_id=c.id, reason=d.mode,
                              policy=d.reason, salience=c.salience)
            self.on_event({"type": "surface", "card_id": c.id, "mode": "silent",
                           "reason": d.reason})

    # ------------------------------------------------------------------ #
    # presentation helpers
    # ------------------------------------------------------------------ #

    def _label(self, draft: CardDraft, scratch: dict) -> tuple[TruthLabel, str]:
        v = scratch.get("verdict")
        # The verdict is code's own output, so it outranks whatever the draft called itself.
        # A template that carries a CONFIRMED verdict must not be able to arrive labelled
        # REPORTED just because the supervisor forgot to set insight_type.
        if v and v.get("kind") in (VerdictKind.CONFLICT.value, VerdictKind.CONFIRMED.value,
                                   VerdictKind.CONFIRMED_LAG.value):
            if v["kind"] == VerdictKind.CONFLICT.value:
                return TruthLabel.CONFLICT, "conflict_template"
            return TruthLabel.VERIFIED, "verified_template"
        if draft.insight_type == InsightType.factual_claim and v:
            return TruthLabel.UNVERIFIABLE, "unverifiable_template"
        if draft.kind == "settlement":
            status = (scratch.get("settlement") or {}).get("status", "")
            return {"hit": TruthLabel.SETTLED_HIT, "miss": TruthLabel.SETTLED_MISS,
                    "void": TruthLabel.SETTLED_VOID}.get(status, TruthLabel.REPORTED), \
                "settlement_template"
        if draft.kind == "prediction":
            return TruthLabel.PREDICTION, "prediction_template"
        if draft.kind == "query_answer":
            # "Answer from creator memory" is only true when there was a memory to answer from.
            # The abstain variant says the creator never covered it, so it gets its own label
            # instead of borrowing one that claims an answer exists.
            if "abstain" in (draft.tags or []):
                return TruthLabel.NO_EVIDENCE, "query_abstain_template"
            return TruthLabel.FAN_QUERY, "query_template"
        if draft.insight_type in (InsightType.opinion, InsightType.tactic,
                                  InsightType.rule_explanation):
            return TruthLabel.OPINION, "free_text"
        if draft.insight_type == InsightType.quote:
            return TruthLabel.REPORTED, "quote_template"
        return TruthLabel.REPORTED, "free_text"

    def _state_dict(self) -> dict:
        st = self.feed.state_at(self.t)
        if st is None:
            return {"available": False}
        return {"available": True, "over_label": st.over_label, "score": st.score,
                "striker": st.striker, "bowler": st.bowler, "rrr": st.rrr,
                "target": self.feed.meta.target}

    def _state_summary(self) -> str:
        st = self._state_dict()
        return (f"clock {self.t:.0f}s ({fmt(self.t)}) | {st.get('score', '-')}/-"
                f" at {st.get('over_label', '-')} | striker {st.get('striker', '-')}, "
                f"bowler {st.get('bowler', '-')}, RRR {st.get('rrr', '-')}, "
                f"target {self.feed.meta.target} | mode={self.mode} | "
                f"cards so far={len(self.cards)} | open predictions={len(self.ledger.open)} | "
                f"segments stored={len(self.history)}")

    def _domain_notes(self, scratch: dict) -> str:
        return ("Numbers on a card must already exist in the cited utterance, feed event, "
                "verdict or tally. The kernel replaces the wording of verified, conflict, "
                "prediction and settlement cards with templates, so prose for those is wasted. "
                f"Fan playback position: {self._state_dict().get('over_label', 'unknown')}.")

    def badges(self) -> dict[str, str]:
        return {"asr": getattr(self.asr, "source", "cached"),
                "llm": getattr(self.llm, "mode_used", "unknown"),
                "feed": "replay_fixture", "telemetry": "simulated"}

    def _utterance_by_id(self, uid: str) -> Optional[Utterance]:
        return next((x for x in self.history if x.id == uid), None)


def best_sentence(tokens: set[str], text: str) -> str:
    """The sentence inside an utterance that actually matches, chosen with the same expanded
    token set that found the utterance - otherwise "what did he say about the weather"
    highlights "Haan bhai" instead of the sentence about rain."""
    parts = [p.strip() for p in re.split(r"[.!?]|,\s+(?=[a-z])", text) if p.strip()]
    if not parts:
        return text
    best = max(parts, key=lambda p: len(tokens & content_tokens(p)))
    return best if len(content_tokens(best)) < len(content_tokens(text)) else text
