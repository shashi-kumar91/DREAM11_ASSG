/** The kernel's own org chart, fetched from `/api/kernel` (introspected server-side). */

export interface KernelAgent {
  role: string
  mission: string
  tools: string[]
  capabilities: string[]
  max_inner_calls: number
}

export interface KernelTool {
  name: string
  description: string
  capabilities: string[]
  deterministic: boolean
  read_only: boolean
}

export interface KernelPack {
  agents: KernelAgent[]
  tools: KernelTool[]
  supervisor_capabilities: string[]
  critic_required_before_commit: boolean
}

export interface KernelWall {
  id: string
  where: string
  claim: string
}

export interface KernelMap {
  supervisor: {
    role: string
    mission: string
    contract: string[]
    cannot: string[]
  }
  packs: Record<'gap' | 'creator', KernelPack>
  walls: KernelWall[]
}

export async function getKernel(): Promise<KernelMap> {
  const res = await fetch('/api/kernel')
  if (!res.ok) throw new Error(`${res.status} on /api/kernel`)
  return (await res.json()) as KernelMap
}
