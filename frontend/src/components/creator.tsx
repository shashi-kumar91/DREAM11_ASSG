import { useMemo, useState } from 'react'
import type { Frame } from '../lib/types'
import type { SegmentFact } from '../lib/facts'
import type { PredictionRow, SessionView, UtteranceRow } from '../lib/useSession'
import { Pill } from './bits'

const fmt = (t?: number | null) => {
  if (t === null || t === undefined) return '—'
  const m = Math.floor(t / 60)
  const s = Math.round(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * The creator's own words, with the audio the transcription ran on.
 *
 * The segments come from the fixture transcript (`/api/facts`); any confidence figure shown
 * comes from the session's own `utterance` frames, because the live route is what produces a
 * real number. Nothing here estimates a confidence for the cached path.
 */
export function TranscriptPanel({
  segments,
  utterances,
  onSelect,
}: {
  segments: SegmentFact[]
  utterances: UtteranceRow[]
  onSelect?: (id: string) => void
}) {
  const live = useMemo(() => {
    const m = new Map<string, UtteranceRow>()
    utterances.forEach((u) => m.set(u.id, u))
    return m
  }, [utterances])
  const [open, setOpen] = useState<string | null>(null)

  return (
    <ol className="transcript">
      {segments.map((s, i) => {
        const l = live.get(s.id)
        const isOpen = open === s.id
        return (
          <li key={s.id} className={`seg${isOpen ? ' open' : ''}`} style={{ animationDelay: `${i * 40}ms` }}>
            <button type="button" className="seg-head" onClick={() => setOpen(isOpen ? null : s.id)} aria-expanded={isOpen}>
              <span className="seg-id">{s.id}</span>
              <span className="seg-time">
                {fmt(s.t_start)}–{fmt(s.t_end)}
              </span>
              <span className="seg-text">{s.text}</span>
              <span className="seg-tags">
                {s.noisy ? <Pill tone="warn">noisy audio</Pill> : null}
                {l?.asr_confidence !== undefined ? <Pill tone="info">ASR {l.asr_confidence.toFixed(2)}</Pill> : null}
                {l?.asr_source ? <Pill>{l.asr_source}</Pill> : null}
              </span>
            </button>
            {isOpen ? (
              <div className="seg-body">
                <p className="seg-full">{s.text}</p>
                <audio controls preload="none" src={s.audio}>
                  Your browser cannot play this audio.
                </audio>
                <div className="seg-actions">
                  <span className="tiny">Speaker: {s.speaker}</span>
                  {onSelect ? (
                    <button type="button" className="btn tiny-btn" onClick={() => onSelect(s.id)}>
                      Check just this segment
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

const STATUS_WORD: Record<string, { text: string; tone: 'good' | 'bad' | 'warn' | 'plain' }> = {
  hit: { text: 'Hit - it happened', tone: 'good' },
  miss: { text: 'Miss - it did not happen', tone: 'bad' },
  open: { text: 'Still open', tone: 'warn' },
  pending: { text: 'Waiting for the over to finish', tone: 'warn' },
  void: { text: 'Void', tone: 'plain' },
}

/** Predictions the creator made, and the exact feed tally that later settled them. */
export function PredictionPanel({ predictions }: { predictions: PredictionRow[] }) {
  if (!predictions.length) {
    return (
      <div className="empty-card small">
        <div className="empty-icon">🎯</div>
        <div>
          <b>No predictions yet</b>
          <p>Run the stream and every future claim the creator makes lands here, waiting to be checked.</p>
        </div>
      </div>
    )
  }
  return (
    <ul className="predictions">
      {predictions.map((p, i) => {
        const s = STATUS_WORD[p.status] || { text: p.status, tone: 'plain' as const }
        return (
          <li key={p.id} style={{ animationDelay: `${i * 60}ms` }}>
            <div className="pred-top">
              <Pill tone={s.tone}>{s.text}</Pill>
              {p.over !== null && p.over !== undefined ? <Pill>over {p.over}</Pill> : null}
            </div>
            <p className="pred-text">“{p.text}”</p>
            {p.tally_text ? <p className="pred-tally">What the feed counted: {p.tally_text}</p> : null}
            {p.settle_event_ids?.length ? <p className="pred-events">settled on {p.settle_event_ids.join(', ')}</p> : null}
          </li>
        )
      })}
    </ul>
  )
}

/** Claims the deterministic verifier rejected or flagged, quoted from the trace. */
export function VerdictPanel({ frames }: { frames: Frame[] }) {
  if (!frames.length) return null
  return (
    <ul className="verdicts">
      {frames.map((f, i) => (
        <li key={i}>
          <Pill tone="info">{String(f.data.utterance_id || 'segment')}</Pill>
          <span>{String(f.data.note || 'checked against the feed')}</span>
        </li>
      ))}
    </ul>
  )
}

/** Where a run stands, in one sentence a fan can read. */
export function RunStatusLine({ view }: { view: SessionView }) {
  if (view.actionError) return <p className="run-status bad">{view.actionError}</p>
  if (view.starting) return <p className="run-status">Opening a session…</p>
  if (!view.sid) return null
  if (view.done) return <p className="run-status good">Finished · {view.done.cards} card(s) committed</p>
  return <p className="run-status live">Working…</p>
}
