import { useEffect, useState } from 'react'
import { verifyStatements, type VerifyResult } from '../api'

// Whether a company's own reported statements reconcile. This belongs beside a run because it says whether
// the figures the agents reasoned about can be trusted at all, which is not visible from the note itself.
export function Verify({ ticker }: { ticker: string }) {
  const [result, setResult] = useState<VerifyResult | null>(null)
  const [state, setState] = useState<'idle' | 'checking' | 'failed'>('idle')

  useEffect(() => {
    setResult(null)
    setState('idle')
  }, [ticker])

  async function check() {
    setState('checking')
    try {
      setResult(await verifyStatements(ticker))
      setState('idle')
    } catch {
      setState('failed')
    }
  }

  if (!result) {
    return (
      <div className="flex items-baseline gap-3">
        <button onClick={check} disabled={state === 'checking'}
                className="px-3 py-1 text-[13px] border border-rule hover:bg-surface disabled:opacity-40">
          {state === 'checking' ? 'Checking the accounts' : 'Do the accounts add up?'}
        </button>
        {state === 'failed' ? <span className="text-[13px] text-expensive">could not check</span> : null}
      </div>
    )
  }

  return (
    <div>
      <p className={`text-[14.5px] ${result.trustworthy ? '' : 'text-expensive font-medium'}`}>
        {result.trustworthy
          ? `The statements reconcile: ${result.passed} checks agreed.`
          : result.failed
            ? `${result.failed} check${result.failed === 1 ? '' : 's'} did not balance. Treat these numbers as unconfirmed.`
            : 'Nothing could be checked, so these numbers are unconfirmed.'}
      </p>
      {result.skipped ? (
        <p className="mt-0.5 text-[13px] text-muted">
          {result.skipped} could not run because the company does not report the inputs.
        </p>
      ) : null}
      <ul className="mt-2 space-y-0.5">
        {result.results.filter((r) => r.status === 'fail').map((r, i) => (
          <li key={i} className="text-[13px] text-expensive">
            {r.period_end} {r.name.replace(/_/g, ' ')}: {r.detail}
          </li>
        ))}
      </ul>
    </div>
  )
}
