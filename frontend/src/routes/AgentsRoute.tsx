import { useState } from 'react'
import type { KernelMap } from '../lib/kernel'
import { PACE_MS, useLanes, type LaneSpec } from '../lib/useLanes'
import type { Pack, PlayerMode } from '../lib/types'
import { AgentRace } from '../components/agents'
import { Pill } from '../components/bits'

/**
 * A page whose only job is to make the machinery watchable.
 *
 * Two things it refuses to do: invent agents (the roster is `/api/kernel`, introspected from
 * the code) and pretend one episode runs agents in parallel (the supervisor takes one step at a
 * time). The parallel view is real parallelism: several episodes, each on its own stream.
 */
export function AgentsRoute({ kernel, kernelError }: { kernel: KernelMap | null; kernelError: string | null }) {
  const [paced, setPaced] = useState(true)
  const { lanes, start, clear, showAll } = useLanes(paced ? PACE_MS : 0)
  const [mode, setMode] = useState<PlayerMode>('watch')
  const [pack, setPack] = useState<Pack>('gap')

  const running = lanes.some((l) => l.status === 'live' || l.status === 'starting')

  const one = (scenario: string, which: Pack) => {
    start([{
      key: `${which}-${scenario}`,
      label: `${which === 'gap' ? 'Gap' : 'Watch Along'} · ${scenario}`,
      request: { pack: which, scenario, mode, auto_answer: true },
    }])
  }

  const race = () => {
    const specs: LaneSpec[] = ['S1', 'S2', 'S3'].map((id) => ({
      key: `gap-${id}`,
      label: `Situation ${id}`,
      request: { pack: 'gap' as Pack, scenario: id, mode, auto_answer: true },
    }))
    start(specs)
  }

  return (
    <div className="page container">
      <header className="page-head">
        <Pill tone="warn">Inside the machine</Pill>
        <h1>Watch the agents work</h1>
        <p className="page-lead">
          One supervisor decides each next step and hands it to a specialist. Every specialist can
          only touch the tools and the data its role was granted — so the picture below is also the
          permission list. Nothing here is a video: it is the live trace of a real run.
        </p>
      </header>

      <section className="panel">
        <div className="run-row">
          <div className="toggle" role="group" aria-label="pack">
            {(['gap', 'creator'] as Pack[]).map((p) => (
              <button key={p} type="button" className={`toggle-btn${pack === p ? ' on' : ''}`} onClick={() => setPack(p)}>
                {p === 'gap' ? 'Gap Receipt' : 'Watch Along'}
              </button>
            ))}
          </div>
          <div className="toggle" role="group" aria-label="data mode">
            {(['watch', 'quiet'] as PlayerMode[]).map((m) => (
              <button key={m} type="button" className={`toggle-btn${mode === m ? ' on' : ''}`} onClick={() => setMode(m)}>
                {m === 'watch' ? 'watching (may use a clip)' : 'quiet (text only)'}
              </button>
            ))}
          </div>
        </div>

        <div className="run-row">
          <button type="button" className="btn primary" disabled={running} onClick={() => one(pack === 'gap' ? 'S1' : 'u01', pack)}>
            Run one episode
          </button>
          <button type="button" className="btn" disabled={running} onClick={race}>
            Race all three situations
          </button>
          {lanes.length ? (
            <>
              <button type="button" className="btn ghost" onClick={showAll}>Skip the pacing</button>
              <button type="button" className="btn ghost" onClick={clear}>Clear</button>
            </>
          ) : null}
          <span className="run-status">
            {running ? 'running…' : lanes.length ? `${lanes.length} lane(s) finished or idle` : 'not started'}
          </span>
        </div>

        <div className="run-row">
          <div className="toggle" role="group" aria-label="pacing">
            <button type="button" className={`toggle-btn${paced ? ' on' : ''}`} onClick={() => setPaced(true)}>
              read at human pace
            </button>
            <button type="button" className={`toggle-btn${!paced ? ' on' : ''}`} onClick={() => setPaced(false)}>
              instant
            </button>
          </div>
          <span className="tiny dim">
            A recorded episode finishes in milliseconds. Pacing reveals its frames one at a time so
            you can read them — the frames, their order and their content are the server's.
          </span>
        </div>

        <p className="tiny dim">
          The fan answers in these lanes come from the recorded fixture (<code>auto_answer</code>),
          so three episodes can run side by side without waiting on a human. The page you answer
          yourself is the Gap Receipt page.
        </p>
      </section>

      {kernelError ? <p className="run-status bad">roster unavailable: {kernelError}</p> : null}

      {lanes.length ? (
        <AgentRace lanes={lanes} kernel={kernel} />
      ) : (
        <section className="panel">
          <div className="roster">
            {(kernel?.packs[pack]?.agents || []).map((a) => (
              <article key={a.role} className="roster-card">
                <b>{a.role}</b>
                <p className="tiny dim">{a.mission}</p>
                <div className="chips">
                  {a.capabilities.map((c) => <span key={c} className="cap-chip">{c}</span>)}
                </div>
                <p className="tiny">
                  tools: {a.tools.length ? a.tools.join(', ') : 'none — it reasons, code verifies'}
                </p>
              </article>
            ))}
          </div>
          <p className="tiny dim">
            Press a run button above and this wall of cards becomes a live picture: the supervisor
            lights up, the specialist it picks starts working, its tool calls stream into the log,
            and a card only exists if the commit gate let it through.
          </p>
        </section>
      )}

      <section className="panel">
        <h2 className="panel-title">What the code will not let them do</h2>
        <ul className="walls">
          {(kernel?.walls || []).map((w) => (
            <li key={w.id}>
              <b>{w.id.replace(/_/g, ' ')}</b>
              <span>{w.claim}</span>
              <code>{w.where}</code>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
