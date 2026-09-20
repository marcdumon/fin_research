import { useState } from 'react'
import { checkSchemas, type SchemaCheck } from '../api'

// Asks each model whether it will accept the structure the system asks it for. A rejection otherwise
// surfaces as an error partway through a run, after the cheap stages have been paid for.
export function ModelCheck() {
  const [results, setResults] = useState<SchemaCheck[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setResults(await checkSchemas())
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'the check could not run')
    } finally {
      setBusy(false)
    }
  }

  const rejected = results?.filter((r) => !r.accepted) ?? []

  return (
    <div className="px-4 py-3 border-b border-rule">
      <button
        onClick={run}
        disabled={busy}
        className="w-full px-3 py-1.5 text-[13px] border border-rule hover:bg-surface disabled:opacity-40"
      >
        {busy ? 'Checking models' : 'Check models'}
      </button>

      {error ? <p className="mt-2 text-[12.5px] text-expensive">{error}</p> : null}

      {results ? (
        <div className="mt-2 space-y-1">
          <p className={`text-[12.5px] ${rejected.length ? 'text-expensive' : 'text-muted'}`}>
            {rejected.length
              ? `${rejected.length} of ${results.length} rejected`
              : `all ${results.length} accepted`}
          </p>
          {rejected.map((result) => (
            <p key={result.schema} className="text-[12px] text-expensive">
              {result.schema} on {result.model}: {result.detail}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  )
}
