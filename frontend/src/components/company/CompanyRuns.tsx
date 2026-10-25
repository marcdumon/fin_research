import { useQuery } from '@tanstack/react-query'
import { Link } from 'wouter'
import { isRunning, listRuns, money } from '../../api'

const STATUS_WORDING: Record<string, string> = {
  running: 'Working',
  done: 'Finished',
  incomplete: 'Stopped early',
  budget_exceeded: 'Hit the ceiling',
  failed: 'Failed',
  interrupted: 'Interrupted',
}

export function CompanyRuns({ ticker }: { ticker: string }) {
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => listRuns(), refetchInterval: 5_000 })
  const mine = (runs.data ?? []).filter((run) => run.ticker.toUpperCase() === ticker)

  if (mine.length === 0) {
    return <p className="px-6 py-5 text-small text-muted">No run has been started for {ticker}.</p>
  }

  return (
    <div className="px-6 py-5 max-w-3xl">
      <table className="w-full text-small">
        <thead>
          <tr className="text-micro uppercase tracking-wider text-faint border-b border-rule">
            <th className="text-left font-normal py-1.5 pr-3">Started</th>
            <th className="text-left font-normal py-1.5 pr-3">Outcome</th>
            <th className="text-right font-normal py-1.5 px-3">Spent</th>
            <th className="text-right font-normal py-1.5 px-3">Budget</th>
            <th className="text-right font-normal py-1.5 px-3">Calls</th>
            <th className="text-left font-normal py-1.5 pl-3"></th>
          </tr>
        </thead>
        <tbody>
          {mine.map((run) => (
            <tr key={run.run_id} className="border-b border-rule/60 hover:bg-raised/50">
              <td className="py-1.5 pr-3 tabular-nums text-muted">{run.started_at.slice(0, 16).replace('T', ' ')}</td>
              <td className={`py-1.5 pr-3 ${isRunning(run.status) ? 'text-accent' : 'text-muted'}`}>
                {STATUS_WORDING[run.status] ?? run.status}
              </td>
              <td className="py-1.5 px-3 text-right tabular-nums">{money(run.spend_usd)}</td>
              <td className="py-1.5 px-3 text-right tabular-nums text-faint">{money(run.budget_usd)}</td>
              <td className="py-1.5 px-3 text-right tabular-nums text-muted">{run.calls}</td>
              <td className="py-1.5 pl-3">
                <Link href={`/activity/${run.run_id}`} className="text-accent hover:underline text-tiny">
                  trace ↗
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-micro text-faint mt-2">
        What each run cost and how it ended. Trace opens what it did, call by call.
      </p>
    </div>
  )
}
