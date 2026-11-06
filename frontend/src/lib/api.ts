import type {
  Health,
  InterruptResponse,
  Manifest,
  Pack,
  PlayerMode,
  RunMode,
  SessionState,
  TraceResponse,
} from './types'

const JSON_HEADERS = { 'content-type': 'application/json' }

export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  /** The server's own `why` - shown verbatim instead of a generic client message. */
  get why(): string {
    if (this.payload && typeof this.payload === 'object') {
      const p = this.payload as Record<string, unknown>
      for (const key of ['why', 'error', 'message', 'detail']) {
        if (typeof p[key] === 'string') return p[key] as string
      }
    }
    return this.message
  }
}

/**
 * What the UI shows when a call fails. An ApiError carries the server's own reason, so a 400 for
 * an unknown scenario reads "400: unknown gap scenario S9" instead of a bare status line.
 */
export function errMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.why}`
  return err instanceof Error ? err.message : String(err)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {  const res = await fetch(path, init)
  const text = await res.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { raw: text.slice(0, 240) }
    }
  }
  if (!res.ok) {
    throw new ApiError(`${res.status} ${res.statusText} on ${path}`, res.status, payload)
  }
  return payload as T
}

export function getHealth(): Promise<Health> {
  return request<Health>('/api/health')
}

export function getManifest(): Promise<Manifest> {
  return request<Manifest>('/api/manifest')
}

export interface StartRequest {
  pack: Pack
  scenario: string
  mode: PlayerMode
  run_mode?: RunMode
  query?: string
  /** The server resolves `ask_user` from the fixture instead of waiting for a human. Used by
   *  the agent-room race, where three episodes run at once and nobody is answering. */
  auto_answer?: boolean
  /** `scenario: 'custom'` — a situation the fixtures do not contain. The ball the fan came back
   *  on, and what happened to them, are the whole request. */
  resume_event_id?: string
  interruption?: string
  /** `scenario: 'custom'` on the creator pack: words typed instead of transcribed. */
  text?: string
  /** Force a transport for this session: recordings only, the live pool, or nothing. */
  llm?: 'replay' | 'live' | 'off'
}

export interface StartResponse {
  session_id: string
  pack: Pack
  scenario: string
  mode: PlayerMode
  run_mode: RunMode
  events: string
}

export function startSession(req: StartRequest): Promise<StartResponse> {
  return request<StartResponse>('/api/session', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(req),
  })
}

export function getSession(sid: string): Promise<SessionState> {
  return request<SessionState>(`/api/session/${encodeURIComponent(sid)}`)
}

export function getTrace(sid: string): Promise<TraceResponse> {
  return request<TraceResponse>(`/api/session/${encodeURIComponent(sid)}/trace`)
}

export function answerWithText(sid: string, text: string): Promise<{ ok: boolean; answer: string }> {
  return request(`/api/session/${encodeURIComponent(sid)}/answer`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ text }),
  })
}

export function answerWithIndex(sid: string, optionIndex: number): Promise<{ ok: boolean; answer: string }> {
  return request(`/api/session/${encodeURIComponent(sid)}/answer`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ option_index: optionIndex }),
  })
}

export type InterruptKind = 'ad_start' | 'ad_end' | 'stall' | 'resume' | 'gap' | 'join' | 'play'

export function sendInterrupt(
  sid: string,
  kind: InterruptKind,
  opts: { eventId?: string; note?: string; t?: number } = {},
): Promise<InterruptResponse> {
  return request<InterruptResponse>(`/api/session/${encodeURIComponent(sid)}/interrupt`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      kind,
      event_id: opts.eventId || '',
      note: opts.note || 'jury pane heartbeat',
      t: opts.t,
    }),
  })
}

/**
 * Loads a clip through the server's byte counter and returns what the server said.
 *
 * The body is read and discarded - there is no encoded video in the fixture set - because the
 * only number the UI is allowed to show is the one the meter emits. The client-side byte count
 * is returned purely to prove the transfer happened.
 */
export async function loadClip(
  sid: string,
  eventId: string,
  onProgress?: (receivedBytes: number) => void,
): Promise<{ charged: boolean; receivedBytes: number; refusal?: string; headers: Record<string, string> }> {
  const url = `/api/media/clip/${encodeURIComponent(eventId)}?sid=${encodeURIComponent(sid)}`
  const res = await fetch(url)
  const headers: Record<string, string> = {}
  res.headers.forEach((value, key) => {
    headers[key] = value
  })
  if (!res.ok) {
    let why = `${res.status}`
    try {
      const body = (await res.json()) as Record<string, unknown>
      if (typeof body.why === 'string') why = body.why
    } catch {
      /* keep the status */
    }
    return { charged: false, receivedBytes: 0, refusal: why, headers }
  }
  let received = 0
  if (res.body) {
    const reader = res.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      received += value ? value.byteLength : 0
      onProgress?.(received)
    }
  } else {
    const buf = await res.arrayBuffer()
    received = buf.byteLength
    onProgress?.(received)
  }
  return { charged: true, receivedBytes: received, headers }
}
