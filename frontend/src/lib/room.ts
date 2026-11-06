import type { Frame } from './types'
import type { KernelAgent } from './kernel'
import { humanize } from './human'

/**
 * The agent room is a *view* of frames the server actually sent.
 *
 * Every field below is written from a frame the kernel published: a dispatch only exists if a
 * `supervisor_decision` carried `action=dispatch`, a tool chip only exists if a `tool_call`
 * named that tool, and a card only lights up because a `commit` frame arrived. There is no
 * timer, no scripted choreography and no invented parallelism - the honest claim this visual
 * makes is "this is the order it happened in".
 */

export type NodeState = 'idle' | 'active' | 'tools' | 'done' | 'error' | 'blocked'

export interface AgentLive {
  role: string
  mission: string
  tools: string[]
  capabilities: string[]
  state: NodeState
  dispatches: number
  out: string
  seq: number
}

export interface RoomLogLine {
  seq: number
  kind: string
  text: string
  tone: string
}

export interface RoomState {
  pack: string
  scenario: string
  supState: NodeState
  supOut: string
  lastAction: string
  lastReason: string
  steps: number
  toolsRun: number
  supTools: string[]
  agents: AgentLive[]
  commits: number
  rejections: number
  guardrails: { rule: string; why: string }[]
  fanAsked: string
  fanAskedBy: string
  fanAnswer: string
  terminal: string
  terminalReason: string
  retries: number
  log: RoomLogLine[]
}

/**
 * The one-line summary a card shows. It never serialises an object: a fan reading a card should
 * see "checked the claim against the feed", not `{"kind":"conflict","claimed":"142/2"}`.
 */
function brief(v: unknown, limit = 180): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v.slice(0, limit)
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    for (const k of ['summary', 'detail', 'note', 'why', 'reason', 'text', 'kind', 'verdict']) {
      const val = o[k]
      if (typeof val === 'string' && val.trim()) return val.slice(0, limit)
    }
    return Object.keys(o).slice(0, 4).join(', ')
  }
  return ''
}

/** One readable line for the room's log, built from the server's own fields. */
function toneOf(kind: string): string {
  if (kind === 'agent_error' || kind === 'supervisor_error' || kind === 'commit_rejected') return 'bad'
  if (kind === 'guardrail' || kind === 'abstain' || kind === 'asr_abstain' || kind === 'budget_block') return 'warn'
  if (kind === 'commit' || kind === 'card' || kind === 'prediction_settled') return 'good'
  return ''
}

