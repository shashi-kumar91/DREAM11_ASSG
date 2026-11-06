import type { Manifest, PlayerMode, Scenario } from '../lib/types'
import type { KernelMap } from '../lib/kernel'
import type { SessionView, StreamState } from '../lib/useSession'
import type { InterruptKind } from '../lib/api'
import type { StartRequest } from '../lib/api'

/** Everything a pack route needs: the one session, plus the shell's manifest and mode. */
export interface PackProps {
  manifest: Manifest | null
  view: SessionView
  stream: StreamState
  mode: PlayerMode
  setMode: (m: PlayerMode) => void
  start: (req: StartRequest) => void
  reset: () => void
  answer: (payload: { text?: string; optionIndex?: number }) => void
  interrupt: (kind: InterruptKind, opts?: { eventId?: string; t?: number }) => void
  tapClip: (eventId: string) => void
  derived: { loadedKb: number; wouldKb: number }
  /** Introspected roster; the compact agent room on a pack page draws from it. */
  kernel: KernelMap | null
}

export function scenariosFor(props: PackProps, pack: string): Scenario[] {
  const p = props.manifest?.packs?.find((x) => x.id === pack)
  return p?.scenarios || []
}

export function subtitleFor(props: PackProps, pack: string): string {
  return props.manifest?.packs?.find((x) => x.id === pack)?.subtitle || ''
}

/** The mode as the server echoed it, not as the client left it in state. */
export function serverModeOf(view: SessionView): string | null {
  for (let i = view.frames.length - 1; i >= 0; i--) {
    const f = view.frames[i]
    if (f.type === 'session' && typeof f.data.mode === 'string') return f.data.mode
    if (f.type === 'episode_start' && typeof f.data.player_mode === 'string') return f.data.player_mode
  }
  return null
}
