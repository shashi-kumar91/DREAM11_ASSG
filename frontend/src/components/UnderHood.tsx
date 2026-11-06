import { useState } from 'react'
import type { SessionView, StreamState } from '../lib/useSession'
import { JuryPane } from './jury'

/**
 * The engineer drawer. Everything technical lives here instead of on the fan's screen:
 * the journal, the budget walls, the byte graph and the provenance badges.
 * It is the same `JuryPane` the first build used - just moved behind a disclosure, because a
 * fan opening this page to find out what they missed does not want a trace log first.
 */
export function UnderHood({
  view,
  stream,
  runMode,
}: {
  view: SessionView
  stream: StreamState
  runMode: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <section className="underhood">
      <button type="button" className="underhood-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{open ? '▾' : '▸'} Show the working</span>
        <small>what happened, step by step, and what it cost — for anyone who wants to check</small>
      </button>
      {open ? (
        <div className="underhood-body">
          <JuryPane view={view} stream={stream} open onToggle={() => setOpen(false)} runMode={runMode} />
        </div>
      ) : null}
    </section>
  )
}