export function roomState(frames: Frame[], roster: KernelAgent[], pack: string): RoomState {
  const byRole = new Map<string, AgentLive>()
  for (const a of roster) {
    byRole.set(a.role, {
      role: a.role,
      mission: a.mission,
      tools: a.tools,
      capabilities: a.capabilities,
      state: 'idle',
      dispatches: 0,
      out: '',
      seq: 0,
    })
  }

  const room: RoomState = {
    pack,
    scenario: '',
    supState: 'idle',
    supOut: '',
    lastAction: '',
    lastReason: '',
    steps: 0,
    toolsRun: 0,
    supTools: [],
    agents: [],
    commits: 0,
    rejections: 0,
    guardrails: [],
    fanAsked: '',
    fanAskedBy: '',
    fanAnswer: '',
    terminal: '',
    terminalReason: '',
    retries: 0,
    log: [],
  }

  for (const f of frames) {
    const d = f.data || {}
    switch (f.type) {
      case 'episode_start':
      case 'session': {
        if (typeof d.scenario === 'string' && d.scenario) room.scenario = d.scenario
        if (typeof d.scenario_name === 'string' && d.scenario_name) room.scenario = d.scenario_name
        break
      }
      case 'supervisor_decision': {
        const action = String(d.action || '')
        const agent = String(d.agent || '')
        room.steps += 1
        room.lastAction = action
        room.lastReason = String(d.reason || d.thought || '')
        room.supOut = room.lastReason
        room.supState = 'active'
        if (action === 'dispatch' && byRole.has(agent)) {
          const node = byRole.get(agent) as AgentLive
          node.state = 'active'
          node.dispatches += 1
          node.seq = room.steps
          room.supState = 'active'
        }
        break
      }
      case 'tool_call': {
        const tool = String(d.tool || '')
        const caller = String(d.caller || '')
        room.toolsRun += 1
        if (caller && byRole.has(caller)) {
          const node = byRole.get(caller) as AgentLive
          node.state = 'tools'
          node.out = `${tool}${d.ok === false ? ' (refused)' : ''}`
        } else {
          room.supTools = [...room.supTools, tool].slice(-6)
          room.supState = 'tools'
        }
        break
      }
      case 'agent_result': {
        const role = String(d.agent || '')
        if (byRole.has(role)) {
          const node = byRole.get(role) as AgentLive
          node.state = 'done'
          node.out = brief(d.result, 200)
        }
        break
      }
      case 'agent_error': {
        const role = String(d.agent || '')
        if (byRole.has(role)) {
          const node = byRole.get(role) as AgentLive
          node.state = 'error'
          node.out = brief(d.error, 140)
        }
        break
      }
      case 'agent_no_final':
      case 'agent_schema_retry':
      case 'agent_unparseable_retry':
      case 'supervisor_unparseable_retry': {
        room.retries += 1
        break
      }
      case 'commit': {
        if (d.ok === false) {
          room.rejections += 1
        } else {
          room.commits += 1
          room.supState = 'done'
        }
        break
      }
      case 'budget_block': {
        // A budget wall is not an error in the model: it is the kernel enforcing a limit. The
        // agent it hit is marked blocked so nobody reads the silence as the agent's choice.
        const where = String(d.where || '')
        const role = where.startsWith('agent:') ? where.slice(6) : ''
        if (role && byRole.has(role)) {
          const node = byRole.get(role) as AgentLive
          node.state = 'blocked'
          node.out = node.out || String(d.detail || 'hit the call budget')
        }
        break
      }
      case 'commit_rejected': {
        room.rejections += 1
        break
      }
      case 'guardrail': {
        room.guardrails = [...room.guardrails, {
          rule: String(d.rule || ''),
          why: String(d.why || d.detail || ''),
        }].slice(-4)
        break
      }
      case 'ask_user': {
        const q = (d.question || {}) as Record<string, unknown>
        room.fanAsked = String(q.text || d.question || '')
        room.fanAskedBy = String(d.note || q.reason || '')
        break
      }
      case 'answer':
      case 'answer_accepted':
      case 'auto_answer': {
        room.fanAnswer = String(d.answer || '')
        break
      }
      case 'episode_end': {
        room.terminal = String(d.terminal || '')
        room.terminalReason = String(d.reason || '')
        break
      }
      default:
        break
    }

    // The log keeps the frames a human can read, newest last, capped for the panel.
    if (['supervisor_decision', 'tool_call', 'agent_result', 'agent_error', 'commit', 'guardrail',
      'ask_user', 'answer', 'asr_abstain', 'abstain', 'triage', 'episode_end', 'budget_block',
      'prediction_settled', 'kernel_verifier'].includes(f.type)) {
      const line = humanize(f)
      room.log = [...room.log, {
        seq: Number(d.seq || 0) || room.log.length + 1,
        kind: line.what,
        text: line.detail,
        tone: line.tone || toneOf(f.type),
      }].slice(-40)
    }
  }

  room.agents = roster.map((a) => byRole.get(a.role) as AgentLive)
  return room
}

/** How far through its own roster the supervisor has got, for the header pill. */
export function roomSummary(room: RoomState): string {
  if (room.terminal) return room.terminal
  if (!room.steps) return 'waiting for the first decision'
  return `step ${room.steps} of the supervisor's own choosing`
}
