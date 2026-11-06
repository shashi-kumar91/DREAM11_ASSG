import type { FeedEvent, FrameFact, MatchMeta, MatchState } from '../lib/facts'
import { Photo } from './bits'

/**
 * The match strip. Every value here is a fixture value passed in from `/api/facts`; the first
 * version of this component hard-coded "147/5", "Need 29 off 18", "Rohan Bedi 8* (5b)" and
 * "IPL 2026 MATCH 48", which were four separate inventions. Now the numbers are arguments.
 */
export function MatchBar({
  match,
  state,
  chaseScore,
  chaseOver,
  note,
}: {
  match: MatchMeta
  state?: MatchState
  chaseScore?: string
  chaseOver?: string
  note?: string
}) {
  const score = chaseScore || (state ? state.score : '')
  const over = chaseOver || (state ? state.last_over : '')
  const rrr = state?.rrr ?? null
  return (
    <div className="matchbar">
      <div className="matchbar-top">
        <span className="live-dot" />
        <span className="matchbar-live">SIMULATED REPLAY</span>
        <span className="matchbar-comp">{match.title}</span>
        {note ? <span className="matchbar-note">{note}</span> : null}
      </div>
      <div className="matchbar-grid">
        <div className="team">
          <span className="team-dot chasing">CK</span>
          <div>
            <div className="team-name">{match.team_b}</div>
            <div className="team-score">
              {score} <small>{over ? `after ${over}` : ''}</small>
            </div>
          </div>
        </div>
        <div className="match-mid">
          <div className="match-target">Target {match.target}</div>
          <div className="match-sub">
            {match.total_overs} overs · {state ? `${state.balls_in_fixture} balls in this replay` : 'replay'}
          </div>
        </div>
        <div className="team right">
          <div>
            <div className="team-name">{match.team_a}</div>
            <div className="team-overs">set the target</div>
          </div>
          <span className="team-dot">MV</span>
        </div>
      </div>
      {rrr !== null ? (
        <div className="matchbar-foot">
          Required run rate at {over}: <b>{rrr.toFixed(2)}</b> per over
        </div>
      ) : null}
    </div>
  )
}

const BALL_TONE = (e: FeedEvent): string => {
  if (e.wicket) return 'wicket'
  if (e.runs >= 6) return 'six'
  if (e.runs >= 4) return 'four'
  if (e.runs === 0) return 'dot'
  return 'runs'
}

const BALL_LABEL = (e: FeedEvent): string => {
  if (e.wicket) return 'W'
  if (e.runs >= 4) return String(e.runs)
  return e.runs === 0 ? '•' : String(e.runs)
}

/**
 * Ball-by-ball strip, straight from the fixture feed. `markFrom` / `markTo` shade the window a
 * scenario cares about, which is how the page explains "you were away here" with the real balls.
 */
export function BallStrip({
  feed,
  markFrom,
  markTo,
  compact = false,
}: {
  feed: FeedEvent[]
  markFrom?: string
  markTo?: string
  compact?: boolean
}) {
  const from = markFrom ? feed.findIndex((e) => e.over === markFrom) : -1
  const to = markTo ? feed.findIndex((e) => e.over === markTo) : -1
  return (
    <div className={`strip${compact ? ' compact' : ''}`} role="list" aria-label="ball by ball">
      {feed.map((e, i) => {
        const inGap = from >= 0 && to >= 0 && i >= from && i <= to
        return (
          <div
            key={e.id}
            role="listitem"
            className={`ball ${BALL_TONE(e)}${inGap ? ' in-gap' : ''}`}
            style={{ animationDelay: `${Math.min(i * 45, 700)}ms` }}
            title={`${e.over} · ${e.text} · ${e.score}`}
          >
            <span className="ball-over">{e.over}</span>
            <span className="ball-pip">{BALL_LABEL(e)}</span>
            {!compact ? <span className="ball-score">{e.score}</span> : null}
          </div>
        )
      })}
    </div>
  )
}

/**
 * The scoreboard frames the receipt path actually reads. `ocr` is the live pixel read from the
 * server, so a reader can compare "what the picture says" with "what the reader saw".
 */
export function FramesGallery({ frames, credit }: { frames: FrameFact[]; credit?: { artist: string; license: string; source: string; title: string } }) {
  return (
    <div className="frames-grid">
      {frames.map((f, i) => (
        <div key={f.frame_id} className={`frame-card${f.injected_fault ? ' faulty' : ''}`} style={{ animationDelay: `${i * 70}ms` }}>
          <Photo src={f.url} alt={`Rendered scoreboard frame at over ${f.over}`} ratio="760 / 220" className="frame-shot" credit={credit} />
          <div className="frame-body">
            <div className="frame-head">
              <b>Over {f.over}</b>
              {f.injected_fault ? <span className="pill bad">drawn wrong on purpose</span> : null}
            </div>
            <div className="frame-read">
              <span>Reader saw</span>
              <b>{f.ocr.readable ? `${f.ocr.score} · ${f.ocr.over} · RRR ${f.ocr.rrr}` : 'could not read it'}</b>
            </div>
            <div className={`frame-true${f.ocr.score && f.feed_score && f.ocr.score !== f.feed_score ? ' mismatch' : ''}`}>
              Feed says {f.feed_score || '—'}
              {f.ocr.score && f.feed_score
                ? f.ocr.score === f.feed_score
                  ? ' → the reader and the feed agree'
                  : ` → the reader saw ${f.ocr.score}, so this clip is refused`
                : ''}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
