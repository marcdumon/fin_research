import { useEffect, useState } from 'react'
import { verifyStatements, type VerifyResult } from '../api'

// What each identity means in business terms, and what its absence costs you. An earlier version reported
// "8 checks agreed, 4 could not run", which is true and useless: it names the machinery rather than the
// conclusion, and leaves a reader with nothing to decide.
const MEANING: Record<string, { confirmed: string; absent: string; why: string }> = {
  balance_sheet: {
    confirmed: 'The balance sheet balances',
    absent: 'The balance sheet could not be checked',
    why: 'assets should equal what is owed plus what shareholders own',
  },
  cash_flow: {
    confirmed: 'The cash flow statement adds up',
    absent: 'The cash flow statement could not be checked',
    why: 'operating, investing and financing flows should account for the change in cash',
  },
  gross_profit: {
    confirmed: 'Gross profit is consistent with revenue and costs',
    absent: 'Gross profit could not be checked',
    why: 'this company does not report a cost of revenue, which is normal for banks, insurers and some service businesses',
  },
}

type Verdict = 'confirmed' | 'broken' | 'absent'

function verdicts(result: VerifyResult): Record<string, Verdict> {
  const byCheck: Record<string, Verdict> = {}
  for (const row of result.results) {
    const current = byCheck[row.name]
    if (row.status === 'fail') byCheck[row.name] = 'broken'
    else if (row.status === 'pass' && current !== 'broken') byCheck[row.name] = 'confirmed'
    else if (!current) byCheck[row.name] = 'absent'
  }
  return byCheck
}

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
          {state === 'checking' ? `Checking ${ticker}` : 'Do the accounts add up?'}
        </button>
        {state === 'failed' ? <span className="text-[13px] text-expensive">could not check</span> : null}
      </div>
    )
  }

  const byCheck = verdicts(result)
  const broken = result.results.filter((r) => r.status === 'fail')
  const confirmed = Object.entries(byCheck).filter(([, v]) => v === 'confirmed')
  const absent = Object.entries(byCheck).filter(([, v]) => v === 'absent')

  return (
    <div className="max-w-2xl">
      {broken.length ? (
        <>
          <p className="text-[15px] font-medium text-expensive">
            {ticker}&rsquo;s own statements do not add up. Treat every figure in this note as unconfirmed.
          </p>
          <p className="mt-1 text-[14px]">
            A filer whose statements stop reconciling has usually restated something, which is among the most
            informative things a company does and among the least announced. Worth finding out what changed
            before acting on anything here.
          </p>
          <ul className="mt-2 space-y-1">
            {broken.map((row, i) => (
              <li key={i} className="text-[13px] text-expensive">
                {row.period_end}: {row.detail}
              </li>
            ))}
          </ul>
        </>
      ) : confirmed.length ? (
        <>
          <p className="text-[15px]">
            The figures behind this note come from statements that add up. Nothing to act on.
          </p>
          <ul className="mt-2 space-y-0.5">
            {confirmed.map(([name]) => (
              <li key={name} className="text-[13.5px]">
                {MEANING[name]?.confirmed ?? name.replace(/_/g, ' ')}
                <span className="text-muted"> — {MEANING[name]?.why ?? ''}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-[15px] font-medium text-expensive">
          Nothing could be checked, so none of these figures is confirmed. Read the note as unverified.
        </p>
      )}

      {absent.length ? (
        <ul className="mt-3 space-y-0.5">
          {absent.map(([name]) => (
            <li key={name} className="text-[13px] text-muted">
              {MEANING[name]?.absent ?? name.replace(/_/g, ' ')} — {MEANING[name]?.why ?? 'the inputs are not reported'}.
            </li>
          ))}
        </ul>
      ) : null}

      <p className="mt-3 text-[12.5px] text-muted">
        Checked against {ticker}&rsquo;s own filed statements, not against an outside source. This says the
        numbers are internally consistent; it does not say the business is sound.
      </p>
    </div>
  )
}
