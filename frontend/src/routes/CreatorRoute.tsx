import { useMemo, useState } from 'react'
import type { Facts } from '../lib/facts'
import { CardList } from '../components/cards'
import { PredictionPanel, TranscriptPanel } from '../components/creator'
import { AnswerSource } from '../components/ui'
import { Drawer, Empty, ErrLines, Pill, Photo, Step } from '../components/bits'
import { MatchBar } from '../components/match'
import { ModeSwitch } from '../components/gap'
import { UnderHood } from '../components/UnderHood'
import { AgentRoom } from '../components/agents'
import type { PackProps } from './shared'

export function CreatorRoute(props: PackProps & { facts: Facts | null }) {
  const { view, mode, setMode, start, reset, facts, manifest } = props
  const [query, setQuery] = useState('')
  const [typed, setTyped] = useState('')
  const running = view.starting || (!!view.sid && !view.done)

  // Counted from the kernel's own trace (`GET /api/session/{sid}/trace`), not from the SSE
  // frames: some of these kinds are published only on the pack's event channel, so counting
  // frames showed 0 while the trace said 2 and 1. The trace is the authority.
  const kindCount = useMemo(() => {
    const m = new Map<string, number>()
    for (const e of view.traceEvents) m.set(e.kind, (m.get(e.kind) || 0) + 1)
    return m
  }, [view.traceEvents])
  const verifier = kindCount.get('kernel_verifier') || 0
  const settled = kindCount.get('prediction_settled') || 0
  const skipped = kindCount.get('episode_skipped_tier0') || 0
  const abstained = kindCount.get('asr_abstain') || 0

  const claims = view.cards.filter((c) => ['insight', 'conflict', 'query_answer'].includes(c.kind))
  const predictions = view.cards.filter((c) => ['prediction', 'settlement'].includes(c.kind))
  const other = view.cards.filter((c) => !['insight', 'conflict', 'query_answer', 'prediction', 'settlement'].includes(c.kind))

  const errors = [
    ...(view.actionError ? [view.actionError] : []),
    ...view.streamErrors,
    ...(view.done?.errors?.length ? view.done.errors.map((e) => `${e.where}: ${e.message}`) : []),
  ]

  const credit = facts?.photo_credits.find((c) => c.file.endsWith('crowd-celebration.jpg'))
  const status = view.starting ? 'Opening a session…' : view.done ? `Finished · ${view.done.cards} card(s)` : view.sid ? 'Listening and checking…' : 'Not started'

  return (
    <div className="page container">
      <header className="page-head with-photo">
        <div>
          <Pill tone="info">Watch Along · Dream11-shaped</Pill>
          <h1>The creator talks. The feed checks.</h1>
          <p className="page-lead">
            A Hinglish live stream is part fact, part opinion, part filler. This one transcribes it, checks every
            spoken number against the official feed, and labels each line so you can enjoy the take without being
            misled by it.
          </p>
        </div>
        <Photo src="/img/crowd-celebration.jpg" alt="Spectators on their feet at a cricket ground" ratio="4 / 3" credit={credit} />
      </header>

      <div className="gap-layout">
        <main className="gap-main">
          <section className="panel">
            <Step n={1} title="Turn the stream on" tone="violet">
              <ModeSwitch mode={mode} onChange={setMode} />
              <div className="run-row">
                <button
                  type="button"
                  className="btn primary"
                  disabled={running}
                  onClick={() => start({ pack: 'creator', scenario: 'ALL', mode })}
                >
                  {running ? 'Running…' : 'Check all 12 segments'}
                </button>
                {view.sid ? (
                  <button type="button" className="btn ghost" onClick={reset}>
                    Clear
                  </button>
                ) : null}
                <span className={`run-status ${view.done ? 'good' : view.sid ? 'live' : ''}`}>{status}</span>
              </div>

              <div className="query-row">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="or ask about the stream: “what did he say about the death overs?”"
                  aria-label="ask the stream"
                />
                <button
                  type="button"
                  className="btn ghost"
                  disabled={running || !query.trim()}
                  onClick={() => start({ pack: 'creator', scenario: 'ALL', mode, query: query.trim() })}
                >
                  Ask
                </button>
              </div>
              <p className="tiny dim">
                You get a card quoting the exact segment, or an honest “nothing matches”. It never invents a quote.
              </p>

              <div className="query-row" style={{ marginTop: 10 }}>
                <input
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder="or put words in the creator's mouth: “142 for 2, yeh match abhi bhi khula hai”"
                  aria-label="type what the creator said"
                />
                <button
                  type="button"
                  className="btn ghost"
                  disabled={running || !typed.trim()}
                  onClick={() =>
                    start({ pack: 'creator', scenario: 'custom', mode, text: typed.trim() })
                  }
                >
                  Check this line
                </button>
              </div>
              <p className="tiny dim">
                Type anything and it is checked exactly like a live line: claims against the feed, predictions
                logged and settled. The badge says <b>typed</b>, never “transcribed”.
              </p>
            </Step>
          </section>

          <AnswerSource layer={view.llmLayer} note={view.llmNote} served={view.badges?.llm} />

          <ErrLines lines={errors} />

          {view.sid ? (
            <section className="panel">
              <h2 className="panel-title">What the agents are doing</h2>
              <p className="tiny dim">
                Live, from the server: who is working, what they looked up, and what came back.
              </p>
              <AgentRoom frames={view.frames} pack="creator" kernel={props.kernel} compact
                         title="Watch Along pack" pacing={false} />
            </section>
          ) : null}

          <section className="panel">
            <h2 className="panel-title">Cards it committed</h2>
            {!claims.length && !other.length ? (
              <Empty
                icon="🎙️"
                title={view.sid ? 'Listening…' : 'Nothing yet'}
                sub={
                  view.sid
                    ? 'Transcribing, dropping the greetings without a model call, then checking each claim.'
                    : 'Press “Check all 12 segments”. You will see verified, conflict, opinion and prediction cards.'
                }
              />
            ) : (
              <CardList cards={[...claims, ...other]} />
            )}
          </section>

          <section className="panel">
            <h2 className="panel-title">Predictions, and whether they landed</h2>
            <PredictionPanel predictions={view.predictions} />
            {predictions.length ? <CardList cards={predictions} /> : null}
            <div className="facts-row">
              <div>
                <span>Numbers checked against the feed</span>
                <b>{verifier}</b>
              </div>
              <div>
                <span>Predictions settled on a real ball</span>
                <b>{settled}</b>
              </div>
              <div>
                <span>Segments skipped for free</span>
                <b>{skipped}</b>
              </div>
              <div>
                <span>Lines it refused to guess at</span>
                <b>{abstained}</b>
              </div>
            </div>
          </section>

          {facts ? (
            <section className="panel">
              <h2 className="panel-title">The stream, segment by segment</h2>
              <p className="tiny dim">
                Tap a segment to hear the audio the transcription ran on, and the live confidence if a session reported
                one. {facts.creator.segments.length} segments, {facts.match.creator || 'one creator'}.
              </p>
              <TranscriptPanel
                segments={facts.creator.segments}
                utterances={view.utterances}
                onSelect={(id) => start({ pack: 'creator', scenario: id, mode })}
              />
            </section>
          ) : null}

          <Drawer label="What the model is and is not allowed to do" hint="the rule the whole build follows">
            <p>
              The model proposes wording. Deterministic code decides whether a claim is verified or conflicting, which
              numbers may appear on a card, and whether a prediction hit or missed. A model never writes a truth badge
              and never authors a figure.
            </p>
            <p className="tiny dim">
              That is why the conflict card quotes the creator's stale score <i>and</i> the feed's real one instead of
              quietly “fixing” the sentence.
            </p>
          </Drawer>
        </main>

        <aside className="gap-side">
          {facts ? <MatchBar match={facts.match} state={facts.state} /> : null}
          <div className="panel slim">
            <h4>Reading the badges</h4>
            <ul className="mini-terms">
              <li>
                <b>verified</b> — the spoken number matches the feed at that moment.
              </li>
              <li>
                <b>conflicts</b> — the feed says something else; both are shown.
              </li>
              <li>
                <b>opinion</b> — a take, not a fact. Kept separate on purpose.
              </li>
              <li>
                <b>prediction</b> — a future claim, logged and settled on a later ball.
              </li>
            </ul>
          </div>
          <div className="panel slim">
            <h4>Why text, not video</h4>
            <p className="tiny">
              A fan should not spend 6 MB of mobile data to learn one score line. Cards are a few hundred bytes; the
              audio is there when you want to hear the voice itself.
            </p>
          </div>
        </aside>
      </div>

      <UnderHood view={view} stream={props.stream} runMode={manifest?.run_mode || 'cached-demo'} />
    </div>
  )
}
