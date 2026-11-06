import { useMemo, useState } from 'react'
import type { BytesRecord, BudgetUsed, BytesSnapshot, Frame } from '../lib/types'
import type { SessionView, StreamState } from '../lib/useSession'
import { str } from '../lib/frames'
import { humanize } from '../lib/human'
import { Bar, SourceBadges, StreamBadge } from './ui'

/**
 * The panel behind the product. It used to print the wire format and a raw JSON blob per row -
 * accurate, unreadable, and the first thing anyone complained about. Every row here is one
 * plain sentence now, built by `humanize`, from the same server frames. Nothing was removed
 * from the server; it simply stopped being shouted at the fan.
 */

function toneOf(f: Frame): string {
  const h = humanize(f)
  return h.tone
}

export function TraceList({ frames }: { frames: Frame[] }) {
  const [filter, setFilter] = useState('')
  const [only, setOnly] = useState<string | null>(null)

  const groups = useMemo(() => {
    const seen = new Map<string, { label: string; n: number }>()
    for (const f of frames) {
      const h = humanize(f)
      const key = h.what
      const got = seen.get(key)
      if (got) got.n += 1
      else seen.set(key, { label: key, n: 1 })
    }
    return [...seen.entries()].slice(0, 8)
  }, [frames])

  const shown = useMemo(() => {
    const t = filter.trim().toLowerCase()
    return frames.filter((f) => {
      const h = humanize(f)
      if (only && !h.what.toLowerCase().includes(only.toLowerCase())) return false
      if (t && !`${h.what} ${h.detail}`.toLowerCase().includes(t)) return false
      return true
    })
  }, [frames, filter, only])

  return (
    <div className="behind">
      <div className="row">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search what happened…"
          aria-label="search the decision trail"
        />
        <span className="badge">{frames.length} steps</span>
        <span className="spacer" />
        {only ? (
          <button type="button" className="btn tiny" onClick={() => setOnly(null)}>
            show everything
          </button>
        ) : null}
      </div>
      <div className="chips" style={{ margin: '8px 0' }}>
        {groups.map(([key, g]) => (
          <button
            key={key}
            type="button"
            className={`chip${only === key ? ' on' : ''}`}
            onClick={() => setOnly(g.label)}
          >
            {g.label} · {g.n}
          </button>
        ))}
      </div>
      {!frames.length ? <div className="empty">Nothing has happened yet — start a run.</div> : null}
      {shown.map((f, i) => {
        const h = humanize(f)
        const seq = /^\d+$/.test(f.id) ? f.id : String(i + 1)
        return (
          <div key={`${seq}-${i}`} className={`step-row ${h.tone || toneOf(f)}`}>
            <span className="step-n">{seq}</span>
            <span className="step-what">{h.what}</span>
            <span className="step-detail">{h.detail || '—'}</span>
          </div>
        )
      })}
      {frames.length && !shown.length ? <div className="empty">Nothing matches that search.</div> : null}
    </div>
  )
}

const METER_WORD: Record<string, string> = {
  llm_calls: 'thinking turns',
  tool_calls: 'look-ups',
  cards: 'cards published',
  trace_events: 'steps recorded',
  clip_loads: 'videos played',
  refusals: 'videos refused',
  ask_timeouts: 'questions timed out',
}

export function BudgetMeter({ budget, errors }: { budget: BudgetUsed | null; errors: string[] }) {
  if (!budget) return <div className="empty">No run yet, so there is nothing spent.</div>
  const b = budget.budget
  const rows: [string, number, number | null][] = [
    ['thinking turns', budget.llm_calls, b ? b.max_llm_calls : null],
    ['look-ups', budget.tool_calls, b ? b.max_tool_calls : null],
    ['cards published', budget.cards, null],
    ['steps recorded', budget.trace_events, null],
    ['videos played', budget.clip_loads ?? 0, null],
    ['videos refused', budget.refusals ?? 0, null],
    ['questions timed out', budget.ask_timeouts ?? 0, null],
  ]
  return (
    <div>
      <div className="meters">
        {rows.map(([label, used, max]) => (
          <Bar key={label} label={METER_WORD[label] || label} used={used} max={max} />
        ))}
      </div>
      <div className="kv" style={{ marginTop: 10 }}>
        <b>Data you paid for</b>
        <span>{budget.bytes_kb} KB</span>
        <b>Data you would have paid for</b>
        <span>{budget.would_have_kb} KB</span>
        <b>Time limit per run</b>
        <span>{b ? `${Math.round(b.max_ms / 1000)}s` : '—'}</span>
        <b>Problems</b>
        <span>{errors.length ? errors.join('; ') : 'none'}</span>
      </div>
    </div>
  )
}

