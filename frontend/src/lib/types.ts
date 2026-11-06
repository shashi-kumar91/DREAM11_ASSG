// Types for the HTTP contract in docs/API_CONTRACT.md, plus the extra frames the
// implemented server (backend/app/server.py) actually puts on the wire. Every field here
// is read from a server payload; nothing is invented client-side.

export type Pack = 'gap' | 'creator'
export type PlayerMode = 'watch' | 'quiet'
export type RunMode = 'offline-ci' | 'cached-demo' | 'live-demo' | 'safe-demo'

export interface Scenario {
  id: string
  name: string
  blurb: string
  expected: Record<string, unknown>
  interruption_type?: string
  resume_over?: string
  asks?: boolean
}

export interface PackManifest {
  id: string
  route: string
  title: string
  subtitle: string
  scenarios: Scenario[]
}

export interface MatchMeta {
  match_id: string
  title: string
  team_a: string
  team_b: string
  format: string
  squads: string[]
  target?: number | null
  total_overs: number
  keyterms: string[]
  creator?: string | null
}

export interface Manifest {
  run_mode: RunMode
  backend: string
  cassettes: number
  match: MatchMeta
  packs: PackManifest[]
}

export interface Health {
  ok: boolean
  run_mode: RunMode
  llm_backend: string
  cassettes: number
  sessions: number
  frontend_built: boolean
  asr: string
  ask_timeout_s: number
}

export interface Card {
  id: string
  kind: string
  insight_type?: string | null
  headline: string
  body: string
  salience: number
  truth_label: string
  attribution: string
  timestamp_label: string
  template_used?: string | null
  numbers: string[]
  cited_event_ids: string[]
  cited_utterance_ids: string[]
  cited_verdict_ids: string[]
  cited_tally_ids: string[]
  tags: string[]
  episode_id: string
  source_badges: Record<string, string>
  created_at: number
}

export interface QuestionOption {
  text: string
}

export interface PendingQuestion {
  id?: string
  text: string
  reason: string
  options: string[]
  expected_info_gain?: number
  blocking?: boolean
}

export interface BoundaryCandidate {
  overs?: string[]
  event_ids?: string[]
  confidence?: number
  notes?: string[]
}

export interface BoundaryInfo {
  candidates: string[]
  candidate_overs: string[]
  confidence: number
  notes: string[]
  needs_fan_input?: boolean
  clean_last_position?: boolean
  resume_over?: string
  source?: string
  kind?: string
}

export interface BytesRecord {
  t: number
  endpoint: string
  bytes_sent: number
  kb: number
  reason: string
  total_kb: number
  would_have_kb: number
  offered_kb?: number
  refused_kb?: number
  clip_loads?: number
  event_id?: string
  over_label?: string
}

export interface BytesSnapshot {
  total_bytes: number
  kb: number
  clip_loads: number
  refusals: number
  refused_kb: number
  would_have_kb: number
  saved_kb: number
  records: BytesRecord[]
}

export interface BudgetUsed {
  llm_calls: number
  tool_calls: number
  trace_events: number
  cards: number
  bytes_kb: number
  would_have_kb: number
  clip_loads: number
  refusals: number
  ask_timeouts: number
  budget?: {
    max_llm_calls: number
    max_tool_calls: number
    max_ms: number
    max_specialist_rounds: number
  }
}

export interface DoneFrame {
  cards: number
  llm_calls: number
  llm_mode?: string
  trace_events: number
  badges: Record<string, string>
  bytes: BytesSnapshot
  budget: BudgetUsed
  errors: { where: string; message: string }[]
  /** Which transport answered, and why. Added after a live run could not be explained from the
   *  snapshot alone: the SSE frame had it, the polled state did not. */
  llm_layer?: string
  llm_note?: string
  llm_models?: string[]
  served_models?: string[]
}

export interface SessionState {
  session_id: string
  pack: Pack
  scenario: string
  mode: PlayerMode
  run_mode: RunMode
  frames: number
  done: boolean
  badges: Record<string, string>
  cards: Card[]
  pending_question: PendingQuestion | null
  bytes: BytesSnapshot
  budget: BudgetUsed
  errors: { where: string; message: string }[]
  /** Which transport answered, and why. Added after a live run could not be explained from the
   *  snapshot alone: the SSE frame had it, the polled state did not. */
  llm_layer?: string
  llm_note?: string
  llm_models?: string[]
  served_models?: string[]
}

export interface InterruptResponse {
  ok: boolean
  seq: number
  kind: string
  t_player: number
  event_id: string
  player_mode: PlayerMode
  candidates?: string[]
  candidate_overs?: string[]
  confidence?: number
  notes?: string[]
  applied_to_session?: boolean
}

export interface TraceRow {
  seq: number
  t: number
  kind: string
  [key: string]: unknown
}

export interface TraceResponse {
  session_id: string
  pack: Pack
  scenario: string
  mode: PlayerMode
  run_mode: RunMode
  events: TraceRow[]
  counts: Record<string, number>
  journal: { frames: number; closed: boolean }
  badges: Record<string, string>
  cards: Card[]
  budget_used: BudgetUsed
  bytes: BytesSnapshot
  boundary: (BoundaryInfo & { chosen?: string; source?: string }) | null
  released_events: string[]
  answers: { text: string; t: number }[]
  errors: { where: string; message: string }[]
}

// One SSE frame as the journal replays it. `type` is the event name; the data object also
// carries its own `seq`, which is the trace sequence for kernel frames, so React keys use
// the journal cursor (event.lastEventId) instead.
export interface Frame {
  id: string
  type: string
  data: Record<string, unknown>
}
