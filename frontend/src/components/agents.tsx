import { useEffect, useMemo, useRef } from 'react'
import type { Frame } from '../lib/types'
import type { KernelMap } from '../lib/kernel'
import type { Lane } from '../lib/useLanes'
import { roomState, roomSummary, type AgentLive, type NodeState } from '../lib/room'

/**
 * The agent room: a live picture of the kernel working.
 *
 * It renders only what the server published. A card lights up because a `supervisor_decision`
 * dispatched that role, shows a tool because a `tool_call` named it, and prints its output
 * because an `agent_result` carried it. Nothing is scripted, nothing loops for decoration, and
 * when the run is over the room stays exactly as the trace left it.
 */

const ICON: Record<string, string> = {
  reader: '🧾', investigator: '🔍', questioner: '❓', strategist: '🧠', critic: '🛡',
  perceiver: '👂', analyst: '📐', verifier: '⚖️', supervisor: '🎛',
}

const WORD: Record<NodeState, string> = {
  idle: 'idle', active: 'working', tools: 'using a tool', done: 'reported', error: 'errored',
  blocked: 'out of budget',
}

function AgentCard({ a }: { a: AgentLive }) {
  return (
    <article className={`agent-card s-${a.state}`}>
      {a.state === 'active' ? <span className="pkt" key={a.seq} aria-hidden /> : null}
      <header>
        <span className="avatar" aria-hidden>{ICON[a.role] || '🤖'}</span>
        <b>{a.role}</b>
        <span className={`state-chip ${a.state}`}>{WORD[a.state]}</span>
      </header>
      <p className="agent-mission">{a.mission}</p>
      <div className="chips">
        {a.capabilities.map((c) => (
          <span key={c} className="cap-chip">{c}</span>
        ))}
      </div>
      {a.out ? <p className="agent-out">{a.out}</p> : null}
      <footer>
        <span className={`state-dot ${a.state}`} />
        <span>{a.dispatches ? `${a.dispatches}× dispatched` : 'not dispatched'}</span>
        <span className="dim">· {a.tools.length} tool{a.tools.length === 1 ? '' : 's'} allowed</span>
      </footer>
    </article>
  )
}

export function AgentRoom({
  frames,
  pack,
  kernel,
  title,
  note,
  compact = false,
  pacing = false,
}: {
  frames: Frame[]
  pack: 'gap' | 'creator'
  kernel: KernelMap | null
  title?: string
  note?: string
  compact?: boolean
  /** The lane has finished but the room is still revealing its frames one at a time. */
  pacing?: boolean
}) {
  const roster = kernel?.packs[pack]?.agents || []
  const room = useMemo(() => roomState(frames, roster, pack), [frames, roster, pack])
  const live = frames.length > 0 && !room.terminal
  const logRef = useRef<HTMLOListElement | null>(null)

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [room.log.length])

  return (
    <div className={`room${compact ? ' compact' : ''}${live ? ' live' : ''}`}>
      <div className="room-head">
        {compact ? null : (
          <div>
            <h3>{title || 'The agent room'}</h3>
            {note ? <p className="tiny dim">{note}</p> : null}
          </div>
        )}
        <div className="room-meta">
          <span className={`room-pill ${live ? 'on' : ''}`}>
            {pacing ? 'replaying' : live ? 'live' : room.terminal ? 'finished' : 'not running'}
          </span>
          <span className="room-pill">{roomSummary(room)}</span>
          {room.scenario ? <span className="room-pill">{room.scenario}</span> : null}
          {note && compact ? <span className="room-pill">{note}</span> : null}
        </div>
        <div className="room-legend">
          <span><i className="state-dot active" />working</span>
          <span><i className="state-dot tools" />using a tool</span>
          <span><i className="state-dot done" />reported</span>
        </div>
      </div>

      <div className="room-sup">
        <div className="sup-top">
          <span className="avatar big" aria-hidden>🎛</span>
          <div>
            <b>supervisor</b>
            <p className="tiny dim">
              {kernel?.supervisor.mission || 'chooses the next single step'}
            </p>
          </div>
        </div>
        <div className="sup-now">
          <span className={`state-dot ${room.supState}`} />
          {room.lastAction
            ? <span>last call: <b>{room.lastAction}</b></span>
            : <span className="dim">waiting for the first decision</span>}
          {room.lastReason ? <span className="sup-reason">“{room.lastReason}”</span> : null}
        </div>
        {room.supTools.length ? (
          <div className="chips">
            {room.supTools.map((t, i) => (
              <span key={`${t}-${i}`} className="tool-chip">{t}</span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="room-bus" aria-hidden>
        <span className="bus-line" />
        {live ? <span className="bus-pulse" /> : null}
      </div>

      <div className="room-floor">
        {room.agents.length ? room.agents.map((a) => <AgentCard key={a.role} a={a} />) : (
          <p className="tiny dim">Loading the roster from /api/kernel…</p>
        )}
      </div>

      {(room.fanAsked || room.commits || room.rejections || room.guardrails.length || room.retries) ? (
        <div className="room-edges">
          {room.fanAsked ? (
            <div className="edge-card fan">
              <b>you (the fan)</b>
              <p className="edge-q">{room.fanAsked}</p>
              <p className="tiny dim">{room.fanAnswer ? `you said: ${room.fanAnswer}` : 'waiting for your answer'}</p>
              {room.fanAskedBy ? <p className="edge-who">{room.fanAskedBy}</p> : null}
            </div>
          ) : null}
          <div className="edge-card gate">
            <b>commit gate</b>
            <p className="tiny">
              {room.commits} committed · {room.rejections} rejected
              {room.guardrails.length ? ` · ${room.guardrails.length} guardrail${room.guardrails.length === 1 ? '' : 's'} fired` : ''}
            </p>
            {room.guardrails.map((g, i) => (
              <p key={i} className="edge-warn">{g.rule || 'rule'} — {g.why}</p>
            ))}
          </div>
        </div>
      ) : null}

      <ol className="room-log" ref={logRef} aria-label="live kernel log">
        {room.log.length ? room.log.map((l, i) => (
          <li key={`${l.seq}-${i}`} className={l.tone}>
            <span className="log-kind">{l.kind}</span>
            <span className="log-text">{l.text}</span>
          </li>
        )) : (
          <li className="dim"><span className="log-kind">waiting</span>
            <span className="log-text">frames appear here the moment the kernel publishes them</span>
          </li>
        )}
      </ol>
    </div>
  )
}

export function AgentRace({ lanes, kernel }: { lanes: Lane[]; kernel: KernelMap | null }) {
  return (
    <div className="race">
      {lanes.map((lane) => (
        <section key={lane.key} className={`lane s-${lane.status}`}>
          <header className="lane-head">
            <b>{lane.label}</b>
            <span className={`room-pill ${lane.status === 'live' ? 'on' : ''}`}>{lane.status}</span>
            <span className="tiny dim">{lane.cards} card{lane.cards === 1 ? '' : 's'}</span>
            {lane.error ? <span className="lane-err">{lane.error}</span> : null}
          </header>
          <AgentRoom
            frames={lane.frames.slice(0, lane.shown || lane.frames.length)}
            pack={(lane.key.startsWith('creator') ? 'creator' : 'gap')}
            kernel={kernel}
            compact
            title={lane.label}
            note={lane.sid ? `session ${lane.sid.slice(0, 12)}…` : 'opening a session'}
            pacing={lane.shown < lane.frames.length}
          />
        </section>
      ))}
    </div>
  )
}
