import { useState } from 'react'
import { startRun } from '../api'

// Starting a run from here spawns a separate process, exactly as the command line does. The dashboard then
// follows it through the same stream, so there is one path for a run to be watched however it was begun.
export function NewRun({ onStarted }: { onStarted: (runId: string) => void }) {
  const [ticker, setTicker] = useState('')
  const [budget, setBudget] = useState('2.00')
  const [gated, setGated] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!ticker.trim()) return
    setBusy(true)
    setError(null)
    try {
      const runId = await startRun(ticker.trim(), Number(budget) || 2, gated ? ['expensive'] : [])
      if (runId) onStarted(runId)
      setTicker('')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'could not start the run')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="px-4 py-3 border-b border-rule space-y-2">
      <div className="flex gap-2">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Ticker"
          aria-label="Ticker to research"
          className="min-w-0 flex-1 px-2 py-1 text-[14px] bg-surface border border-rule"
        />
        <input
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          inputMode="decimal"
          aria-label="Budget in dollars"
          className="w-16 px-2 py-1 text-[14px] bg-surface border border-rule"
        />
      </div>
      <label className="flex items-center gap-2 text-[12.5px] text-muted">
        <input type="checkbox" checked={gated} onChange={(e) => setGated(e.target.checked)} />
        Ask before each expensive step
      </label>
      <button
        type="submit"
        disabled={busy || !ticker.trim()}
        className="w-full px-3 py-1.5 text-[13px] bg-ink text-surface disabled:opacity-40"
      >
        {busy ? 'Starting' : 'Research this company'}
      </button>
      {error ? <p className="text-[12.5px] text-expensive">{error}</p> : null}
    </form>
  )
}
