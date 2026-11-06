import { useEffect, useRef, useState } from 'react'
import { getKernel, type KernelMap } from './kernel'

/** Fetched once per page load. Read-only: it starts no run and spends no token. */
export function useKernel(): { kernel: KernelMap | null; error: string | null } {
  const [kernel, setKernel] = useState<KernelMap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    getKernel()
      .then((k) => {
        if (alive.current) setKernel(k)
      })
      .catch((e: unknown) => {
        if (alive.current) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      alive.current = false
    }
  }, [])

  return { kernel, error }
}
