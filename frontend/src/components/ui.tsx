import type { ReactNode } from 'react'
import type { Health, Manifest, Pack, PlayerMode, Scenario } from '../lib/types'
import type { StreamState } from '../lib/useSession'

/**
 * A scenario declares what it should end in. That used to be printed as raw JSON next to the
 * button; a fan only needs the words, so the object is translated once here.
 */
const CARD_WORD: Record<string, string> = {
  receipt: 'ends with a receipt',
  noop: 'ends with nothing missed',
  status: 'ends with a status line',
  quote: 'ends with a quote card',
  conflict: 'ends with a wrong-score warning',
  confirmed: 'ends with a confirmed claim',
  prediction: 'ends with a tracked prediction',
  settlement: 'ends with the prediction settled',
  opinion: 'ends with an opinion, labelled',
}

export function expectedWords(expected?: Record<string, unknown>): string[] {
  if (!expected) return []
  const out: string[] = []
  const kind = expected.card_kind ?? expected.kind
  if (typeof kind === 'string' && CARD_WORD[kind]) out.push(CARD_WORD[kind])
  else if (typeof kind === 'string') out.push(`ends with ${kind}`)
  const asks = expected.asks
  if (asks === true) out.push('asks you one question')
  if (asks === false) out.push('needs no answer from you')
  return out
}

