import type { Card, Frame } from './types'

/** Read-only helpers over server frames. No number is produced here - only labelled. */

export function str(d: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = d[k]
    if (typeof v === 'string' && v) return v
    if (typeof v === 'number') return String(v)
    if (typeof v === 'boolean') return v ? 'yes' : 'no'
  }
  return ''
}

export function num(d: Record<string, unknown>, key: string): number | null {
  const v = d[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function framesByType(frames: Frame[], ...types: string[]): Frame[] {
  return frames.filter((f) => types.includes(f.type))
}

export function lastFrame(frames: Frame[], type: string): Frame | null {
  for (let i = frames.length - 1; i >= 0; i--) if (frames[i].type === type) return frames[i]
  return null
}

/** Some fields arrive as objects (the ask's question), so a plain String() would print
 *  "[object Object]" in the jury. Quote the text and the options the fan was offered. */
function fieldText(v: unknown): string {
  if (Array.isArray(v)) return v.join('|')
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    const text = typeof o.text === 'string' ? o.text : ''
    const opts = Array.isArray(o.options) && o.options.length ? ` options=${o.options.join('|')}` : ''
    return text ? `"${text}"${opts}`.slice(0, 160) : JSON.stringify(v).slice(0, 160)
  }
  return String(v)
}

/** One-line human summary of a trace frame, built from the server's own fields. */
const FIELDS: Record<string, string[]> = {
  supervisor_decision: ['action', 'agent', 'tool', 'thought'],
  tool_call: ['tool', 'caller', 'ok', 'ms'],
  agent_result: ['agent', 'found', 'detail'],
  guardrail: ['rule', 'why', 'offending'],
  commit: ['ok', 'card_id'],
  commit_rejected: ['ok', 'why'],
  bytes: ['endpoint', 'kb', 'total_kb', 'would_have_kb', 'reason'],
  card: ['kind', 'headline'],
  status: ['note'],
  done: ['cards', 'llm_calls', 'llm_mode', 'trace_events'],
  error: ['where', 'message'],
  boundary: ['candidate_overs', 'confidence', 'source', 'kind'],
  answer: ['answer'],
  answer_accepted: ['answer', 'source'],
  auto_answer: ['answer', 'note'],
  ask_user: ['question', 'note'],
  ask_refused: ['question', 'answer', 'note'],
  no_question: ['why'],
  interrupt: ['heartbeat', 't_player', 'event_id', 'applied_to_session'],
  triage: ['utterance_id', 'skip', 'reason'],
  episode_skipped_tier0: ['utterance_id', 'reason'],
  tier0_skip: ['utterance_id', 'reason', 'note'],
  abstain: ['utterance_id', 'reason', 'confidence'],
  asr_abstain: ['utterance_id', 'confidence', 'action'],
  surface: ['card_id', 'mode', 'reason'],
  prediction: ['text', 'status', 'over', 'bowler', 'metric', 'threshold'],
  prediction_settled: ['prediction_id', 'status', 'tally'],
  kernel_verifier: ['utterance_id', 'note'],
  utterance: ['asr_model'],
  budget_block: ['where', 'detail'],
  modality_overridden: ['from_', 'to', 'note'],
  terminal_vetoed: ['action', 'reason'],
  clip_rejected: ['event_id', 'why'],
  uncertainty: ['flag', 'detail'],
  episode_end: ['terminal', 'reason'],
  episode_open: ['pack', 'trigger'],
  session: ['pack', 'scenario', 'mode', 'run_mode', 'llm_backend'],
  episode_start: ['pack', 'scenario', 'resume_over', 'player_mode', 'trigger'],
  boundary_from_fan: ['event_id', 'answer'],
  query_no_evidence: ['query', 'segments_searched'],
  stored_not_surfaced: ['card_id', 'reason'],
  agent_error: ['agent', 'error'],
  agent_schema_retry: ['agent'],
  agent_unparseable_retry: ['agent'],
  supervisor_error: ['error'],
  supervisor_unparseable_retry: [],
  agent_no_final: ['agent'],
  question_wording_from_kernel: ['note'],
  ask_fan_refused: ['why', 'note'],
  episode_context: ['interruption_type', 'resume_over', 'boundary_candidates', 'boundary_confidence'],
  modality: ['modality'],
}

export function frameSummary(f: Frame): string {
  const keys = FIELDS[f.type]
  if (keys) {
    const parts: string[] = []
    for (const k of keys) {
      const v = f.data[k]
      if (v === undefined || v === null || v === '') continue
      parts.push(`${k}=${fieldText(v).slice(0, 120)}`)
    }
    if (f.type === 'card') {
      const c = (f.data.card || {}) as Partial<Card>
      return `headline=${c.headline || ''} | truth=${c.truth_label || ''} | kind=${c.kind || ''}`
    }
    if (f.type === 'utterance') {
      const u = (f.data.utterance || {}) as Record<string, unknown>
      return `${str(u, 'id')} conf=${str(u, 'asr_confidence')} ${str(u, 'asr_source')} "${str(u, 'text').slice(0, 110)}"`
    }
    if (f.type === 'prediction') {
      const p = (f.data.prediction || {}) as Record<string, unknown>
      return `${str(p, 'id')} ${str(p, 'status')} over=${str(p, 'over')} "${str(p, 'text').slice(0, 90)}"`
    }
    if (parts.length) return parts.join(' ')
  }
  const rest = Object.entries(f.data).filter(([k]) => k !== 'seq' && k !== 't' && k !== 'type')
  return rest.map(([k, v]) => `${k}=${fieldText(v).slice(0, 90)}`).join(' ')
}

export function kindTone(type: string, data: Record<string, unknown>): string {
  if (type === 'error' || type === 'guardrail' || type === 'commit_rejected') return 'bad'
  if (type === 'clip_rejected' || type === 'terminal_vetoed' || type === 'budget_block'
    || type === 'abstain' || type === 'asr_abstain' || type === 'ask_refused') return 'warn'
  if (type === 'uncertainty' || type === 'modality_overridden' || type === 'no_question') return 'warn'
  if (type === 'agent_error' || type === 'supervisor_error') return 'bad'
  if (type === 'card' || type === 'commit' || type === 'prediction_settled') {
    return data.ok === false ? 'bad' : 'good'
  }
  if (type === 'done') return 'good'
  return ''
}

/** Maps the server's TruthLabel text onto a colour class - presentation only. */
export function truthClass(label: string): string {
  const l = label.toLowerCase()
  if (l.includes('verified')) return 'verified'
  if (l.includes('conflict')) return 'conflict'
  if (l.includes('hit')) return 'hit'
  if (l.includes('miss')) return 'miss'
  if (l.includes('void')) return 'void'
  if (l.includes('opinion')) return 'opinion'
  if (l.includes('prediction')) return 'prediction'
  if (l.includes('creator said')) return 'reported'
  if (l.includes('not checkable')) return 'unverifiable'
  if (l.includes('no evidence')) return 'unverifiable'
  if (l.includes('creator memory')) return 'fan_query'
  if (l.includes('evidence')) return 'evidenced'
  return ''
}

export function fmtSecs(t: number | null): string {
  if (t === null) return '-'
  const m = Math.floor(t / 60)
  const s = Math.round(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
