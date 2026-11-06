import { useMemo, useState } from 'react'
import type { Facts } from '../lib/facts'
import { INTERRUPTION_WORD } from '../lib/facts'
import type { Card } from '../lib/types'
import { spoilerState } from '../lib/useSession'
import { AskCard, ClipOffer, DataCost, ModeSwitch, SpoilerShield } from '../components/gap'
import { BallStrip, MatchBar } from '../components/match'
import { CardList } from '../components/cards'
import { AnswerSource } from '../components/ui'
import { Drawer, Empty, ErrLines, Pill, Step } from '../components/bits'
import type { InterruptKind } from '../lib/api'
import { UnderHood } from '../components/UnderHood'
import { AgentRoom } from '../components/agents'
import { lastFrame } from '../lib/frames'
import type { PackProps } from './shared'

const HEARTBEATS: [InterruptKind, string][] = [
  ['ad_start', 'An ad starts'],
  ['ad_end', 'The ad ends'],
  ['stall', 'The video freezes'],
  ['resume', "I'm back watching"],
  ['join', 'I open the app'],
  ['play', 'I press play'],
]

const ICON: Record<string, string> = { ad: '📺', buffer: '⏳', app_switch: '📱', late_join: '🚪', unknown: '❔' }

export function GapRoute(props: PackProps & { facts: Facts | null }) {
  const { view, mode, setMode, start, reset, answer, interrupt, tapClip, derived, facts } = props
  const [sel, setSel] = useState('S1')
  const [ball, setBall] = useState('')
  const [kind, setKind] = useState('ad')
  const running = view.starting || (!!view.sid && !view.done)

  const scenarios = facts?.gap_scenarios || []
  const chosen = scenarios.find((s) => s.id === sel)

  const guardrails = useMemo(
    () => view.frames.filter((f) => ['guardrail', 'clip_rejected', 'terminal_vetoed', 'commit_rejected'].includes(f.type)),
    [view.frames],
  )
  const spoiler = spoilerState(view.episodeStart, view.cards, view.bytesRecords, view.releasedEvents, guardrails)

  const receiptCards: Card[] = view.cards.filter((c) => ['receipt', 'noop'].includes(c.kind))
  const otherCards = view.cards.filter((c) => !['receipt', 'noop'].includes(c.kind))
  const bytesFrame = lastFrame(view.frames, 'bytes')
  const resumeOver = String(view.episodeStart?.resume_over || chosen?.resume_over || '')
  const gapFrom = useMemo(() => {
    const b = view.boundary
    if (!b) return chosen ? undefined : undefined
    const ids = b.candidates || []
    const first = ids[0]
    const ev = facts?.feed.find((f) => f.id === first)
    return ev?.over
  }, [view.boundary, facts, chosen])

  const errors = [
    ...(view.actionError ? [view.actionError] : []),
    ...view.streamErrors,
    ...(view.done?.errors?.length ? view.done.errors.map((e) => `${e.where}: ${e.message}`) : []),
  ]

  const status = view.starting
    ? 'Opening a session…'
    : view.done
      ? `Finished · ${view.done.cards} card(s)`
      : view.sid
        ? 'Working — reading your player log'
        : 'Not started'

  return (
    <div className="page container">
      <header className="page-head">
        <Pill tone="warn">Gap Receipt · FanCode-shaped</Pill>
        <h1>What did I miss?</h1>
        <p className="page-lead">
          Your stream dropped. Pick what happened to you, and the agent rebuilds exactly the balls you never saw —
          never the one you paused on, and never a guessed score.
        </p>
      </header>

      <div className="gap-layout">
        <main className="gap-main">
          <section className="panel">
            <Step n={1} title="What happened to you?" tone="red">
              <div className="scn-list" role="radiogroup" aria-label="situation">
                {scenarios.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    role="radio"
                    aria-checked={sel === s.id}
                    className={`scn${sel === s.id ? ' on' : ''}`}
                    onClick={() => setSel(s.id)}
                  >
                    <span className="scn-icon">{ICON[s.interruption_type] || '❔'}</span>
                    <span className="scn-body">
                      <b>{INTERRUPTION_WORD[s.interruption_type] || s.name}</b>
                      <span className="scn-blurb">{s.blurb}</span>
                      <span className="scn-meta">
                        <Pill>{s.name}</Pill>
                        <Pill>back at {s.resume_over}</Pill>
                        <Pill tone={s.asks ? 'warn' : 'plain'}>{s.asks ? 'will ask you one question' : 'no question needed'}</Pill>
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </Step>
          </section>

          <section className="panel">
            <Step n={2} title="How much data may it spend?" tone="teal">
              <ModeSwitch mode={mode} onChange={setMode} />
              <div className="run-row">
                <button
                  type="button"
                  className="btn primary"
                  disabled={running || !sel}
                  onClick={() => start({ pack: 'gap', scenario: sel, mode })}
                >
                  {running ? 'Running…' : `Run scenario ${sel}`}
                </button>
                {view.sid ? (
                  <button type="button" className="btn ghost" onClick={reset}>
                    Clear
                  </button>
                ) : null}
                <span className={`run-status ${view.done ? 'good' : view.sid ? 'live' : ''}`}>{status}</span>
              </div>
            </Step>
          </section>

          <section className="panel">
            <Step n={3} title="Or tell it your own situation" tone="teal">
              <p className="tiny dim">
                The three above are recorded. This one is built from what you pick: any ball in the feed, and what
                happened to you. It runs on the live model pool, because no recording exists for a case nobody has
                run before.
              </p>
              <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                <label className="field">
                  <span>I came back at</span>
                  <select value={ball} onChange={(e) => setBall(e.target.value)} aria-label="resume ball">
                    {(facts?.feed || []).map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.over} · {e.score}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>and</span>
                  <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="what happened">
                    <option value="ad">an ad covered the screen</option>
                    <option value="stall">the stream froze</option>
                    <option value="app">I switched apps</option>
                    <option value="late">I joined late</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={running || !ball}
                  onClick={() =>
                    start({ pack: 'gap', scenario: 'custom', mode, resume_event_id: ball, interruption: kind })
                  }
                >
                  Run my own situation
                </button>
              </div>
            </Step>
          </section>

          <AnswerSource layer={view.llmLayer} note={view.llmNote} served={view.badges?.llm} />

          <ErrLines lines={errors} />

          <AskCard question={view.question} answeredText={view.answeredText} onAnswer={answer} disabled={!view.sid} />

          {view.sid ? (
            <section className="panel">
              <h2 className="panel-title">What the agents are doing</h2>
              <p className="tiny dim">
                Live, from the server: who is working, what they looked up, and what came back.
              </p>
              <AgentRoom
                frames={view.frames}
                pack="gap"
                kernel={props.kernel}
                compact
                title="Gap pack"
                pacing={false}
              />
            </section>
          ) : null}

          <section className="panel">
            <h2 className="panel-title">Your catch-up receipt</h2>
            {!receiptCards.length ? (
              <Empty
                icon="🧾"
                title={view.sid ? 'Building the receipt…' : 'Nothing yet'}
                sub={
                  view.sid
                    ? 'The agent is settling the boundary and checking the balls against the feed.'
                    : 'Pick a situation above, then press run. It takes about a second.'
                }
              />
            ) : (
              <CardList cards={receiptCards} />
            )}
            {receiptCards.length ? <div className="clip-slot"><ClipOffer view={view} onTap={tapClip} /></div> : null}
          </section>

          {otherCards.length ? (
            <section className="panel">
              <h2 className="panel-title">Also committed</h2>
              <CardList cards={otherCards} />
            </section>
          ) : null}

          <section className="panel">
            <h2 className="panel-title">How it decided where your gap started</h2>
            {view.boundary ? (
              <>
                <div className="boundary">
                  <div className="bcard">
                    <span>Possible last balls</span>
                    <b>{(view.boundary.candidate_overs || []).join(' · ') || 'none — only you can scope it'}</b>
                  </div>
                  <div className="bcard">
                    <span>Confidence in that guess</span>
                    <b>{Math.round((view.boundary.confidence || 0) * 100)}%</b>
                  </div>
                  <div className="bcard">
                    <span>Where you resumed</span>
                    <b>{resumeOver || '—'}</b>
                  </div>
                </div>
                {view.boundary.notes?.length ? (
                  <ul className="reason-list">
                    {view.boundary.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : (
              <Empty icon="📈" title="Run a scenario to see the boundary maths" sub="It reads your player's heartbeat log: play, ad_start, ad_end, stall, resume." />
            )}

            {facts ? (
              <div className="strip-slot">
                <h4>The balls, with your gap shaded</h4>
                <BallStrip feed={facts.feed} markFrom={gapFrom} markTo={resumeOver || undefined} />
                <p className="tiny dim">Shaded balls are inside the window the agent may talk about. Anything after your resume point stays locked.</p>
              </div>
            ) : null}

            <Drawer label="Break my stream again" hint="sends the same signal your phone would send">
              <p className="tiny dim">
                Press one and the agent re-reads where your gap starts. Nothing is faked here: this is the same
                signal a player sends when the screen changes.
              </p>
              <div className="chips">
                {HEARTBEATS.map(([key, word]) => (
                  <button
                    key={key}
                    type="button"
                    className="btn tiny-btn"
                    disabled={!view.sid || !!view.done}
                    onClick={() => interrupt(key)}
                  >
                    {word}
                  </button>
                ))}
              </div>
            </Drawer>
          </section>
        </main>

        <aside className="gap-side">
          {facts ? <MatchBar match={facts.match} state={facts.state} chaseScore={chosen?.resume_score} chaseOver={chosen?.resume_over} note="at your resume point" /> : null}

          <div className="panel slim">
            <h4>Your data</h4>
            <DataCost loadedKb={derived.loadedKb} wouldKb={derived.wouldKb} reason={bytesFrame ? String(bytesFrame.data.reason || '') : undefined} />
          </div>

          <div className="panel slim">
            <h4>Spoiler check</h4>
            <SpoilerShield level={spoiler.level} detail={spoiler.detail} />
          </div>

          <div className="panel slim">
            <h4>What the badges mean</h4>
            <ul className="mini-terms">
              <li>
                <b>evidenced / verified</b> — every number traces to a feed event the gate licensed.
              </li>
              <li>
                <b>no-op</b> — it looked and found nothing worth telling you.
              </li>
              <li>
                <b>nothing after {resumeOver || 'your cursor'}</b> — the release clamp, enforced server-side.
              </li>
            </ul>
          </div>
        </aside>
      </div>

      <UnderHood view={view} stream={props.stream} runMode={props.manifest?.run_mode || 'cached-demo'} />
    </div>
  )
}
