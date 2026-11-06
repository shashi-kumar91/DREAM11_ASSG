import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  answerWithIndex,
  answerWithText,
  errMessage,
  getSession,
  getTrace,
  loadClip as fetchClip,
  sendInterrupt,
  startSession,
  type InterruptKind,
  type StartRequest,
} from './api'
import type {
  BoundaryInfo,
  BudgetUsed,
  BytesRecord,
  BytesSnapshot,
  Card,
  DoneFrame,
  Frame,
  InterruptResponse,
  Pack,
  PendingQuestion,
  PlayerMode,
  TraceRow,
} from './types'

/**
 * Every event name the implemented server can put on the wire: the contract's list plus the
 * kernel kinds that backend/app/server.py mirrors from its TraceRecorder. A frame whose name is
 * missing here would never reach the jury, so the list is derived from the server, not guessed.
 */
export const EVENT_TYPES: string[] = [
  // contract
  'episode_start', 'episode_context', 'boundary', 'ask_user', 'answer', 'agent_result',
  'agent_no_final', 'supervisor_decision', 'tool_call', 'guardrail', 'modality_overridden',
  'terminal_vetoed', 'clip_rejected', 'no_question', 'uncertainty', 'card', 'commit', 'bytes',
  'status', 'done', 'error',
  // server.py journal / trace relabelling
  'session', 'episode_open', 'episode_end', 'answer_accepted', 'auto_answer',
  'supervisor_error', 'supervisor_unparseable_retry', 'interrupt', 'commit_rejected',
  'question_wording_from_kernel', 'ask_fan_refused', 'boundary_from_fan', 'budget_block',
  'agent_error', 'agent_schema_retry', 'agent_unparseable_retry', 'asr_abstain',
  'prediction_settled', 'query_no_evidence', 'stored_not_surfaced', 'triage', 'kernel_verifier',
  // emitted by the packs' own on_event channel (backend/packs/creator/session.py:162,197
  // and backend/packs/gap/session.py:430) and by TraceRecorder.record
  'abstain', 'tier0_skip', 'episode_skipped_tier0', 'ask_refused',
  // session channels
  'utterance', 'prediction', 'surface',
]

export type StreamState = 'idle' | 'connecting' | 'open' | 'retrying' | 'dropped' | 'closed'

export interface ClipQuote {
  eventId: string
  overLabel: string
  durationS?: number
  bytesEstimateKb?: number
  loadPolicy?: string
  frameCheck?: string
  offerable: boolean
  why?: string
  source: string
}

export interface ClipRead {
  eventId: string
  bytes: number
  kb: number
  refusal?: string
  charged: boolean
  headerKb?: string
}

export interface UtteranceRow {
  id: string
  text: string
  speaker: string
  t_start?: number
  t_end?: number
  asr_confidence?: number
  asr_source?: string
}

export interface PredictionRow {
  id: string
  utterance_id: string
  text: string
  bowler?: string | null
  batter?: string | null
  over?: number | null
  metric?: string | null
  threshold?: number | null
  status: string
  tally_text?: string | null
  settle_event_ids?: string[]
}

export interface SessionView {
  sid: string | null
  request: StartRequest | null
  frames: Frame[]
  cards: Card[]
  utterances: UtteranceRow[]
  predictions: PredictionRow[]
  question: PendingQuestion | null
  answeredText: string | null
  boundary: BoundaryInfo | null
  episodeStart: Record<string, unknown> | null
  clipQuote: ClipQuote | null
  clipReads: ClipRead[]
  bytesRecords: BytesRecord[]
  bytesSnapshot: BytesSnapshot | null
  budget: BudgetUsed | null
  badges: Record<string, string>
  /** Which transport answered this run, and why — the server's words, not the UI's guess. */
  llmLayer: string
  llmNote: string
  done: DoneFrame | null
  streamErrors: string[]
  traceEvents: TraceRow[]
  releasedEvents: string[]
  interruptRows: InterruptResponse[]
  actionError: string | null
  starting: boolean
}

