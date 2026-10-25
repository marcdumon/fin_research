import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'wouter'
import {
  acknowledgeAlerts, direction, jobStatus, listAlerts, listCompanies, marketCap, price, signed, startPatrol,
  TONE, type Company,
} from '../api'

const VERDICT_STYLE: Record<string, string> = {
  buy: 'text-constructive',
  watch: 'text-cautious',
  pass: 'text-muted',
  reject: 'text-down',
}

function Verdict({ company }: { company: Company }) {
  if (company.verdict) {
    return (
      <span className={VERDICT_STYLE[company.verdict]}>
        {company.verdict}
        {company.conviction && <span className="text-faint"> · {company.conviction}</span>}
      </span>
    )
  }
  // A run's status is not a conclusion. It used to be shown here, so a company whose run hit its
  // budget read as though 'budget_exceeded' were the analyst's view of it. What the run did is under Runs.
  if (company.latest_status === 'done') return <span className="text-faint">undecided</span>
  if (company.runs > 0) return <span className="text-faint">no note yet</span>
  return <span className="text-faint">unread</span>
}

// Checking for changes reads filings from the SEC and calls no model, so it costs nothing and needs
// no confirmation. It runs in the background; the job is polled until it ends so 'checking' is true rather
// than a guess at how long a patrol takes.
function Changes() {
  const queries = useQueryClient()
  const [job, setJob] = useState<number | null>(null)
  const alerts = useQuery({ queryKey: ['alerts'], queryFn: () => listAlerts() })

  const status = useQuery({
    queryKey: ['job', job],
    queryFn: () => jobStatus(job!),
    enabled: job !== null,
    refetchInterval: (query) => (query.state.data?.running === false ? false : 2_000),
  })
  const finished = job !== null && status.data?.running === false
  const checking = job !== null && !finished

  useEffect(() => {
    if (!finished) return
    void queries.invalidateQueries({ queryKey: ['alerts'] })
    void queries.invalidateQueries({ queryKey: ['companies'] })
  }, [finished, queries])

  const check = useMutation({
    mutationFn: () => startPatrol(),
    onSuccess: (started) => setJob(started.pid),
  })
  const seen = useMutation({
    mutationFn: (ticker?: string) => acknowledgeAlerts(ticker),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ['alerts'] })
      void queries.invalidateQueries({ queryKey: ['companies'] })
    },
  })

  const rows = alerts.data ?? []

  return (
    <section className="mt-8" aria-label="What changed">
      <div className="flex items-baseline gap-3 mb-2">
        <h2 className="text-small font-medium">What changed</h2>
        <span className="text-tiny text-faint">
          new filings, material events, large price moves and screen changes for watched companies
        </span>
        <span className="ml-auto flex gap-2">
          {rows.length > 0 && (
            <button onClick={() => seen.mutate(undefined)}
                    className="px-2 py-0.5 text-tiny border border-rule rounded-sm text-muted hover:text-ink">
              Mark all seen
            </button>
          )}
          <button onClick={() => check.mutate()} disabled={checking}
                  className="px-2 py-0.5 text-tiny border border-rule rounded-sm text-muted hover:text-ink
                             disabled:opacity-60">
            {checking ? 'Checking the filings…' : 'Check for changes'}
          </button>
        </span>
      </div>

      {alerts.error ? (
        <p className="text-small text-danger">Could not load changes.</p>
      ) : check.error ? (
        <p className="text-small text-danger">Could not start the check.</p>
      ) : finished && status.data?.exit_code ? (
        <p className="text-small text-danger">The check ended with an error. Its log is in data/runs.</p>
      ) : rows.length === 0 ? (
        <p className="text-small text-faint">Nothing new since you last marked changes as seen.</p>
      ) : (
        <ul>
          {rows.map((alert, index) => (
            <li key={`${alert.ticker}-${alert.check_name}-${index}`}
                className="flex items-baseline gap-3 py-1 border-b border-rule/50 text-small">
              <Link href={`/c/${alert.ticker}`} className="font-medium text-accent w-14 shrink-0">
                {alert.ticker}
              </Link>
              <span className={alert.severity === 'high' ? 'text-danger' : 'text-ink'}>{alert.headline}</span>
              {alert.reference && (
                <a href={alert.reference} target="_blank" rel="noreferrer"
                   className="text-tiny text-accent hover:underline shrink-0">filing ↗</a>
              )}
              <span className="ml-auto text-tiny text-faint shrink-0">{alert.as_of}</span>
              <button onClick={() => seen.mutate(alert.ticker)}
                      className="text-tiny text-faint hover:text-ink shrink-0">seen</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function Watchlist() {
  const companies = useQuery({ queryKey: ['companies'], queryFn: listCompanies })
  const rows = companies.data ?? []
  const changed = rows.filter((c) => c.open_alerts > 0)
  const closes = rows.map((c) => c.price_date).filter((d): d is string => !!d).sort()
  const latestClose = closes.at(-1)

  return (
    <div className="px-6 py-5 max-w-6xl">
      <div className="flex items-baseline gap-3 mb-4 flex-wrap">
        <h1 className="text-title font-semibold tracking-tight">Watchlist</h1>
        <span className="text-small text-faint">
          {rows.length} {rows.length === 1 ? 'company' : 'companies'}
        </span>
        {latestClose && <span className="text-tiny text-faint">prices are closes as of {latestClose}</span>}
        {changed.length > 0 && (
          <span className="text-small text-cautious ml-auto">{changed.length} with something new</span>
        )}
      </div>

      {companies.error ? (
        <p className="text-danger text-small">Could not reach the server. It may not be running.</p>
      ) : companies.isLoading ? (
        <p className="text-faint text-small">Loading.</p>
      ) : rows.length === 0 ? (
        <p className="text-muted text-small">
          Nothing researched yet. Press <kbd className="font-mono text-tiny">⌘K</kbd>, type a ticker, and
          choose Research.
        </p>
      ) : (
        <table className="w-full text-small">
          <thead>
            <tr className="text-micro uppercase tracking-wider text-faint border-b border-rule">
              <th className="text-left font-normal py-1.5 pr-3">Ticker</th>
              <th className="text-left font-normal py-1.5 pr-3">Company</th>
              <th className="text-left font-normal py-1.5 pr-3 hidden lg:table-cell">Sector</th>
              <th className="text-right font-normal py-1.5 px-3">Close</th>
              <th className="text-right font-normal py-1.5 px-3" title="Change from the close before">Move</th>
              <th className="text-right font-normal py-1.5 px-3 hidden sm:table-cell">Mkt cap</th>
              <th className="text-left font-normal py-1.5 px-3">Verdict</th>
              <th className="text-left font-normal py-1.5 px-3 hidden md:table-cell">Decided</th>
              <th className="text-left font-normal py-1.5 px-3 hidden md:table-cell">Researched</th>
              <th className="text-right font-normal py-1.5 pl-3">New</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((company) => (
              <tr key={company.ticker} className="border-b border-rule/60 hover:bg-raised/50">
                <td className="py-1.5 pr-3">
                  <Link href={`/c/${company.ticker}`} className="font-medium text-accent hover:underline">
                    {company.ticker}
                  </Link>
                </td>
                <td className="py-1.5 pr-3 text-muted truncate max-w-[16rem]">{company.name ?? '—'}</td>
                <td className="py-1.5 pr-3 text-faint hidden lg:table-cell">{company.sector ?? '—'}</td>
                <td className="py-1.5 px-3 text-right tabular-nums"
                    title={company.price_date ? `close on ${company.price_date}` : undefined}>
                  {price(company.price)}
                </td>
                <td className={`py-1.5 px-3 text-right tabular-nums ${TONE[direction(company.change_pct)]}`}>
                  {signed(company.change_pct)}
                </td>
                <td className="py-1.5 px-3 text-right tabular-nums text-muted hidden sm:table-cell">
                  {marketCap(company.market_cap)}
                </td>
                <td className="py-1.5 px-3"><Verdict company={company} /></td>
                <td className="py-1.5 px-3 text-faint hidden md:table-cell">
                  {company.reviewed_at?.slice(0, 10) ?? '—'}
                </td>
                <td className="py-1.5 px-3 text-faint hidden md:table-cell">{company.researched_at ?? '—'}</td>
                <td className="py-1.5 pl-3 text-right">
                  {company.open_alerts > 0
                    ? <span className="text-cautious">{company.open_alerts}</span>
                    : <span className="text-faint">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Changes />
    </div>
  )
}
