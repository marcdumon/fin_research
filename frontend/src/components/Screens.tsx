import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useEffect, useState } from 'react'
import { Link } from 'wouter'
import {
  billions, getTriage, jobStatus, listScreens, percent, runScreen, startFunnel,
  type Screen, type ScreenResult, type TriageVerdict,
} from '../api'
import { SpendConfirm } from './shell/SpendConfirm'

const VERDICT_WORD = { escalate: 'worth research', pass: 'pass', reject: 'reject' } as const
const VERDICT_TONE = { escalate: 'text-constructive', pass: 'text-muted', reject: 'text-down' } as const

// Screening reads no filings and calls no model, so it answers in about a second over a couple of thousand
// companies. The funnel that follows reads each survivor and does cost money, which is why it is a separate,
// explicit action rather than something that happens automatically after a screen.
export function Screens({ onResearch }: { onResearch: (ticker: string) => void }) {
  const [screens, setScreens] = useState<Screen[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [result, setResult] = useState<ScreenResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [funnelStarted, setFunnelStarted] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<number | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const queries = useQueryClient()

  // The funnel records a verdict per company as it reads each one, and nothing used to show them: the
  // result of a paid step was reachable only by opening the database. Polled while the funnel runs, so
  // verdicts appear against their companies as they arrive.
  const funnel = useQuery({
    queryKey: ['job', job],
    queryFn: () => jobStatus(job!),
    enabled: job !== null,
    refetchInterval: (query) => (query.state.data?.running === false ? false : 2_000),
  })
  const reading = job !== null && funnel.data?.running !== false
  const triage = useQuery({
    queryKey: ['triage', selected],
    queryFn: () => getTriage(selected!),
    enabled: selected !== null,
    refetchInterval: reading ? 3_000 : false,
  })
  const verdicts = new Map<string, TriageVerdict>((triage.data?.verdicts ?? []).map((v) => [v.ticker, v]))

  useEffect(() => {
    if (job !== null && funnel.data?.running === false) void queries.invalidateQueries({ queryKey: ['triage'] })
  }, [job, funnel.data?.running, queries])

  // Screening is free and takes about a second, so the first screen runs as soon as the page opens.
  // It used to wait to be clicked while already shown as selected, which left a page of criteria and nothing
  // else, with no sign that anything was expected of the reader.
  useEffect(() => {
    listScreens().then((found) => {
      setScreens(found)
      if (found[0]) void run(found[0].name)
    }).catch(() => setError('no screens are defined'))
  }, [])

  async function run(name: string) {
    setSelected(name)
    setBusy(true)
    setError(null)
    setResult(null)
    setFunnelStarted(false)
    try {
      setResult(await runScreen(name))
    } catch {
      setError('the screen could not run. It needs the market database.')
    } finally {
      setBusy(false)
    }
  }

  const screen = screens.find((s) => s.name === selected)

  return (
    <div className="max-w-[64rem]">
      <h2 className="text-title font-semibold tracking-tight">Screens</h2>
      <p className="mt-1 text-muted text-base">
        Numbers only, as they stood on the day. Nothing here reads a filing, so it is free.
      </p>

      <div className="mt-5 flex flex-wrap gap-2">
        {screens.map((s) => (
          <button
            key={s.name}
            onClick={() => run(s.name)}
            aria-current={s.name === selected ? 'true' : undefined}
            className={`px-3 py-1.5 text-small border ${
              s.name === selected ? 'border-ink bg-panel font-medium' : 'border-rule hover:bg-raised/60'
            }`}
          >
            {s.name.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {screen ? (
        <div className="mt-4 text-small">
          <p>{screen.description}</p>
          <p className="mt-1 text-tiny text-muted">
            {screen.requires.join(' · ')}
            {screen.excludes.length ? ` · excludes ${screen.excludes.join(', ')}` : ''}
          </p>
          {screen.notes ? <p className="mt-1 text-tiny text-muted">{screen.notes}</p> : null}
        </div>
      ) : null}

      {error ? <p className="mt-4 text-small text-danger">{error}</p> : null}
      {busy ? <p className="mt-4 text-muted text-base">Screening.</p> : null}

      {result ? (
        <>
          <div className="mt-6 flex items-baseline justify-between gap-4 flex-wrap border-b border-rule pb-2">
            <p className="text-small">
              {result.considered.toLocaleString()} considered, {result.survivors.length} survive
              <span className="text-faint">
                {' · '}prices are closes as of {result.prices_as_of ?? result.as_of}
              </span>
            </p>
            <button
              onClick={() => setConfirming(true)}
              disabled={funnelStarted || !result.survivors.length}
              className="px-3 py-1 text-tiny rounded-sm bg-accent text-ground disabled:opacity-40"
            >
              {funnelStarted ? 'Reading the survivors' : 'Read the survivors…'}
            </button>
            {confirming && (
              <SpendConfirm
                title={`Read the ${Math.min(result.survivors.length, 20)} best survivors`}
                detail={'A cheap model reads each company\'s business description and says whether it deserves '
                  + 'full research. About a third of a cent per company.'}
                budget={1}
                allowGating={false}
                confirmLabel="Read them"
                onConfirm={async (budget) => {
                  setConfirming(false)
                  const started = await startFunnel(result.screen, budget, 20)
                  setJob(started.pid)
                  setFunnelStarted(true)
                }}
                onCancel={() => setConfirming(false)}
              />
            )}
          </div>

          {reading ? (
            <p className="mt-2 text-tiny text-accent">
              Reading the survivors. Verdicts appear in the table as each company is judged.
            </p>
          ) : funnelStarted && funnel.data?.exit_code ? (
            <p className="mt-2 text-tiny text-danger">The read ended with an error. Its log is in data/runs.</p>
          ) : triage.data?.started_at && verdicts.size > 0 ? (
            <p className="mt-2 text-tiny text-faint">
              Verdicts from the read on {triage.data.started_at.slice(0, 10)}, which cost
              ${triage.data.spend_usd.toFixed(4)}. Click a verdict for the reasons.
            </p>
          ) : null}

          <details className="mt-3">
            <summary className="text-tiny text-muted cursor-pointer">Where the universe went</summary>
            <ul className="mt-1.5 text-tiny text-muted">
              {Object.entries(result.dropped).map(([reason, count]) => (
                <li key={reason}>−{count.toLocaleString()} {reason}</li>
              ))}
            </ul>
          </details>

          {result.considered === 0 && (
            <p className="mt-3 text-small text-danger">
              No company had usable figures on this date, so nothing could be screened. The market database may
              have no prices this recent.
            </p>
          )}

          <table className="mt-4 w-full text-small border-collapse">
            <thead>
              <tr className="text-left text-muted border-b border-rule">
                <th className="font-normal py-1.5 pr-3">Company</th>
                <th className="font-normal py-1.5 pr-3 hidden sm:table-cell">Sector</th>
                <th className="font-normal py-1.5 pr-3 text-right">$bn</th>
                <th className="font-normal py-1.5 pr-3 text-right">Earnings yield</th>
                <th className="font-normal py-1.5 pr-3 text-right hidden sm:table-cell">Cash yield</th>
                <th className="font-normal py-1.5 pr-3 text-right hidden sm:table-cell">Return on equity</th>
                <th className="font-normal py-1.5 pr-3">Read</th>
                <th className="font-normal py-1.5" />
              </tr>
            </thead>
            <tbody>
              {result.survivors.map((s) => {
                const verdict = verdicts.get(s.Ticker)
                return (
                  <Fragment key={s.Ticker}>
                    <tr className="border-b border-rule/60">
                      <td className="py-1.5 pr-3">
                        <Link href={`/c/${s.Ticker}`} className="font-medium text-accent hover:underline">
                          {s.Ticker}
                        </Link>
                        <span className="block text-micro text-muted">{s.name ?? ''}</span>
                      </td>
                      <td className="py-1.5 pr-3 text-muted hidden sm:table-cell">{s.sector ?? ''}</td>
                      <td className="py-1.5 pr-3 text-right">{billions(s.market_cap)}</td>
                      <td className="py-1.5 pr-3 text-right">{percent(s.earnings_yield)}</td>
                      <td className="py-1.5 pr-3 text-right hidden sm:table-cell">{percent(s.fcf_yield)}</td>
                      <td className="py-1.5 pr-3 text-right hidden sm:table-cell">{percent(s.roe)}</td>
                      <td className="py-1.5 pr-3">
                        {verdict ? (
                          <button onClick={() => setOpen(open === s.Ticker ? null : s.Ticker)}
                                  className={`text-tiny underline underline-offset-2 ${VERDICT_TONE[verdict.verdict]}`}>
                            {VERDICT_WORD[verdict.verdict]}
                          </button>
                        ) : <span className="text-tiny text-faint">—</span>}
                      </td>
                      <td className="py-1.5 text-right">
                        <button onClick={() => onResearch(s.Ticker)}
                                className="text-tiny text-accent underline underline-offset-2">
                          Research…
                        </button>
                      </td>
                    </tr>
                    {verdict && open === s.Ticker && (
                      <tr className="border-b border-rule/60 bg-raised/40">
                        <td colSpan={8} className="px-3 py-2 text-tiny space-y-1.5">
                          <p className="text-small">{verdict.rationale}</p>
                          {([['Strengths', verdict.strengths], ['Concerns', verdict.concerns],
                             ['Open questions', verdict.open_questions]] as const).map(([label, items]) =>
                            items.length > 0 && (
                              <p key={label}><span className="text-faint">{label}: </span>{items.join('; ')}</p>
                            ))}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  )
}
