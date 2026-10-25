import { useState } from 'react'
import { checkSchemas, type SchemaCheck } from '../api'
import { SpendConfirm } from './shell/SpendConfirm'

// Asks each model whether it will accept the structure the system asks it for. A rejection otherwise
// surfaces as an error partway through a run, after the cheap stages have been paid for.
export function ModelCheck() {
  const [results, setResults] = useState<SchemaCheck[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

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
        onClick={() => setConfirming(true)}
        disabled={busy}
        className="w-full px-3 py-1.5 text-tiny border border-rule hover:bg-panel disabled:opacity-40"
      >
        {busy ? 'Checking models' : 'Check models'}
      </button>

      {confirming && (
        <SpendConfirm
          title="Check the models"
          detail={'Sends each artifact structure to the model that will receive it, to find a rejection before a '
            + 'run pays for the stages ahead of it. Usually a few cents.'}
          budget={0.5}
          fixed
          allowGating={false}
          confirmLabel="Check"
          onConfirm={() => { setConfirming(false); void run() }}
          onCancel={() => setConfirming(false)}
        />
      )}

      {error ? <p className="mt-2 text-tiny text-danger">{error}</p> : null}

      {results ? (
        <div className="mt-2 space-y-1">
          <p className={`text-tiny ${rejected.length ? 'text-danger' : 'text-muted'}`}>
            {rejected.length
              ? `${rejected.length} of ${results.length} rejected`
              : `all ${results.length} accepted`}
          </p>
          {rejected.map((result) => (
            <p key={result.schema} className="text-micro text-danger">
              {result.schema} on {result.model}: {result.detail}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  )
}