export function Panel({ title, children, tone }: { title: string; children: ReactNode; tone?: string }) {
  return (
    <section className={`panel${tone ? ` ${tone}` : ''}`}>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

export function Err({ lines }: { lines: string[] }) {
  if (!lines.length) return null
  return (
    <div className="err" role="alert">
      {lines.map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  )
}

/** Watch vs Quiet: sent as `mode` in POST /api/session, so it is a server behaviour. */
export function ModeToggle({
  mode,
  onChange,
  serverMode,
}: {
  mode: PlayerMode
  onChange: (m: PlayerMode) => void
  serverMode: string | null
}) {
  const quiet = mode === 'quiet'
  return (
    <div className="row" style={{ alignItems: 'stretch' }}>
      <div className="row" role="group" aria-label="Playback mode">
        <button
          type="button"
          className={quiet ? '' : 'on'}
          aria-pressed={!quiet}
          data-testid="mode-watch"
          style={quiet ? undefined : { borderColor: 'var(--cyan)', background: '#0f2830' }}
          onClick={() => onChange('watch')}
        >
          Watch
        </button>
        <button
          type="button"
          className={quiet ? 'on' : ''}
          aria-pressed={quiet}
          data-testid="mode-quiet"
          style={quiet ? { borderColor: 'var(--violet)', background: '#1b1430' } : undefined}
          onClick={() => onChange('quiet')}
        >
          Quiet
        </button>
      </div>
      <span className={`badge ${serverMode ? (serverMode === 'quiet' ? 'warn' : 'ok') : ''}`}>
        {serverMode ? `this run is in ${serverMode} mode` : 'no run yet'}
      </span>
      <span className="tiny dim" style={{ flex: 1, minWidth: 180 }}>
        {quiet
          ? 'Quiet: you get words and numbers only. The server refuses every video byte, so nothing is charged.'
          : 'Watch: a clip you tap is streamed and charged against your data.'}
      </span>
    </div>
  )
}

export function ScenarioPicker({
  scenarios,
  selected,
  onSelect,
  onStart,
  running,
  pack,
}: {
  scenarios: Scenario[]
  selected: string
  onSelect: (id: string) => void
  onStart: () => void
  running: boolean
  pack: Pack
}) {
  const cur = scenarios.find((s) => s.id === selected)
  return (
    <div>
      <div className="scenarios">
        {scenarios.map((s) => (
          <button
            key={s.id}
            type="button"
            data-testid={`scn-${s.id}`}
            className={`scn${s.id === selected ? ' sel' : ''}`}
            onClick={() => onSelect(s.id)}
          >
            <b>
              {s.id} · {s.name}
            </b>
            <p>{s.blurb}</p>
            <span className="chips" style={{ marginTop: 5 }}>
              {pack === 'gap' && s.interruption_type ? <span className="badge">{s.interruption_type}</span> : null}
              {pack === 'gap' && s.resume_over ? <span className="badge">resume {s.resume_over}</span> : null}
              {pack === 'gap' ? (
                <span className={`badge ${s.asks ? 'warn' : ''}`}>{s.asks ? 'asks the fan' : 'no question'}</span>
              ) : null}
              {expectedWords(s.expected).map((w) => (
                <span key={w} className="badge">
                  {w}
                </span>
              ))}
            </span>
          </button>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button
          type="button"
          data-testid="run-scenario"
          onClick={onStart}
          disabled={running || !selected}
          style={{ borderColor: 'var(--green)', background: '#0b2015', fontWeight: 600 }}
        >
          {running ? 'run in flight…' : `Run ${cur ? cur.id : selected} on ${pack}`}
        </button>
        {cur ? (
          <span className="tiny dim">
            {expectedWords(cur.expected).join(' · ') || 'nothing is expected in advance'}
          </span>
        ) : null}
      </div>
    </div>
  )
}

export function SourceBadges({ badges, runMode }: { badges: Record<string, string>; runMode: string }) {
  const keys = ['llm', 'feed', 'telemetry', 'frames', 'asr']
  const shown = keys.filter((k) => badges[k])
  return (
    <div className="row">
      <span className="badge info">run_mode: {runMode || '-'}</span>
      {(shown.length ? shown : keys).map((k) => {
        const v = badges[k] || 'not reported'
        const tone =
          k === 'llm' ? (v === 'live' ? 'ok' : v === 'unavailable' ? 'bad' : 'info') : ''
        return (
          <span key={k} className={`badge ${tone}`}>
            {k}: {v}
          </span>
        )
      })}
    </div>
  )
}

export function StreamBadge({ stream }: { stream: StreamState }) {
  const tone = stream === 'open' ? 'ok' : stream === 'dropped' ? 'bad' : stream === 'retrying' ? 'warn' : 'idle'
  return (
    <span className={`badge ${tone === 'idle' ? '' : tone}`}>
      <i className={`dot ${tone}`} />{' '}
      {stream === 'open' ? 'updating live' : stream === 'dropped' ? 'connection lost, retrying' : stream === 'retrying' ? 'reconnecting' : 'not connected'}
    </span>
  )
}

/**
 * One sentence about how this run was answered. It exists because a live run and a replayed one
 * look identical on screen, and a fan (or a reviewer) deserves to know which they are looking at
 * without opening a developer panel.
 *
 * `layer` is what the run ASKED for; `served` (the `llm` badge) is what actually answered. When
 * they disagree - a live run whose pool died on a 429 - the badge is the truth and the sentence
 * must not claim "answered by live models" next to a badge that says `unavailable`.
 */
export function AnswerSource({ layer, note, served }: { layer: string; note: string; served?: string }) {
  if (!layer) return null
  const fell = !!served && served !== 'none' && served !== 'live' && served !== 'cassette'
  const word =
    layer === 'off' ? 'answered with no model at all — code only'
      : served === 'live' ? 'answered by live models'
        : served === 'cassette' ? 'answered from recorded model turns'
          : fell ? 'the model did not answer this step'
            : layer === 'live' ? 'answered by live models'
              : 'answered from recorded model turns'
  const tone =
    layer === 'off' ? 'warn'
      : served === 'live' ? 'ok'
        : served === 'cassette' ? 'info'
          : fell ? 'warn'
            : layer === 'live' ? 'ok' : 'info'
  return (
    <div className="answer-source">
      <span className={`badge ${tone}`}>{word}</span>
      {note ? <span className="as-note">{note}</span> : null}
    </div>
  )
}

export function HealthChips({ health, manifest }: { health: Health | null; manifest: Manifest | null }) {
  if (!health) return <span className="badge bad">api unreachable</span>
  return (
    <>
      <span className={`badge ${health.ok ? 'ok' : 'bad'}`}>health ok={String(health.ok)}</span>
      <span className="badge info">run_mode {health.run_mode}</span>
      <span className="badge">llm {health.llm_backend}</span>
      <span className="badge">cassettes {health.cassettes}</span>
      <span className="badge">sessions {health.sessions}</span>
      <span className="badge">asr {health.asr}</span>
      <span className="badge">ask_timeout {health.ask_timeout_s}s</span>
      <span className={`badge ${health.frontend_built ? 'ok' : 'bad'}`}>
        frontend_built {String(health.frontend_built)}
      </span>
      {manifest?.match ? (
        <span className="badge">
          {manifest.match.title} · target {String(manifest.match.target ?? '-')} · {manifest.match.total_overs} ov
        </span>
      ) : null}
    </>
  )
}

export function Bar({
  label,
  used,
  max,
  unit,
}: {
  label: string
  used: number
  max: number | null
  unit?: string
}) {
  const pct = max && max > 0 ? Math.min(100, (used / max) * 100) : 0
  const tone = max ? (used >= max ? 'red' : used / max > 0.75 ? 'amber' : 'green') : ''
  return (
    <div className="meter-row">
      <span className="dim">{label}</span>
      <span className={`bar ${tone}`}>
        <i style={{ width: `${max ? pct : 0}%` }} />
      </span>
      <span style={{ textAlign: 'right' }}>
        {used}
        {max ? `/${max}` : ''} {unit || ''}
      </span>
    </div>
  )
}
