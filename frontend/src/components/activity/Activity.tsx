import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { cheaperModel, decide, isRunning, listRuns, money } from '../../api'
import { useRunStream } from '../../useRunStream'
import { CallTape } from '../CallTape'
import { ModelCheck } from '../ModelCheck'
import { SpendTape } from '../SpendTape'
import { Stages } from '../Stages'
import { Verify } from '../Verify'

const STATUS_WORDING: Record<string, string> = {
  running: 'Working',
  done: 'Finished',
  incomplete: 'Stopped early',
  budget_exceeded: 'Hit the ceiling',
  failed: 'Failed',
  interrupted: 'Interrupted',
}

// The back room. Everything here is about the machinery rather than the company: what it spent, what
// each agent did, what the provider said. It used to be the front door, which meant the first thing the
// dashboard showed was a table of parse errors.
export function Activity({ runId }: { runId?: string }) {
  const [chosen, setChosen] = useState<string | null>(runId ?? null)
  const [decisionFailed, setDecisionFailed] = useState(false)
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => listRuns(), refetchInterval: 5_000 })
  // Derived rather than stored: until something is chosen, the newest run is the one being watched.
  const selected = chosen ?? runs.data?.[0]?.run_id ?? null
  const { snapshot, calls, approvals, live, justLanded, resolveApproval } = useRunStream(selected)

  return (
    <div className="flex min-h-full">
      <aside className="w-56 shrink-0 border-r border-rule overflow-y-auto">
        <div className="px-3 py-2 text-micro uppercase tracking-wider text-faint border-b border-rule">
          Runs
        </div>
        {runs.error ? (
          <p className="px-3 py-3 text-small text-danger">Could not load runs.</p>
        ) : (
          <ul>
            {(runs.data ?? []).map((run) => (
              <li key={run.run_id}>
                <button
                  onClick={() => setChosen(run.run_id)}
                  aria-current={run.run_id === selected ? 'true' : undefined}
                  className={`w-full text-left px-3 py-2 border-l-2 border-b border-b-rule/50
                    ${run.run_id === selected ? 'bg-raised border-l-accent' : 'border-l-transparent hover:bg-raised/60'}`}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="font-medium text-small">{run.ticker}</span>
                    <span className="text-tiny text-muted tabular-nums">{money(run.spend_usd)}</span>
                  </span>
                  <span className="flex items-baseline justify-between gap-2">
                    <span className={`text-tiny ${isRunning(run.status) ? 'text-accent' : 'text-faint'}`}>
                      {STATUS_WORDING[run.status] ?? run.status}
                    </span>
                    <span className="text-tiny text-faint">{run.as_of}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="border-t border-rule"><ModelCheck /></div>
      </aside>

      <div className="flex-1 min-w-0 px-6 py-5 overflow-y-auto">
        {!snapshot ? (
          <p className="text-faint text-small">
            {runs.data?.length ? 'Select a run.' : 'No runs yet.'}
          </p>
        ) : (
          <div className="max-w-4xl space-y-8">
            <header className="flex items-baseline gap-3 flex-wrap">
              <h1 className="text-title font-semibold tracking-tight">{snapshot.ticker}</h1>
              <span className="text-muted text-small">
                {STATUS_WORDING[snapshot.status] ?? snapshot.status}
              </span>
              {live && isRunning(snapshot.status) && (
                <span className="text-accent text-tiny">live</span>
              )}
            </header>

            <SpendTape byAgent={snapshot.by_agent} spend={snapshot.spend_usd} budget={snapshot.budget_usd}
                       status={snapshot.status} calls={snapshot.calls} />

            {approvals.length > 0 && (
              <section aria-label="Waiting on you"
                       className="bg-panel border-l-2 border-mid px-4 py-3 space-y-3">
                <h2 className="text-small font-medium">Waiting on you</h2>
                {approvals.map((approval) => (
                  <div key={approval.approval_id}
                       className="flex items-baseline justify-between gap-4 flex-wrap">
                    <p className="text-small">{approval.prompt}</p>
                    <span className="flex gap-2 flex-wrap">
                      {([
                        ['approved', 'Run it'],
                        ['downgraded', cheaperModel(approval.prompt)
                          ? `Use ${cheaperModel(approval.prompt)?.split('/').pop()}` : null],
                        ['rejected', 'Skip it'],
                      ] as const).map(([choice, label]) => label ? (
                        <button
                          key={choice}
                          onClick={() => {
                            decide(approval.approval_id, choice)
                              .then(() => resolveApproval(approval.approval_id))
                              .catch(() => setDecisionFailed(true))
                          }}
                          className={`px-2.5 py-1 text-tiny rounded-sm ${choice === 'approved'
                            ? 'bg-accent text-ground' : 'border border-rule'}`}
                        >
                          {label}
                        </button>
                      ) : null)}
                    </span>
                  </div>
                ))}
                {decisionFailed && (
                  <p className="text-tiny text-danger">That answer was not recorded. Try again.</p>
                )}
              </section>
            )}

            <section aria-label="Route">
              <h2 className="text-small font-medium mb-2">What each agent did</h2>
              <Stages tasks={snapshot.tasks} />
            </section>

            <section aria-label="Do the accounts add up">
              <h2 className="text-small font-medium mb-2">Can these numbers be trusted?</h2>
              <Verify ticker={snapshot.ticker} />
            </section>

            <section aria-label="Model calls">
              <h2 className="text-small font-medium mb-2">Every call it made</h2>
              <CallTape calls={calls} justLanded={justLanded} />
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