/** Two lines on one chart: what left the server, and the bill the fan never got. */
export function BytesGraph({ records, snapshot }: { records: BytesRecord[]; snapshot: BytesSnapshot | null }) {
  const pts = useMemo(() => records.map((r, i) => ({ i, served: r.total_kb, would: r.would_have_kb })), [records])

  const W = 320
  const H = 120
  const max = Math.max(1, ...pts.map((p) => Math.max(p.served, p.would)))
  const path = (key: 'served' | 'would') =>
    pts.length === 0
      ? ''
      : pts
          .map((p, idx) => {
            const x = pts.length === 1 ? W : (idx / (pts.length - 1)) * W
            const y = H - (p[key] / max) * (H - 8) - 4
            return `${idx === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
          })
          .join(' ')

  const served = snapshot?.kb ?? pts[pts.length - 1]?.served ?? 0
  const would = snapshot?.would_have_kb ?? pts[pts.length - 1]?.would ?? 0
  const saved = snapshot?.saved_kb ?? Math.max(0, would - served)

  return (
    <div>
      <div className="row">
        <span className="badge ok">{served} KB sent</span>
        <span className="badge warn">{would} KB if everything had loaded</span>
        <span className="badge info">{saved.toFixed(1)} KB saved</span>
      </div>
      <svg className="graph" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="data used over the run">
        <rect x="0" y="0" width={W} height={H} fill="#0a1020" />
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" y1={H * g} x2={W} y2={H * g} stroke="#172034" strokeWidth="1" />
        ))}
        <path d={path('would')} fill="none" stroke="var(--amber)" strokeWidth="1.6" strokeDasharray="4 3" />
        <path d={path('served')} fill="none" stroke="var(--green)" strokeWidth="2" />
        {pts.map((p, idx) =>
          p.served > 0 ? (
            <circle
              key={idx}
              cx={pts.length === 1 ? W / 2 : (idx / (pts.length - 1)) * W}
              cy={H - (p.served / max) * (H - 8) - 4}
              r="2.4"
              fill="var(--green)"
            />
          ) : null,
        )}
      </svg>
      <div className="tiny dim" style={{ marginTop: 6 }}>
        Solid green is data actually sent to your phone. Dashed amber is what it would have cost if
        every clip it offered had loaded.
      </div>
    </div>
  )
}

const SOURCE_WORD: Record<string, string> = {
  llm: 'language model',
  feed: 'ball-by-ball feed',
  telemetry: 'your player',
  frames: 'scoreboard photos',
  asr: 'speech recognition',
}

const STATE_WORD: Record<string, string> = {
  live: 'live now',
  cassette: 'a recorded run, replayed',
  replay_fixture: 'recorded match data',
  simulated: 'simulated, labelled as such',
  unavailable: 'not available',
  typed: 'typed by you',
}

export function JuryPane({
  view,
  stream,
  open,
  onToggle,
  runMode,
}: {
  view: SessionView
  stream: StreamState
  open: boolean
  onToggle: () => void
  runMode?: string
}) {
  void runMode
  const [tab, setTab] = useState<'trail' | 'cost' | 'data'>('trail')
  const done = view.done
  const badges = view.badges

  return (
    <div className="jury">
      <div className="jury-head">
        <button type="button" data-testid="jury-toggle" className="btn tiny" onClick={onToggle}>
          {open ? 'Hide the working' : 'Show the working'}
        </button>
        <StreamBadge stream={stream} />
        <span className={`badge ${done ? 'ok' : ''}`}>
          {done ? 'run finished' : view.frames.length ? 'running' : 'idle'}
        </span>
        {view.request ? (
          <span className="badge">
            {view.request.pack === 'gap' ? 'Gap Receipt' : 'Watch Along'} · {view.request.mode}
          </span>
        ) : null}
        {open ? (
          <span className="jury-tabs">
            {(
              [
                ['trail', 'what happened'],
                ['cost', 'what it cost'],
                ['data', 'where it came from'],
              ] as const
            ).map(([t, label]) => (
              <button
                key={t}
                type="button"
                data-testid={`jury-tab-${t}`}
                className={tab === t ? 'on' : ''}
                onClick={() => setTab(t)}
              >
                {label}
              </button>
            ))}
          </span>
        ) : null}
        <span className="spacer" />
        <span className="tiny dim">{view.frames.length} steps</span>
      </div>

      {open ? (
        <div className="jury-body">
          {tab === 'trail' ? <TraceList frames={view.frames} /> : null}
          {tab === 'cost' ? <BudgetMeter budget={view.budget} errors={view.streamErrors} /> : null}
          {tab === 'data' ? (
            <div className="grid">
              <div className="row">
                {(Object.keys(SOURCE_WORD) as string[]).map((k) => {
                  const v = badges[k] || 'not reported'
                  return (
                    <span key={k} className={`badge ${k === 'llm' && v === 'live' ? 'ok' : ''}`}>
                      {SOURCE_WORD[k]}: {STATE_WORD[v] || v}
                    </span>
                  )
                })}
              </div>
              <BytesGraph records={view.bytesRecords} snapshot={view.bytesSnapshot} />
              <div className="kv">
                <b>Cards you can see</b>
                <span>{view.cards.length}</span>
                <b>Steps recorded</b>
                <span>{view.traceEvents.length}</span>
                <b>Your answers</b>
                <span>{view.answeredText || 'none needed'}</span>
              </div>
              <p className="tiny dim">
                Every badge above is the server's own report for this run. “A recorded run, replayed”
                means a real model conversation was saved earlier and is being played back, so the
                page works with no key or network.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export { SourceBadges, str }
