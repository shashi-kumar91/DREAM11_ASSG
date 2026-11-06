// The one place the UI learns facts. Every number on screen traces back to `GET /api/facts`,
// which reads the same fixture files the agent runs on (see backend/app/facts.py).
//
// Rule this file exists to enforce: nothing in the components may type a score, a rate, a card
// metric or a "what the agent does" claim. If it is not in this payload it is not on the page.

export interface MatchMeta {
  match_id: string
  title: string
  team_a: string
  team_b: string
  format: string
  squads: string[]
  target: number
  total_overs: number
  creator?: string | null
  fictional?: boolean
  note?: string
}

export interface MatchState {
  last_over: string
  score: string
  rrr: number | null
  balls_in_fixture: number
  wickets: number
  boundaries: number
  window: string
}

export interface FeedEvent {
  id: string
  over: string
  t: number
  kind: string
  runs: number
  wicket: boolean
  score: string
  rrr: number | null
  crr: number | null
  text: string
  bowler: string
  striker: string
  out: string | null
}

export interface OcrRead {
  score: string | null
  over: string | null
  rrr: string | null
  readable: boolean
  error?: string
}

export interface FrameFact {
  frame_id: string
  over: string
  /** What the picture was drawn with (differs from the feed on the injected-fault frame). */
  drawn_score: string
  /** What the official feed says at that ball - the only thing that can certify a number. */
  feed_score: string | null
  feed_rrr: number | null
  true_score: string
  true_rrr: number | null
  injected_fault: boolean
  url: string
  ocr: OcrRead
}

export interface Heartbeat {
  t: number
  event_id: string
  kind: string
  note: string
}

export interface GapScenarioFact {
  id: string
  name: string
  blurb: string
  interruption_type: string
  resume_over: string
  resume_score: string
  asks: boolean
  expected: Record<string, unknown>
  heartbeats: Heartbeat[]
}

export interface SegmentFact {
  id: string
  t_start: number
  t_end: number
  text: string
  speaker: string
  noisy: boolean
  asr_confidence_fixture: number | null
  audio: string
}

export interface ProvenanceRow {
  surface: string
  state: string
  detail: string
}

export interface Credit {
  file: string
  title: string
  artist: string
  license: string
  source: string
}

export interface Facts {
  match: MatchMeta
  state: MatchState
  feed: FeedEvent[]
  frames: FrameFact[]
  gap_scenarios: GapScenarioFact[]
  creator: { segments: SegmentFact[] }
  provenance: ProvenanceRow[]
  run: { run_mode: string; llm_backend: string; cassettes: number }
  photo_credits: Credit[]
}

export async function getFacts(): Promise<Facts> {
  const res = await fetch('/api/facts')
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on /api/facts`)
  return (await res.json()) as Facts
}

/** Plain-language name for an interruption, kept next to the server's own scenario fields. */
export const INTERRUPTION_WORD: Record<string, string> = {
  ad: 'An ad covered the screen',
  buffer: 'The stream froze',
  app_switch: 'You switched apps',
  late_join: 'You joined late',
  unknown: 'You were away',
}
