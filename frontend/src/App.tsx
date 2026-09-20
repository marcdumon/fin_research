import { useEffect, useState } from 'react'
import { decide, isRunning, listRuns, money, type Run } from './api'
import { CallTape } from './components/CallTape'
import { SpendTape } from './components/SpendTape'
import { Stages } from './components/Stages'
import { useRunStream } from './useRunStream'

const STATUS_WORDING: Record<string, string> = {
  running: 'Working',
  done: 'Finished',
  incomplete: 'Stopped early',
  budget_exceeded: 'Hit the ceiling',
  failed: 'Failed',
}

function RunRail({ runs, selected, onSelect }: {
  runs: Run[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  return (
    <nav aria-label="Runs" className="lg:w-64 lg:shrink-0 lg:border-r border-rule lg:h-dvh lg:overflow-y-auto">
      <h1 className="px-4 py-4 text-[15px] font-semibold border-b border-rule">Research runs</h1>
      {/* Capped on narrow screens so a long history does not push the run itself below the fold. */}
      <ul className="max-h-64 overflow-y-auto lg:max-h-none lg:overflow-visible">
        {runs.map((run) => {
          const active = run.run_id === selected
          return (
            <li key={run.run_id}>
              <button
                onClick={() => onSelect(run.run_id)}
                aria-current={active ? 'true' : undefined}
                className={`w-full text-left px-4 py-3 border-b border-rule/70 transition-colors
                  ${active ? 'bg-surface' : 'hover:bg-surface/60'}`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{run.ticker}</span>
                  <span className="text-[13px] text-muted">{money(run.spend_usd)}</span>
                </span>
                <span className="flex items-baseline justify-between gap-2 mt-0.5">
                  <span className={`text-[12.5px] ${isRunning(run.status) ? 'text-link' : 'text-muted'}`}>
                    {STATUS_WORDING[run.status] ?? run.status}
                  </span>
                  <span className="text-[12.5px] text-muted">{run.as_of}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

export default function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const { snapshot, calls, approvals, live, justLanded } = useRunStream(selected)

  useEffect(() => {
    listRuns().then((found) => {
      setRuns(found)
      setSelected((current) => current ?? found[0]?.run_id ?? null)
    })
  }, [])

  // Keep the rail's totals honest while a run is moving.
  useEffect(() => {
    if (!snapshot) return
    setRuns((previous) => previous.map((run) =>
      run.run_id === snapshot.run_id
        ? { ...run, spend_usd: snapshot.spend_usd, status: snapshot.status, calls: snapshot.calls }
        : run))
  }, [snapshot])

  return (
    <div className="lg:flex min-h-dvh">
      <RunRail runs={runs} selected={selected} onSelect={setSelected} />

      <main className="flex-1 min-w-0 px-4 sm:px-8 py-6 lg:h-dvh lg:overflow-y-auto">
        {!snapshot ? (
          <p className="text-muted">
            {runs.length ? 'Loading the run.' : 'No runs yet. Start one from the command line.'}
          </p>
        ) : (
          <div className="max-w-[60rem] space-y-10">
            <header className="flex items-baseline gap-3 flex-wrap">
              <h2 className="text-2xl font-semibold tracking-tight">{snapshot.ticker}</h2>
              <span className="text-muted">{STATUS_WORDING[snapshot.status] ?? snapshot.status}</span>
              {live && isRunning(snapshot.status) ? (
                <span className="text-link text-[13px]">live</span>
              ) : null}
            </header>

            <SpendTape byAgent={snapshot.by_agent} spend={snapshot.spend_usd} budget={snapshot.budget_usd}
                       status={snapshot.status} calls={snapshot.calls} />

            {approvals.length > 0 && (
              <section aria-label="Waiting on you"
                       className="bg-surface border-l-2 border-mid px-4 py-3 space-y-3 max-w-2xl">
                <h3 className="text-[15px] font-medium">Waiting on you</h3>
                {approvals.map((approval) => (
                  <div key={approval.approval_id} className="flex items-baseline justify-between gap-4 flex-wrap">
                    <p className="text-[14px]">{approval.prompt}</p>
                    <span className="flex gap-2">
                      <button onClick={() => decide(approval.approval_id, true)}
                              className="px-3 py-1 text-[13px] bg-ink text-surface">Approve</button>
                      <button onClick={() => decide(approval.approval_id, false)}
                              className="px-3 py-1 text-[13px] border border-rule">Stop here</button>
                    </span>
                  </div>
                ))}
              </section>
            )}

            <section aria-label="Route">
              <h3 className="text-[15px] font-medium mb-2">What each agent did</h3>
              <Stages tasks={snapshot.tasks} />
            </section>

            <section aria-label="Model calls">
              <h3 className="text-[15px] font-medium mb-2">Every call it made</h3>
              <CallTape calls={calls} justLanded={justLanded} />
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
