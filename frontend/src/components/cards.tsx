import type { Card } from '../lib/types'
import { truthClass } from '../lib/frames'
import { Drawer, Pill } from './bits'

/**
 * Card renderer. Every figure shown here is parsed out of the card the kernel committed
 * (`card.body`, `card.numbers`) - the previous version hard-coded "142/3 ➔ 147/5", "+0.40
 * (9.27 ➔ 9.67)" and "29 off 18 balls" into a strip that was painted onto *every* receipt,
 * including the one whose true shift was 8.48 -> 11.57. A receipt that lies about its own
 * numbers is worse than no receipt.
 */

const KIND_WORD: Record<string, string> = {
  receipt: 'Catch-up receipt',
  noop: 'Nothing to catch up on',
  insight: 'What the creator said',
  conflict: 'Creator contradicts the feed',
  prediction: 'Prediction logged',
  settlement: 'Prediction settled',
  query_answer: 'Answered from the stream',
  status: 'Status',
}

/**
 * Plain-language reading of a truth label. The label itself always stays visible next to it.
 * Keys are the *server's* own strings (`kernel/schemas.py: TruthLabel`), lowercased - an
 * earlier version guessed nicer names and silently explained nothing.
 */
const TRUTH_WORD: Record<string, string> = {
  'verified vs feed': 'the spoken words match the official ball-by-ball feed',
  'conflicts with feed': 'the feed says something different — both are shown, nothing is hidden',
  'opinion, not match fact': 'an opinion, kept visibly separate from facts',
  'prediction, being tracked': 'a claim about a future ball; it gets settled later',
  'prediction hit': 'checked on a later ball, and it happened',
  'prediction missed': 'checked on a later ball, and it did not happen',
  'prediction void': 'the ball it depended on never arrived, so it stays unsettled',
  'not checkable from feed': 'the feed cannot confirm or deny this',
  'creator said': 'kept exactly as spoken',
  'answer from creator memory': 'quoted from what the creator actually said',
  'evidence-backed': 'every figure traces to a feed event the gate licensed',
  'no evidence — nothing invented': 'the creator never covered this, and nothing was invented to fill it',
}

export interface ReceiptParts {
  scope: string
  from: string
  to: string
  bullets: { over: string; kind: string; text: string; before: string; after: string }[]
  scoreFrom: string
  scoreTo: string
  rrrFrom: string
  rrrTo: string
  rrrDelta: string
  need: string
  balls: string
  others: string
  why: string
  frames: string
  clamp: string
  noClip: boolean
  notes: string[]
}

export function parseReceipt(body: string, headline = ''): ReceiptParts | null {
  const lines = (body || '').split('\n').map((l) => l.trim()).filter(Boolean)
  const out: ReceiptParts = {
    scope: '',
    from: '',
    to: '',
    bullets: [],
    scoreFrom: '',
    scoreTo: '',
    rrrFrom: '',
    rrrTo: '',
    rrrDelta: '',
    need: '',
    balls: '',
    others: '',
    why: '',
    frames: '',
    clamp: '',
    noClip: false,
    notes: [],
  }
  // Some bodies lead with the window on the headline instead of inside the body. Use whichever
  // carries it, so the panel never loses the "which balls" fact.
  const head = /(\d+\.\d+)\s*(?:[–-]|to)\s*(\d+\.\d+):\s*(\S+)\s*->\s*(\S+)/.exec(headline)
  if (head) {
    out.from = head[1]
    out.to = head[2]
    out.scoreFrom = head[3]
    out.scoreTo = head[4]
    out.scope = `${head[1]} to ${head[2]}`
  }
  let touched = false
  for (const line of lines) {
    let m = /^You missed (\d+\.\d+)[–-](\d+\.\d+):\s*(\S+)\s*->\s*(\S+)/.exec(line)
    if (m) {
      out.from = m[1]
      out.to = m[2]
      out.scoreFrom = m[3]
      out.scoreTo = m[4]
      out.scope = `${m[1]} to ${m[2]}`
      touched = true
      continue
    }
    m = /^-\s*(\d+\.\d+)\s+([A-Z]+):\s*(.+?)\s*\(([^)]*?)\s*->\s*([^)]*?)\)\s*$/.exec(line)
    if (m) {
      out.bullets.push({ over: m[1], kind: m[2], text: m[3], before: m[4], after: m[5] })
      touched = true
      continue
    }
    m = /^Score (\S+)\s*->\s*(\S+)\.\s*Required rate ([\d.]+)\s*->\s*([\d.]+)\s*\(([+-]?[\d.]+)\);\s*(\d+) needed off (\d+) balls/.exec(line)
    if (m) {
      out.scoreFrom = m[1]
      out.scoreTo = m[2]
      out.rrrFrom = m[3]
      out.rrrTo = m[4]
      out.rrrDelta = m[5]
      out.need = m[6]
      out.balls = m[7]
      touched = true
      continue
    }
    if (/^Other balls in the window:/i.test(line)) {
      out.others = line.replace(/^Other balls in the window:\s*/i, '')
      continue
    }
    if (/^Why it matters:/i.test(line)) {
      out.why = line.replace(/^Why it matters:\s*/i, '')
      continue
    }
    if (/^Scoreboard frames read/i.test(line)) {
      out.frames = line.replace(/^Scoreboard frames read[^:]*:\s*/i, '')
      continue
    }
    if (/^Nothing after/i.test(line)) {
      out.clamp = line
      touched = true
      continue
    }
    if (/^No clip loaded/i.test(line)) {
      out.noClip = true
      continue
    }
    out.notes.push(line)
  }
  return touched || head ? out : null
}

