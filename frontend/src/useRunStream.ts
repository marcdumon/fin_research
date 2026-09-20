import { useEffect, useRef, useState } from 'react'
import { getCalls, type Call, type Snapshot, type Approval } from './api'

// Subscribes to one run's event stream. The server sends a snapshot first, so a browser arriving mid-run
// sees the whole picture rather than only what happens next -- which is what makes a reconnect cheap.
export function useRunStream(runId: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [calls, setCalls] = useState<Call[]>([])
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [live, setLive] = useState(false)
  const landed = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!runId) return
    setSnapshot(null)
    setCalls([])
    setApprovals([])
    landed.current = new Set()

    // A finished run has no more events, so its calls are fetched once instead of streamed.
    getCalls(runId).then(setCalls).catch(() => undefined)

    const source = new EventSource(`/api/runs/${runId}/stream`)
    source.addEventListener('open', () => setLive(true))

    source.addEventListener('snapshot', (event) => {
      setSnapshot(JSON.parse((event as MessageEvent).data))
      setLive(true)
    })

    source.addEventListener('call', (event) => {
      const call = JSON.parse((event as MessageEvent).data) as Call
      landed.current.add(call.call_id)
      setCalls((previous) => [call, ...previous.filter((c) => c.call_id !== call.call_id)])
    })

    source.addEventListener('task', (event) => {
      const task = JSON.parse((event as MessageEvent).data)
      setSnapshot((previous) => {
        if (!previous) return previous
        const others = previous.tasks.filter((t) => t.task_id !== task.task_id)
        return { ...previous, tasks: [...others, task].sort((a, b) => a.stage - b.stage) }
      })
    })

    source.addEventListener('run', (event) => {
      const update = JSON.parse((event as MessageEvent).data)
      setSnapshot((previous) => (previous ? { ...previous, ...update } : previous))
    })

    source.addEventListener('approval', (event) => {
      setApprovals((previous) => [...previous, JSON.parse((event as MessageEvent).data)])
    })

    source.addEventListener('done', (event) => {
      setSnapshot(JSON.parse((event as MessageEvent).data))
      setLive(false)
      source.close()
    })

    source.onerror = () => setLive(false)
    return () => source.close()
  }, [runId])

  return { snapshot, calls, approvals, live, justLanded: landed.current }
}
