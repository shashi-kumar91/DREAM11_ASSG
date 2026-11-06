import { useCallback, useEffect, useRef, useState } from 'react'
import { startSession, type StartRequest } from './api'
import type { Frame } from './types'
import { EVENT_TYPES } from './useSession'

/**
 * N real sessions, running at the same time, each on its own SSE stream.
 *
 * This is the only honest way to show parallelism in this product: the kernel dispatches one
 * specialist at a time inside an episode, but the server holds several episodes at once. So the
 * race view starts every scenario for real and lets each lane narrate itself - nothing here
 * fakes concurrent agents inside one episode.
 */
export type LaneStatus = 'idle' | 'starting' | 'live' | 'done' | 'failed'

export interface LaneSpec {
  key: string
  label: string
  request: StartRequest
}

export interface Lane {
  key: string
  label: string
  sid: string | null
  frames: Frame[]
  /** How many of `frames` the room is currently showing. A recorded episode finishes in
   *  milliseconds; revealing its frames at a human pace is a presentation choice, and the room
   *  says so, because the frames themselves are untouched. */
  shown: number
  status: LaneStatus
  error: string
  cards: number
}

/** Frames per second the room reveals a finished trace at, when pacing is on. */
export const PACE_MS = 380

const EMPTY_LANES: Lane[] = []

export function useLanes(paceMs: number = PACE_MS) {
  const [lanes, setLanes] = useState<Lane[]>(EMPTY_LANES)
  const sources = useRef<Map<string, EventSource>>(new Map())
  const mounted = useRef(true)

  const closeAll = useCallback(() => {
    for (const es of sources.current.values()) es.close()
    sources.current.clear()
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      closeAll()
    }
  }, [closeAll])

  const patch = useCallback((key: string, fn: (l: Lane) => Lane) => {
    setLanes((prev) => prev.map((l) => (l.key === key ? fn(l) : l)))
  }, [])

  const start = useCallback(
    async (specs: LaneSpec[]) => {
      closeAll()
      setLanes(specs.map((s) => ({
        key: s.key, label: s.label, sid: null, frames: [], shown: 0, status: 'starting',
        error: '', cards: 0,
      })))

      for (const spec of specs) {
        try {
          const started = await startSession(spec.request)
          if (!mounted.current) return
          patch(spec.key, (l) => ({ ...l, sid: started.session_id, status: 'live' }))
          const es = new EventSource(started.events)
          sources.current.set(spec.key, es)

          const onFrame = (ev: MessageEvent) => {
            // The server's journal flattens its payload into the frame, so the parsed object is
            // the `data` bag the rest of the UI reads - same shape useSession uses.
            let parsed: Record<string, unknown>
            try {
              parsed = JSON.parse(ev.data) as Record<string, unknown>
            } catch {
              return
            }
            const frame: Frame = {
              id: ev.lastEventId || String(parsed.seq || Date.now()),
              type: typeof parsed.type === 'string' ? parsed.type : 'status',
              data: parsed,
            }
            patch(spec.key, (l) => {
              const frames = [...l.frames, frame].slice(-600)
              const cards = frame.type === 'card' ? l.cards + 1 : l.cards
              const status: LaneStatus = frame.type === 'done' ? 'done' : l.status
              const error = frame.type === 'error'
                ? String((frame.data || {}).message || 'error frame')
                : l.error
              const shown = paceMs > 0 ? Math.min(l.shown, frames.length) : frames.length
              return { ...l, frames, cards, status, error, shown }
            })
            if (frame.type === 'done') {
              es.close()
              sources.current.delete(spec.key)
            }
          }

          for (const type of EVENT_TYPES) es.addEventListener(type, onFrame)
          es.onerror = () => {
            // The stream can end after `done` (server closes), which is not a failure. Only a
            // lane that never reached `done` is reported as failed, and even then the frames it
            // already has stay on screen.
            patch(spec.key, (l) => (l.status === 'done' ? l : {
              ...l, error: l.error || 'stream closed early', status: l.sid ? 'failed' : l.status,
            }))
          }
        } catch (e: unknown) {
          patch(spec.key, (l) => ({
            ...l, status: 'failed', error: e instanceof Error ? e.message : String(e),
          }))
        }
      }
    },
    [closeAll, patch],
  )

  /** Reveal the buffered frames at a readable pace. Off when `paceMs` is 0. */
  useEffect(() => {
    if (paceMs <= 0) return
    const id = window.setInterval(() => {
      setLanes((prev) => {
        let changed = false
        const next = prev.map((l) => {
          if (l.shown >= l.frames.length) return l
          changed = true
          return { ...l, shown: l.shown + 1 }
        })
        return changed ? next : prev
      })
    }, paceMs)
    return () => window.clearInterval(id)
  }, [paceMs])

  const showAll = useCallback(() => {
    setLanes((prev) => prev.map((l) => ({ ...l, shown: l.frames.length })))
  }, [])

  const clear = useCallback(() => {
    closeAll()
    setLanes(EMPTY_LANES)
  }, [closeAll])

  return { lanes, start, clear, showAll }
}