const EMPTY: SessionView = {
  sid: null,
  request: null,
  frames: [],
  cards: [],
  utterances: [],
  predictions: [],
  question: null,
  answeredText: null,
  boundary: null,
  episodeStart: null,
  clipQuote: null,
  clipReads: [],
  bytesRecords: [],
  bytesSnapshot: null,
  budget: null,
  badges: {},
  llmLayer: '',
  llmNote: '',
  done: null,
  streamErrors: [],
  traceEvents: [],
  releasedEvents: [],
  interruptRows: [],
  actionError: null,
  starting: false,
}

const MAX_FRAMES = 600

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}
}

function asArray<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : []
}

/** A field a sparse frame did not carry must not erase what an earlier frame already said. */
function nz<T>(v: T, old: T): T {
  const blank = v === null || v === undefined || (v as unknown) === ''
  return blank ? old : v
}

/** The `offer_clip` preview is truncated JSON, so pull the quote out by field, not by parse. */
export function parseClipQuote(preview: unknown, caller: unknown): ClipQuote | null {
  const text = typeof preview === 'string' ? preview : JSON.stringify(preview ?? '')
  const grab = (key: string): string | null => {
    const m = new RegExp(`"${key}"\\s*:\\s*"([^"]*)"`).exec(text)
    return m ? m[1] : null
  }
  const num = (key: string): number | undefined => {
    const m = new RegExp(`"${key}"\\s*:\\s*([0-9.]+)`).exec(text)
    return m ? Number(m[1]) : undefined
  }
  const eventId = grab('event_id')
  if (!eventId) return null
  const offerable = /"offerable"\s*:\s*true/.test(text) || (!/"offerable"\s*:\s*false/.test(text) && /"quote"/.test(text))
  return {
    eventId,
    overLabel: grab('over_label') || '',
    durationS: num('duration_s'),
    bytesEstimateKb: num('bytes_estimate_kb'),
    loadPolicy: grab('load_policy') || undefined,
    frameCheck: grab('frame_check') || undefined,
    offerable,
    why: grab('why') || undefined,
    source: `tool_call offer_clip (${String(caller || 'agent')})`,
  }
}

function frameToClipRead(f: BytesRecord, charged: boolean): ClipRead {
  return {
    eventId: f.event_id || f.endpoint.replace('/api/media/clip/', ''),
    bytes: f.bytes_sent,
    kb: f.kb,
    charged,
  }
}

