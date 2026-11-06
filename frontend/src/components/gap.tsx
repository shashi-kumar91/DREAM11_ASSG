import type { PendingQuestion } from '../lib/types'
import type { SessionView } from '../lib/useSession'
import { Pill } from './bits'

/** The one question the agent is allowed to ask, with the fan's own buttons. */
export function AskCard({
  question,
  answeredText,
  onAnswer,
  disabled,
}: {
  question: PendingQuestion | null
  answeredText: string | null
  onAnswer: (p: { text?: string; optionIndex?: number }) => void
  disabled: boolean
}) {
  if (!question && !answeredText) return null
  if (!question && answeredText) {
    return (
      <div className="ask ask-answered">
        <div className="ask-kicker">You answered</div>
        <p className="ask-answer">“{answeredText}”</p>
      </div>
    )
  }
  const q = question as PendingQuestion
  return (
    <div className="ask">
      <div className="ask-kicker">The agent needs one thing from you</div>
      <p className="ask-question">{q.text}</p>
      {q.reason ? <p className="ask-reason">{q.reason}</p> : null}
      <div className="ask-options">
        {q.options.map((opt, i) => (
          <button
            key={opt}
            type="button"
            className="ask-option"
            disabled={disabled}
            onClick={() => onAnswer({ optionIndex: i })}
          >
            {opt}
          </button>
        ))}
      </div>
      <p className="ask-foot">
        It asks once, and only when two possible answers would produce different receipts.
      </p>
    </div>
  )
}

/** The spoiler shield, stated as a claim the server can back. */
export function SpoilerShield({ level, detail }: { level: string; detail: string }) {
  const tone = level === 'red' ? 'bad' : level === 'amber' ? 'warn' : level === 'green' ? 'good' : 'plain'
  return (
    <div className={`shield ${tone}`}>
      <span className="shield-icon">{level === 'red' ? '⚠️' : '🔒'}</span>
      <div>
        <b>{level === 'red' ? 'Something past your view was released' : 'Nothing past your view has been released'}</b>
        <p>{detail}</p>
      </div>
    </div>
  )
}

/**
 * Data cost, in fan words. `loaded` is what the server actually sent for this session;
 * `would` is what the policy says a video replay of the same window would have cost.
 */
export function DataCost({ loadedKb, wouldKb, reason }: { loadedKb: number; wouldKb: number; reason?: string }) {
  const saved = Math.max(0, wouldKb - loadedKb)
  return (
    <div className="datacost">
      <div className="dc-row">
        <span>Data this session actually used</span>
        <b>{loadedKb.toFixed(1)} KB</b>
      </div>
      {wouldKb > 0 ? (
        <>
          <div className="dc-row muted">
            <span>If it had loaded the video clips</span>
            <b>{wouldKb.toFixed(0)} KB</b>
          </div>
          <div className="dc-saved">Saved {saved.toFixed(0)} KB this session</div>
        </>
      ) : (
        <div className="dc-row muted">
          <span>Video downloaded</span>
          <b>none</b>
        </div>
      )}
      {reason ? <p className="dc-reason">{reason}</p> : null}
    </div>
  )
}

/** Clip tile: the only place in the product where a video byte is ever moved. */
export function ClipOffer({
  view,
  onTap,
}: {
  view: SessionView
  onTap: (eventId: string) => void
}) {
  const q = view.clipQuote
  const reads = view.clipReads
  if (!q) {
    return (
      <div className="clip clip-none">
        <div>
          <b>No video offered</b>
          <p>This receipt is text on purpose - a score line does not need a 12-second clip.</p>
        </div>
      </div>
    )
  }
  if (!q.offerable) {
    return (
      <div className="clip clip-refused">
        <div>
          <b>The clip was withheld</b>
          <p>{q.why || 'The verifier refused it.'}</p>
        </div>
        <Pill tone="warn">{q.overLabel || q.eventId}</Pill>
      </div>
    )
  }
  return (
    <div className="clip clip-offered">
      <div>
        <b>Want the 12 seconds anyway?</b>
        <p>
          Ball {q.overLabel} · about {q.bytesEstimateKb ?? '?'} KB. It is the only thing on this page that spends real data, and
          nothing is downloaded until you press it.
        </p>
      </div>
      <div className="clip-actions">
        <button type="button" className="btn ghost" onClick={() => onTap(q.eventId)}>
          Load the clip
        </button>
        {reads.map((r, i) => (
          <span key={i} className={`pill ${r.charged ? 'warn' : 'plain'}`}>
            {r.charged ? `downloaded ${r.kb} KB` : `refused: ${r.refusal || 'no'}`}
          </span>
        ))}
      </div>
    </div>
  )
}

/** A big two-state switch written as words, not as a toggle with a code name. */
export function ModeSwitch({ mode, onChange }: { mode: 'watch' | 'quiet'; onChange: (m: 'watch' | 'quiet') => void }) {
  return (
    <div className="modeswitch" role="radiogroup" aria-label="data mode">
      <button
        type="button"
        role="radio"
        aria-checked={mode === 'watch'}
        className={mode === 'watch' ? 'on' : ''}
        onClick={() => onChange('watch')}
      >
        <b>Watch mode</b>
        <span>A clip may be downloaded if you tap it, and it gets counted.</span>
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={mode === 'quiet'}
        className={mode === 'quiet' ? 'on' : ''}
        onClick={() => onChange('quiet')}
      >
        <b>Quiet mode</b>
        <span>No clip bytes ever leave the server. The meter records what it refused to send.</span>
      </button>
    </div>
  )
}
