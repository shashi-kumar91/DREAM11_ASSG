import type { Frame } from './types'

/**
 * One plain sentence per frame — the only thing the interface prints about a frame.
 *
 * The first build showed the wire format (`action=dispatch agent=investigator ok=true ms=0.1`)
 * because it was easy to generate and nobody had to decide what mattered. It reads like a log
 * file and means nothing to a fan, so every kind now has a sentence a person would say out loud.
 * The technical detail is still one click away in the drawer, and the raw frame is still on the
 * server: nothing is hidden, it is just not shouted.
 */

export interface HumanLine {
  /** Short verb phrase, e.g. "asked you". */
  what: string
  /** The actual content: the question, the reason, the tool's finding. */
  detail: string
  tone: '' | 'good' | 'warn' | 'bad'
}

const s = (v: unknown, limit = 220): string => {
  if (v === null || v === undefined) return ''
  const text = typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : ''
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' ? (v as Record<string, unknown>) : {}

/** Trim a model's JSON-ish output to a sentence-ish fragment without pretending it is prose. */
function snippet(v: unknown, limit = 160): string {
  if (typeof v === 'string') return s(v, limit)
  const o = obj(v)
  for (const k of ['summary', 'detail', 'thought', 'note', 'why', 'reason', 'text']) {
    const val = o[k]
    if (typeof val === 'string' && val.trim()) return s(val, limit)
  }
  return ''
}

export function humanize(f: Frame): HumanLine {
  const d = obj(f.data)
  const agent = s(d.agent) || 'a specialist'
  switch (f.type) {
    case 'episode_context':
      return {
        what: 'read the situation',
        detail: `${s(d.interruption_type)} · you came back at ${s(d.resume_over)} · possible gaps ${(d.boundary_candidates as string[] || []).join(', ')}`,
        tone: '',
      }
    case 'episode_open':
    case 'episode_start':
      return { what: 'opened an episode', detail: s(d.objective) || s(d.trigger), tone: '' }
    case 'supervisor_decision': {
      const action = s(d.action)
      const why = s(d.reason) || s(d.thought)
      if (action === 'dispatch') return { what: `handed the task to the ${agent}`, detail: why, tone: '' }
      if (action === 'tool') return { what: `ran ${s(d.tool) || 'a tool'} itself`, detail: why, tone: '' }
      if (action === 'commit') return { what: 'decided to publish a card', detail: why, tone: 'good' }
      if (action === 'skip') return { what: 'decided this was not worth your attention', detail: why, tone: 'warn' }
      if (action === 'stop') return { what: 'stopped: nothing true to add', detail: why, tone: 'warn' }
      if (action === 'ask_user') return { what: 'decided to ask you', detail: why, tone: '' }
      return { what: `chose ${action || 'a step'}`, detail: why, tone: '' }
    }
    case 'tool_call': {
      const tool = s(d.tool)
      const ok = d.ok !== false
      const preview = snippet(d.preview)
      return {
        what: ok ? `${tool} returned` : `${tool} refused`,
        detail: preview || (ok ? 'ok' : s(d.error) || 'no detail'),
        tone: ok ? '' : 'warn',
      }
    }
    case 'agent_result':
      return { what: `${agent} reported back`, detail: snippet(d.result, 200), tone: '' }
    case 'agent_error':
      return { what: `${agent} failed`, detail: s(d.error), tone: 'bad' }
    case 'agent_no_final':
      return { what: `${agent} gave no answer`, detail: 'no structured result; the kernel moved on', tone: 'warn' }
    case 'agent_schema_retry':
    case 'agent_unparseable_retry':
    case 'supervisor_unparseable_retry':
      return { what: 'asked again', detail: `${agent} did not match the contract, so it was retried once`, tone: 'warn' }
    case 'commit':
      return d.ok === false
        ? { what: 'a card was refused at the gate', detail: 'it did not survive verification', tone: 'bad' }
        : { what: 'published a card', detail: 'every number in it was found in the cited evidence', tone: 'good' }
    case 'commit_rejected':
      return { what: 'the gate rejected a card', detail: s(d.why) || 'a rule refused it', tone: 'bad' }
    case 'guardrail':
      return { what: 'a guardrail fired', detail: `${s(d.rule)} — ${s(d.why)}`, tone: 'warn' }
    case 'ask_user': {
      const q = obj(d.question)
      return { what: 'asked you one question', detail: s(q.text), tone: '' }
    }
    case 'answer':
    case 'answer_accepted':
      return { what: 'heard your answer', detail: s(d.answer), tone: 'good' }
    case 'auto_answer':
      return { what: 'used the recorded fan answer', detail: s(d.answer), tone: '' }
    case 'boundary':
      return {
        what: 'located where your stream broke',
        detail: `last balls you may have seen: ${(d.candidate_overs as string[] || []).join(', ') || 'unknown'}`
          + (d.confidence ? ` · confidence ${Math.round(Number(d.confidence) * 100)}%` : ''),
        tone: '',
      }
    case 'boundary_from_fan':
      return { what: 'took your word over the log', detail: s(d.answer), tone: 'good' }
    case 'no_question':
      return { what: 'did not ask you anything', detail: s(d.why) || 'the answers would not have changed the card', tone: '' }
    case 'ask_refused':
    case 'ask_fan_refused':
      return { what: 'did not ask again', detail: s(d.note) || s(d.why), tone: '' }
    case 'asr_abstain':
    case 'abstain':
      return {
        what: 'abstained on a segment',
        detail: s(d.reason) === 'asr_empty_transcript'
          ? 'the transcription came back empty, so nothing was invented'
          : `too unsure of the words (${Math.round(Number(d.confidence || 0) * 100)}% confident)`,
        tone: 'warn',
      }
    case 'triage':
      return d.skip
        ? { what: 'dropped a segment for free', detail: s(d.reason), tone: 'warn' }
        : { what: 'kept a segment', detail: 'worth a model call', tone: '' }
    case 'episode_skipped_tier0':
    case 'tier0_skip':
      return { what: 'skipped a segment', detail: s(d.reason), tone: 'warn' }
    case 'kernel_verifier': {
      const v = obj(d.verdict)
      return { what: 'checked the claim against the feed', detail: s(v.kind) || s(d.note), tone: '' }
    }
    case 'prediction':
      return { what: 'logged a prediction', detail: s(obj(d.prediction).text), tone: '' }
    case 'prediction_completed':
      return {
        what: 'filled in what the sentence already said',
        detail: (d.filled as string[] || []).join(', '),
        tone: '',
      }
    case 'prediction_settled': {
      const st = s(d.status)
      return {
        what: st === 'hit' ? 'a prediction landed' : st === 'miss' ? 'a prediction missed' : 'a prediction voided',
        detail: s(d.tally) || s(obj(d.prediction).tally_text),
        tone: st === 'hit' ? 'good' : 'warn',
      }
    }
    case 'surface':
      return { what: `showed the card as ${s(d.mode) || 'a card'}`, detail: s(d.reason), tone: '' }
    case 'stored_not_surfaced':
      return { what: 'stored a card without showing it', detail: s(d.reason), tone: 'warn' }
    case 'query_no_evidence':
      return { what: 'found no evidence for your question', detail: 'nothing was invented to fill the gap', tone: 'warn' }
    case 'bytes':
      return { what: 'counted data', detail: `${Number(d.kb || 0).toFixed(1)} KB — ${s(d.reason)}`, tone: '' }
    case 'supervisor_error':
      return {
        what: 'could not reach a model for this step',
        detail: 'the kernel carried on without one; nothing was invented to fill the gap',
        tone: 'warn',
      }
    case 'llm_absent':
      return {
        what: 'built this without a model turn',
        detail: s(d.note),
        tone: '',
      }
    case 'budget_block':
      return {
        what: 'hit a budget wall',
        detail: `${s(d.where)} — the kernel stops here rather than spending more`,
        tone: 'warn',
      }
    case 'modality_overridden':
      return { what: 'changed the card type', detail: `${s(d.from_)} → ${s(d.to)}: ${s(d.note)}`, tone: 'warn' }
    case 'terminal_vetoed':
      return { what: 'refused to stay silent', detail: s(d.reason), tone: 'warn' }
    case 'clip_rejected':
      return { what: 'declined to load a clip', detail: s(d.why), tone: 'warn' }
    case 'uncertainty':
      return { what: 'flagged something it was unsure of', detail: s(d.detail) || s(d.flag), tone: 'warn' }
    case 'episode_end': {
      const term = s(d.terminal)
      if (term === 'committed') return { what: 'finished with a card', detail: 'what you see is what the feed supports', tone: 'good' }
      if (term === 'stopped_noop') return { what: 'finished with nothing to report', detail: 'a deliberate no-op, not a failure', tone: '' }
      if (term === 'supervisor_unavailable' || term === 'failed') {
        return {
          what: 'finished without the model',
          detail: 'the kernel completed the work in code, so this is not a broken run',
          tone: '',
        }
      }
      return { what: 'closed the episode', detail: `${term}${d.reason ? ` — ${s(d.reason)}` : ''}`, tone: '' }
    }
    case 'interrupt':
      return { what: `player sent “${s(d.heartbeat)}”`, detail: s(d.event_id), tone: '' }
    case 'card': {
      const c = obj(d.card)
      return { what: 'card added to your page', detail: s(c.headline), tone: 'good' }
    }
    case 'done':
      return { what: 'finished', detail: `${d.cards ?? 0} card(s), ${d.llm_calls ?? 0} model turns`, tone: 'good' }
    case 'status':
      return { what: 'note', detail: s(d.note), tone: '' }
    case 'error':
      return { what: 'error', detail: s(d.message), tone: 'bad' }
    case 'utterance': {
      const u = obj(d.utterance)
      return {
        what: `heard the creator (${Math.round(Number(u.asr_confidence || 0) * 100)}% confident)`,
        detail: s(u.text, 240),
        tone: '',
      }
    }
    case 'session':
      return { what: 'session open', detail: `${s(d.pack)} · ${s(d.mode)} · ${s(d.llm_layer) || s(d.run_mode)}`, tone: '' }
    default: {
      // Unknown kinds still get a sentence: the type name, and the most sentence-like field.
      const guess = snippet(d, 140)
      return { what: s(f.type).replace(/_/g, ' '), detail: guess, tone: '' }
    }
  }
}