export function useSession() {
  const [view, setView] = useState<SessionView>(EMPTY)
  const [stream, setStream] = useState<StreamState>('idle')
  const sourceRef = useRef<EventSource | null>(null)
  const sidRef = useRef<string | null>(null)

  const applyFrame = useCallback((frame: Frame) => {
    setView((prev) => {
      const d = frame.data
      const next: SessionView = { ...prev, frames: [...prev.frames, frame].slice(-MAX_FRAMES) }
      const type = frame.type

      if (type === 'episode_start' || type === 'session') {
        next.episodeStart = { ...(prev.episodeStart || {}), ...d }
      }
      if (type === 'session' || type === 'llm_absent') {
        if (typeof d.llm_layer === 'string' && d.llm_layer) next.llmLayer = d.llm_layer
        if (typeof d.llm_note === 'string' && d.llm_note) next.llmNote = d.llm_note
        if (typeof d.note === 'string' && d.note && type === 'llm_absent') next.llmNote = d.note
      }
      if (type === 'boundary') {
        next.boundary = {
          candidates: asArray<string>(d.candidates),
          candidate_overs: asArray<string>(d.candidate_overs),
          confidence: typeof d.confidence === 'number' ? d.confidence : 0,
          notes: asArray<string>(d.notes),
          needs_fan_input: d.needs_fan_input as boolean | undefined,
          clean_last_position: d.clean_last_position as boolean | undefined,
          resume_over: d.resume_over as string | undefined,
          source: d.source as string | undefined,
          kind: d.kind as string | undefined,
        }
      }
      if (type === 'ask_user') {
        const q = asRecord(d.question)
        next.question = {
          id: q.id as string | undefined,
          text: String(q.text || ''),
          reason: String(q.reason || ''),
          options: asArray<string>(q.options),
          expected_info_gain: q.expected_info_gain as number | undefined,
          blocking: d.blocking !== false,
        }
        next.answeredText = null
      }
      if (type === 'answer' || type === 'answer_accepted' || type === 'auto_answer') {
        next.answeredText = String(d.answer || '')
        next.question = null
      }
      if (type === 'card') {
        const card = asRecord(d.card) as unknown as Card
        if (card && card.id) {
          next.cards = prev.cards.some((c) => c.id === card.id) ? prev.cards : [...prev.cards, card]
        }
        const badges = asRecord(d.badges)
        if (Object.keys(badges).length) next.badges = { ...prev.badges, ...badges } as Record<string, string>
      }
      if (type === 'bytes') {
        const rec: BytesRecord = {
          t: typeof d.t === 'number' ? d.t : Date.now() / 1000,
          endpoint: String(d.endpoint || ''),
          bytes_sent: Number(d.bytes_sent || 0),
          kb: Number(d.kb || 0),
          reason: String(d.reason || ''),
          total_kb: Number(d.total_kb || 0),
          would_have_kb: Number(d.would_have_kb || 0),
          offered_kb: d.offered_kb as number | undefined,
          refused_kb: d.refused_kb as number | undefined,
          clip_loads: d.clip_loads as number | undefined,
          event_id: d.event_id as string | undefined,
          over_label: d.over_label as string | undefined,
        }
        const dupe = prev.bytesRecords.some(
          (r) => r.t === rec.t && r.endpoint === rec.endpoint && r.bytes_sent === rec.bytes_sent,
        )
        if (!dupe) next.bytesRecords = [...prev.bytesRecords, rec]
      }
      if (type === 'tool_call' && d.tool === 'offer_clip') {
        const q = parseClipQuote(d.preview, d.caller)
        if (q) next.clipQuote = q
      }
      if (type === 'clip_rejected') {
        next.clipQuote = {
          eventId: String(d.event_id || ''),
          overLabel: '',
          offerable: false,
          why: String(d.why || ''),
          source: 'clip_rejected',
        }
      }
      if (type === 'utterance') {
        const u = asRecord(d.utterance)
        if (u && u.id) {
          const row: UtteranceRow = {
            id: String(u.id),
            text: String(u.text || ''),
            speaker: String(u.speaker || ''),
            t_start: u.t_start as number | undefined,
            t_end: u.t_end as number | undefined,
            asr_confidence: u.asr_confidence as number | undefined,
            asr_source: u.asr_source as string | undefined,
          }
          next.utterances = prev.utterances.some((x) => x.id === row.id)
            ? prev.utterances
            : [...prev.utterances, row]
        }
      }
      if (type === 'prediction' || type === 'prediction_settled') {
        const p = asRecord(type === 'prediction' ? d.prediction : d)
        const id = String(p.id || p.prediction_id || '')
        if (id) {
          const row: PredictionRow = {
            id,
            utterance_id: String(p.utterance_id || ''),
            text: String(p.text || ''),
            bowler: (p.bowler as string | undefined) ?? null,
            batter: (p.batter as string | undefined) ?? null,
            over: (p.over as number | undefined) ?? null,
            metric: (p.metric as string | undefined) ?? null,
            threshold: (p.threshold as number | undefined) ?? null,
            status: String(p.status || 'open'),
            tally_text: (p.tally_text as string | undefined) ?? (p.tally as string | undefined) ?? null,
            settle_event_ids: asArray<string>(p.settle_event_ids || p.event_ids),
          }
          const existing = prev.predictions.findIndex((x) => x.id === id)
          const list = [...prev.predictions]
          if (existing >= 0) {
            // `prediction_settled` carries only the settlement fields; a flat spread would blank
            // the predicted text and its utterance id, which is what the ledger shows.
            const old = list[existing]
            list[existing] = {
              id,
              utterance_id: nz(row.utterance_id, old.utterance_id),
              text: nz(row.text, old.text),
              bowler: nz(row.bowler, old.bowler),
              batter: nz(row.batter, old.batter),
              over: nz(row.over, old.over),
              metric: nz(row.metric, old.metric),
              threshold: nz(row.threshold, old.threshold),
              status: row.status === 'open' && old.status !== 'open' ? old.status : row.status,
              tally_text: nz(row.tally_text, old.tally_text),
              settle_event_ids: row.settle_event_ids?.length ? row.settle_event_ids : old.settle_event_ids,
            }
          } else list.push(row)
          next.predictions = list
        }
      }
      if (type === 'done') {
        const done = d as unknown as DoneFrame
        next.done = done
        next.budget = done.budget || prev.budget
        next.bytesSnapshot = done.bytes || prev.bytesSnapshot
        next.badges = { ...prev.badges, ...(done.badges || {}) }
      }
      if (type === 'error') {
        next.streamErrors = [...prev.streamErrors, `${String(d.where || 'error')}: ${String(d.message || '')}`].slice(-8)
      }
      return next
    })
  }, [])

  const openStream = useCallback(
    (sid: string) => {
      sourceRef.current?.close()
      const es = new EventSource(`/api/session/${encodeURIComponent(sid)}/events`)
      sourceRef.current = es
      setStream('connecting')
      es.onopen = () => setStream('open')
      for (const type of EVENT_TYPES) {
        es.addEventListener(type, (ev: MessageEvent) => {
          let data: Record<string, unknown> = {}
          try {
            data = JSON.parse(ev.data) as Record<string, unknown>
          } catch {
            data = { raw: String(ev.data).slice(0, 400) }
          }
          const realType = typeof data.type === 'string' ? data.type : type
          applyFrame({ id: ev.lastEventId || `${Date.now()}`, type: realType, data })
        })
      }
      es.onerror = () => {
        // Tell the jury the stream dropped instead of spinning quietly: EventSource retries by
        // itself with Last-Event-ID, so a blip becomes 'retrying' and then 'open' again.
        if (es.readyState === EventSource.CLOSED) {
          setStream('dropped')
          setView((prev) => ({
            ...prev,
            streamErrors: [...prev.streamErrors, 'SSE stream closed by the browser - the journal is still on the server; start a new session or reopen this one to replay it.'].slice(-8),
          }))
        } else {
          setStream('retrying')
        }
      }
    },
    [applyFrame],
  )

  const start = useCallback(
    async (req: StartRequest) => {
      setView({ ...EMPTY, starting: true, request: req })
      setStream('connecting')
      try {
        const res = await startSession(req)
        sidRef.current = res.session_id
        saveLast({ sid: res.session_id, pack: req.pack, scenario: req.scenario, mode: req.mode })
        setView((prev) => ({ ...prev, sid: res.session_id, starting: false }))
        openStream(res.session_id)
      } catch (err) {
        setStream('dropped')
        setView((prev) => ({
          ...prev,
          starting: false,
          actionError: errMessage(err),
        }))
      }
    },
    [openStream],
  )

  /**
   * Re-attach to a session the server already knows about.
   *
   * The journal replays from frame 1 (`events` in backend/app/server.py starts at cursor 0), so
   * a page that was reloaded mid-run rebuilds the whole run - cards, ask, bytes frames - from the
   * stream alone. The snapshot and trace fill in the meters the journal does not carry.
   */
  const resume = useCallback(
    async (sid: string) => {
      const last = readLast()
      sidRef.current = sid
      setView({
        ...EMPTY,
        sid,
        starting: false,
        request: last ? { pack: last.pack, scenario: last.scenario, mode: last.mode } : null,
      })
      setStream('connecting')
      openStream(sid)
      try {
        const snap = await getSession(sid)
        setView((prev) => ({
          ...prev,
          request: { pack: snap.pack as Pack, scenario: snap.scenario, mode: snap.mode },
          cards: snap.cards.length ? snap.cards : prev.cards,
          budget: snap.budget,
          bytesSnapshot: snap.bytes,
          bytesRecords: snap.bytes.records,
          badges: snap.badges || prev.badges,
          llmLayer: snap.llm_layer || prev.llmLayer,
          llmNote: snap.llm_note || prev.llmNote,
          question: prev.question || snap.pending_question,
          done: snap.done ? (prev.done as DoneFrame) : prev.done,
          streamErrors: snap.errors.length ? snap.errors.map((e) => `${e.where}: ${e.message}`) : prev.streamErrors,
        }))
        if (snap.done) {
          const trace = await getTraceSafe(sid)
          setView((prev) => ({
            ...prev,
            traceEvents: trace.events,
            releasedEvents: trace.released_events,
            boundary: trace.boundary
              ? { ...prev.boundary, ...(trace.boundary as unknown as BoundaryInfo) }
              : prev.boundary,
          }))
        }
      } catch (err) {
        clearLast()
        setStream('dropped')
        setView((prev) => ({
          ...prev,
          actionError: `could not re-attach to ${sid}: ${errMessage(err)}`,
        }))
      }
    },
    [openStream],
  )

  const stop = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
    setStream('closed')
  }, [])

  const reset = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
    sidRef.current = null
    clearLast()
    setView(EMPTY)
    setStream('idle')
  }, [])

  const answer = useCallback(async (payload: { text?: string; optionIndex?: number }) => {
    const sid = sidRef.current
    if (!sid) return
    setView((prev) => ({ ...prev, actionError: null }))
    try {
      const res =
        payload.optionIndex !== undefined
          ? await answerWithIndex(sid, payload.optionIndex)
          : await answerWithText(sid, String(payload.text || ''))
      setView((prev) => ({ ...prev, answeredText: res.answer, question: null }))
    } catch (err) {
      setView((prev) => ({
        ...prev,
        actionError: errMessage(err),
      }))
    }
  }, [])

  const interrupt = useCallback(async (kind: InterruptKind, opts: { eventId?: string; t?: number } = {}) => {
    const sid = sidRef.current
    if (!sid) return
    setView((prev) => ({ ...prev, actionError: null }))
    try {
      const res = await sendInterrupt(sid, kind, opts)
      setView((prev) => ({ ...prev, interruptRows: [...prev.interruptRows, res].slice(-24) }))
    } catch (err) {
      setView((prev) => ({
        ...prev,
        actionError: errMessage(err),
      }))
    }
  }, [])

  const tapClip = useCallback(async (eventId: string) => {
    const sid = sidRef.current
    if (!sid) return
    setView((prev) => ({ ...prev, actionError: null }))
    try {
      const res = await fetchClip(sid, eventId)
      setView((prev) => {
        const read: ClipRead = {
          eventId,
          bytes: res.receivedBytes,
          kb: Math.round((res.receivedBytes / 1024) * 10) / 10,
          charged: res.charged,
          refusal: res.refusal,
          headerKb: res.headers['x-session-bytes-kb'],
        }
        // When the server refused, its refusal frame still arrives over SSE; when it served the
        // clip the meter frame is the number the graph draws. This row only proves the tap.
        return { ...prev, clipReads: [...prev.clipReads, read] }
      })
      if (res.charged) {
        const snap = await getSession(sid)
        setView((prev) => ({
          ...prev,
          bytesSnapshot: snap.bytes,
          budget: snap.budget,
          bytesRecords: snap.bytes.records.length >= prev.bytesRecords.length ? snap.bytes.records : prev.bytesRecords,
        }))
      }
    } catch (err) {
      setView((prev) => ({ ...prev, actionError: errMessage(err) }))
    }
  }, [])

  // Poll the session state so the meters stay true even if a frame is missed. It must keep
  // running until it has itself seen `done`, because the trace (released_events, the chosen
  // boundary) is only complete then - the SSE `done` frame arrives first and used to kill this
  // effect before the trace was ever fetched, which left the spoiler light green for the
  // wrong reason.
  useEffect(() => {
    const sid = view.sid
    if (!sid) return
    let cancelled = false
    const id = window.setInterval(async () => {
      try {
        const snap = await getSession(sid)
        if (cancelled) return
        setView((prev) => ({
          ...prev,
          cards: snap.cards.length >= prev.cards.length ? snap.cards : prev.cards,
          budget: snap.budget,
          bytesSnapshot: snap.bytes,
          bytesRecords: snap.bytes.records.length ? snap.bytes.records : prev.bytesRecords,
          badges: snap.badges || prev.badges,
          llmLayer: snap.llm_layer || prev.llmLayer,
          llmNote: snap.llm_note || prev.llmNote,
          question: prev.question || snap.pending_question,
          streamErrors: snap.errors.length ? snap.errors.map((e) => `${e.where}: ${e.message}`) : prev.streamErrors,
        }))
        if (snap.done) {
          const trace = await getTraceSafe(sid)
          if (cancelled) return
          setView((prev) => ({
            ...prev,
            done: snap.done ? (prev.done as DoneFrame) : prev.done,
            traceEvents: trace.events,
            releasedEvents: trace.released_events,
            boundary: trace.boundary
              ? { ...prev.boundary, ...(trace.boundary as unknown as BoundaryInfo) }
              : prev.boundary,
          }))
          window.clearInterval(id)
        }
      } catch {
        /* the SSE path already reports stream trouble */
      }
    }, 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [view.sid])

  const derived = useMemo(() => {
    const lastRecord = view.bytesRecords[view.bytesRecords.length - 1]
    const loadedKb = view.bytesSnapshot?.kb ?? lastRecord?.total_kb ?? 0
    const wouldKb = view.bytesSnapshot?.would_have_kb ?? lastRecord?.would_have_kb ?? 0
    return { loadedKb, wouldKb }
  }, [view.bytesSnapshot, view.bytesRecords])

  return { view, stream, start, stop, reset, resume, answer, interrupt, tapClip, derived }
}

// --- last session, so a reload can re-attach to the journal ----------------- //

const LAST_KEY = 'dream11.last'

export interface LastRun {
  sid: string
  pack: Pack
  scenario: string
  mode: PlayerMode
}

function saveLast(run: LastRun) {
  try {
    window.sessionStorage.setItem(LAST_KEY, JSON.stringify(run))
  } catch {
    /* private mode: the journal catch-up test simply cannot re-attach */
  }
}

export function readLast(): LastRun | null {
  try {
    const raw = window.sessionStorage.getItem(LAST_KEY)
    return raw ? (JSON.parse(raw) as LastRun) : null
  } catch {
    return null
  }
}

function clearLast() {
  try {
    window.sessionStorage.removeItem(LAST_KEY)
  } catch {
    /* nothing to clear */
  }
}

async function getTraceSafe(sid: string) {
  try {
    return await getTrace(sid)
  } catch {
    return { events: [], released_events: [], boundary: null } as never
  }
}

/** Spoiler state: only server-released events can turn this light red. */
export function spoilerState(
  episodeStart: Record<string, unknown> | null,
  cards: Card[],
  bytesRecords: BytesRecord[],
  releasedEvents: string[],
  guardrails: Frame[],
): { level: 'none' | 'green' | 'amber' | 'red'; detail: string } {
  const cursor = String(episodeStart?.resume_cursor || '')
  if (!cursor) {
    return { level: 'none', detail: 'no resume cursor in this pack - nothing to spoil' }
  }
  const over = String(episodeStart?.resume_over || '')
  const past = new Set<string>()
  const seen = (id: string) => {
    if (id && id > cursor) past.add(id)
  }
  cards.forEach((c) => c.cited_event_ids.forEach(seen))
  bytesRecords.forEach((r) => seen(String(r.event_id || '')))
  releasedEvents.forEach(seen)

  if (past.size) {
    return { level: 'red', detail: `${[...past].join(', ')} sit after ${over}` }
  }
  const blocked = guardrails.filter((f) => /spoiler|clamp|past resume|beyond_resume/i.test(JSON.stringify(f.data)))
  if (blocked.length) {
    return {
      level: 'amber',
      detail: `${blocked.length} release attempt${blocked.length > 1 ? 's' : ''} after ${over} stopped by the server`,
    }
  }
  // ${over} is the ball the fan came back on, so it is the one ball they are watching. Every
  // card in this product stops one ball short of it; the claim has to be worded that way.
  return {
    level: 'green',
    detail: `no ball after ${over} has ever been released to this fan - ${over} itself is the ball you came back on`,
  }
}

export function frameToClipRecord(f: BytesRecord): ClipRead {
  return frameToClipRead(f, f.bytes_sent > 0)
}
