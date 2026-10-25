import { useCallback, useState } from 'react'
import type { Source } from '../../api'

// Which source the evidence panel is showing, and stepping through them in citation order.
export function useEvidence(sources: Source[]) {
  const [shown, setShown] = useState<number | null>(null)
  const open = useCallback((n: number) => setShown(n), [])
  const close = useCallback(() => setShown(null), [])
  const step = useCallback((by: number) => setShown((current) => {
    if (current === null || sources.length === 0) return current
    const index = sources.findIndex((s) => s.n === current)
    return sources[(index + by + sources.length) % sources.length].n
  }), [sources])

  return {
    source: shown === null ? null : sources.find((s) => s.n === shown) ?? null,
    open,
    close,
    step,
    count: sources.length,
  }
}
