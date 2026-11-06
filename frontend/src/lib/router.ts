import { useCallback, useEffect, useState } from 'react'

/**
 * A 30-line path router instead of a dependency.
 *
 * FastAPI serves frontend/dist for any unknown path (backend/app/server.py `spa`), so `/gap`
 * and `/creator` resolve to index.html on a hard reload. Pushing state keeps the browser
 * address bar truthful without a server round trip.
 */
export type RouteId = 'gap' | 'creator' | 'agents' | 'home'

export function routeOf(pathname: string): RouteId {
  const p = '/' + pathname.replace(/^\/+/, '').split('/')[0]
  if (p === '/gap') return 'gap'
  if (p === '/creator') return 'creator'
  if (p === '/agents') return 'agents'
  return 'home'
}

export function useRoute(): { route: RouteId; go: (to: RouteId) => void } {
  const [route, setRoute] = useState<RouteId>(() => routeOf(window.location.pathname))

  useEffect(() => {
    const onPop = () => setRoute(routeOf(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const go = useCallback((to: RouteId) => {
    const path = to === 'home' ? '/' : `/${to}`
    if (window.location.pathname !== path) window.history.pushState({}, '', path)
    setRoute(to)
  }, [])

  return { route, go }
}
