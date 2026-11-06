import { useCallback, useEffect, useRef, useState } from 'react'
import { errMessage, getHealth, getManifest } from '../lib/api'
import { readLast, useSession } from '../lib/useSession'
import type { Health, Manifest, PlayerMode } from '../lib/types'
import { routeOf, useRoute } from '../lib/router'
import { useFacts } from '../lib/useFacts'
import { useKernel } from '../lib/useKernel'
import { GapRoute } from './GapRoute'
import { CreatorRoute } from './CreatorRoute'
import { HomeRoute } from './Home'
import { AgentsRoute } from './AgentsRoute'

export function App() {
  const { route, go } = useRoute()
  const [health, setHealth] = useState<Health | null>(null)
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [mode, setMode] = useState<PlayerMode>('watch')
  const s = useSession()
  const { facts, error: factsError } = useFacts()
  const { kernel, error: kernelError } = useKernel()

  const refresh = useCallback(async () => {
    try {
      setHealth(await getHealth())
      setApiError(null)
    } catch (e) {
      setApiError(errMessage(e))
    }
    try {
      setManifest(await getManifest())
    } catch {
      /* health already reported the trouble */
    }
  }, [])

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => void refresh(), 30000)
    return () => window.clearInterval(id)
  }, [refresh])

  // A reload mid-run re-attaches to the same session instead of silently starting over.
  const resumed = useRef(false)
  const { resume } = s
  useEffect(() => {
    if (resumed.current) return
    resumed.current = true
    const last = readLast()
    if (!last) return
    const here = routeOf(window.location.pathname)
    if (here !== 'home' && here !== last.pack) return
    void resume(last.sid)
  }, [resume])

  /** One click from the home page: go to the page and start the run there. */
  const launch = useCallback(
    (pack: 'gap' | 'creator', scenario: string) => {
      go(pack)
      s.start({ pack, scenario, mode })
    },
    [go, mode, s],
  )

  const props = {
    manifest,
    view: s.view,
    stream: s.stream,
    mode,
    setMode,
    start: s.start,
    reset: s.reset,
    answer: s.answer,
    interrupt: s.interrupt,
    tapClip: s.tapClip,
    derived: s.derived,
    facts,
    kernel,
  }

  const navItem = (to: 'home' | 'gap' | 'creator' | 'agents', label: string, sub?: string) => (
    <a
      href={to === 'home' ? '/' : `/${to}`}
      className={`nav-item${route === to ? ' active' : ''}`}
      onClick={(e) => {
        e.preventDefault()
        go(to)
      }}
    >
      <span>{label}</span>
      {sub ? <small>{sub}</small> : null}
    </a>
  )

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <button type="button" className="brand" onClick={() => go('home')} aria-label="home">
            <span className="brand-mark">D11</span>
            <span className="brand-text">
              <b>Fan Companion</b>
              <small>live-match AI · two artifacts</small>
            </span>
          </button>

          <nav className="nav">
            {navItem('home', 'Home', 'why this exists')}
            {navItem('gap', 'Gap Receipt', 'what did I miss?')}
            {navItem('creator', 'Watch Along', 'is the creator right?')}
            {navItem('agents', 'Agent room', 'watch the agents work')}
          </nav>

          <div className="topbar-status">
            <span className={`health-dot ${health?.ok ? 'ok' : apiError ? 'bad' : ''}`} />
            <span className="tiny">
              {apiError ? 'api unreachable' : health ? `run mode ${health.run_mode}` : 'connecting…'}
            </span>
          </div>
        </div>
      </header>

      {route === 'home' ? (
        <HomeRoute
          facts={facts}
          factsError={factsError}
          manifest={manifest ? { run_mode: manifest.run_mode, backend: manifest.backend, cassettes: manifest.cassettes } : null}
          view={s.view}
          mode={mode}
          onMode={setMode}
          go={go}
          launch={launch}
          scenarios={{ gap: manifest?.packs.find((p) => p.id === 'gap')?.scenarios || [], creator: manifest?.packs.find((p) => p.id === 'creator')?.scenarios || [] }}
        />
      ) : null}
      {route === 'gap' ? <GapRoute {...props} /> : null}
      {route === 'creator' ? <CreatorRoute {...props} /> : null}
      {route === 'agents' ? <AgentsRoute kernel={kernel} kernelError={kernelError} /> : null}

      <footer className="sitefoot">
        <span>Dream11 AI internship prototype · One agent kernel, two fan artifacts</span>
        <span>
          {facts ? `${facts.run.cassettes} recorded model turns · ${facts.state.balls_in_fixture} fixture balls · fictional teams` : ''}
        </span>
      </footer>
    </div>
  )
}