function TruthBadge({ label }: { label: string }) {
  const word = TRUTH_WORD[label.toLowerCase()] || ''
  return (
    <div className={`truth ${truthClass(label)}`}>
      <span className="truth-label">{label.replace(/_/g, ' ')}</span>
      {word ? <span className="truth-word">{word}</span> : null}
    </div>
  )
}

function Bullet({ b }: { b: ReceiptParts['bullets'][number] }) {
  const tone = b.kind === 'WICKET' ? 'wicket' : b.kind === 'BOUNDARY' ? 'boundary' : 'plain'
  return (
    <li className={`receipt-bullet ${tone}`}>
      <span className="rb-over">{b.over}</span>
      <span className="rb-kind">{b.kind === 'WICKET' ? 'Wicket' : b.kind === 'BOUNDARY' ? 'Boundary' : b.kind}</span>
      <span className="rb-text">{b.text}</span>
      <span className="rb-score">
        {b.before} → {b.after}
      </span>
    </li>
  )
}

export function CardTile({ card, index = 0 }: { card: Card; index?: number }) {
  const parts = card.kind === 'receipt' ? parseReceipt(card.body, card.headline) : null
  const isNoop = card.kind === 'noop'
  const prose = (card.body || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)

  return (
    <article className={`card card-${card.kind} ${truthClass(card.truth_label)}`} style={{ animationDelay: `${index * 90}ms` }}>
      <header className="card-head">
        <div className="card-kinds">
          <span className="card-kind">{KIND_WORD[card.kind] || card.kind}</span>
          {card.timestamp_label ? <span className="card-time">{card.timestamp_label}</span> : null}
        </div>
        <TruthBadge label={card.truth_label} />
      </header>

      <h3 className="card-title">{card.headline}</h3>

      {parts ? (
        <div className="receipt">
          <div className="receipt-metrics">
            {parts.scope ? (
              <div className="metric">
                <span>Balls you never saw</span>
                <b>{parts.scope}</b>
              </div>
            ) : null}
            <div className="metric">
              <span>Score went</span>
              <b>
                {parts.scoreFrom} <i>→</i> {parts.scoreTo}
              </b>
            </div>
            {parts.rrrFrom ? (
              <div className="metric">
                <span>Required rate</span>
                <b>
                  {parts.rrrFrom} <i>→</i> {parts.rrrTo}{' '}
                  <em className={parts.rrrDelta.startsWith('-') ? 'down' : 'up'}>({parts.rrrDelta})</em>
                </b>
              </div>
            ) : null}
            {parts.need ? (
              <div className="metric">
                <span>To win</span>
                <b>
                  {parts.need} off {parts.balls} balls
                </b>
              </div>
            ) : null}
          </div>

          {parts.bullets.length ? (
            <ul className="receipt-list">
              {parts.bullets.map((b) => (
                <Bullet key={`${b.over}-${b.kind}`} b={b} />
              ))}
            </ul>
          ) : null}

          {parts.why ? <p className="receipt-why">{parts.why}</p> : null}

          <div className="receipt-facts">
            {parts.others ? (
              <div>
                <span>Quiet balls in that window</span>
                <b>{parts.others}</b>
              </div>
            ) : null}
            {parts.frames ? (
              <div>
                <span>Scoreboard photo, read from the pixels</span>
                <b>{parts.frames}</b>
              </div>
            ) : null}
            {parts.noClip ? (
              <div>
                <span>Video downloaded</span>
                <b>none - this receipt is text</b>
              </div>
            ) : null}
          </div>

          {parts.clamp ? <div className="clamp-note">🔒 {parts.clamp}</div> : null}
          {parts.notes.map((n, i) => (
            <p key={i} className="receipt-note">
              {n}
            </p>
          ))}
        </div>
      ) : (
        <div className="card-prose">
          {prose.map((line, i) => (
            <p key={i} className={isNoop && i === 0 ? 'lead' : undefined}>
              {line}
            </p>
          ))}
        </div>
      )}

      {card.attribution ? <p className="card-attrib">{card.attribution}</p> : null}

      <Drawer label="Where every number came from" hint={`${card.cited_event_ids.length} feed events · ${card.cited_utterance_ids.length} audio segments`}>
        <div className="evidence">
          <div>
            <span>Template</span>
            <code>{card.template_used || 'runtime'}</code>
          </div>
          <div>
            <span>Card id</span>
            <code>{card.id}</code>
          </div>
          <div>
            <span>Numbers licensed by the gate</span>
            <code>{card.numbers.length ? card.numbers.join(', ') : 'none'}</code>
          </div>
          {card.cited_event_ids.length ? (
            <div>
              <span>Feed events cited</span>
              <code>{card.cited_event_ids.join(', ')}</code>
            </div>
          ) : null}
          {card.cited_utterance_ids.length ? (
            <div>
              <span>Audio segments cited</span>
              <code>{card.cited_utterance_ids.join(', ')}</code>
            </div>
          ) : null}
          {card.cited_tally_ids.length ? (
            <div>
              <span>Feed tallies cited</span>
              <code>{card.cited_tally_ids.join(', ')}</code>
            </div>
          ) : null}
        </div>
      </Drawer>
    </article>
  )
}

export function CardList({ cards }: { cards: Card[] }) {
  if (!cards.length) return null
  return (
    <div className="card-list">
      {cards.map((c, i) => (
        <CardTile key={c.id} card={c} index={i} />
      ))}
    </div>
  )
}

export { Pill }
