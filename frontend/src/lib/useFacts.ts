import { useEffect, useRef, useState } from 'react'
import { getFacts, type Facts } from '../lib/facts'

/** Facts are fetched once per page load and shared; opening the page starts no agent run. */
export function useFacts(): { facts: Facts | null; error: string | null; reload: () => void } {
  const [facts, setFacts] = useState<Facts | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)

  const load = () => {
    getFacts()
      .then((f) => {
        if (alive.current) {
          setFacts(f)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        if (alive.current) setError(e instanceof Error ? e.message : String(e))
      })
  }

  useEffect(() => {
    alive.current = true
    load()
    return () => {
      alive.current = false
    }
  }, [])

  return { facts, error, reload: load }
}
