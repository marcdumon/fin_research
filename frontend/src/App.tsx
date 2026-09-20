import { useEffect, useState } from 'react'
import {
  cheaperModel, decide, isRunning, listAlerts, listRuns, money, startPatrol, startRun,
  type MonitorAlert, type Run,
} from './api'
import { Alerts } from './components/Alerts'
import { CallTape } from './components/CallTape'
import { ModelCheck } from './components/ModelCheck'
import { NewRun } from './components/NewRun'
import { Screens } from './components/Screens'
import { SpendTape } from './components/SpendTape'
import { Stages } from './components/Stages'
import { Verify } from './components/Verify'
import { useRunStream } from './useRunStream'

type View = 'runs' | 'screens' | 'alerts'

const STATUS_WORDING: Record<string, string> = {
  running: 'Working',
  done: 'Finished',
  incomplete: 'Stopped early',
  budget_exceeded: 'Hit the ceiling',
  failed: 'Failed',
}

function RunRail({ runs, selected, onSelect, view, onView, alertCount, onStarted }: {
  runs: Run[]
  selected: string | null
  onSelect: (id: string) => void
  view: View
  onView: (view: View) => void
  alertCount: number
  onStarted: (runId: string) => void
}) {
  return (
    <nav aria-label="Runs" className="lg:w-64 lg:shrink-0 lg:border-r border-rule lg:h-dvh lg:overflow-y-auto">
      <div className="flex border-b border-rule">
        {(['runs', 'screens', 'alerts'] as const).map((name) => (
          <button
            key={name}
            onClick={() => onView(name)}
            aria-current={view === name ? 'page' : undefined}
            className={`flex-1 px-3 py-3.5 text-[13.5px] text-left border-r border-rule last:border-r-0
              ${view === name ? 'font-semibold bg-surface' : 'text-muted hover:text-ink'}`}
          >
            {name === 'runs' ? 'Runs' : name === 'screens' ? 'Screens'
              : `Changes${alertCount ? ` (${alertCount})` : ''}`}
          </button>
        ))}
      </div>

      <NewRun onStarted={onStarted} />
      <ModelCheck />
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
  const [view, setView] = useState<View>('runs')
  const [alerts, setAlerts] = useState<MonitorAlert[]>([])
  const { snapshot, calls, approvals, live, justLanded, resolveApproval } = useRunStream(selected)

  const refreshRuns = () => listRuns().then((found) => {
    setRuns(found)
    setSelected((current) => current ?? found[0]?.run_id ?? null)
  })

  const refreshAlerts = () => listAlerts().then(setAlerts).catch(() => setAlerts([]))

  useEffect(() => {
    void refreshRuns()
    void refreshAlerts()
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
      <RunRail
        runs={runs}
        selected={selected}
        onSelect={(id) => { setSelected(id); setView('runs') }}
        view={view}
        onView={setView}
        alertCount={alerts.length}
        onStarted={(runId) => { void refreshRuns(); setSelected(runId); setView('runs') }}
      />

      <main className="flex-1 min-w-0 px-4 sm:px-8 py-6 lg:h-dvh lg:overflow-y-auto">
        {view === 'screens' ? (
          <Screens onResearch={async (ticker) => {
            const runId = await startRun(ticker, 2, ['expensive'])
            void refreshRuns()
            if (runId) setSelected(runId)
            setView('runs')
          }} />
        ) : view === 'alerts' ? (
          <Alerts alerts={alerts} onAcknowledged={refreshAlerts} onPatrol={async () => {
            await startPatrol()
            setTimeout(refreshAlerts, 4000)
          }} />
        ) : !snapshot ? (
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
                    <span className="flex gap-2 flex-wrap">
                      {(
                        [
                          ['approved', 'Run it'],
                          ['downgraded', cheaperModel(approval.prompt)
                            ? `Use ${cheaperModel(approval.prompt)?.split('/').pop()}` : null],
                          ['rejected', 'Skip it'],
                        ] as const
                      ).map(([choice, label]) => label ? (
                        <button
                          key={choice}
                          onClick={() => {
                            void decide(approval.approval_id, choice)
                            resolveApproval(approval.approval_id)
                          }}
                          className={`px-3 py-1 text-[13px] ${
                            choice === 'approved' ? 'bg-ink text-surface' : 'border border-rule'}`}
                        >
                          {label}
                        </button>
                      ) : null)}
                    </span>
                  </div>
                ))}
              </section>
            )}

            <section aria-label="Route">
              <h3 className="text-[15px] font-medium mb-2">What each agent did</h3>
              <Stages tasks={snapshot.tasks} />
            </section>

            <section aria-label="Do the accounts add up">
              <h3 className="text-[15px] font-medium mb-2">Can these numbers be trusted?</h3>
              <Verify ticker={snapshot.ticker} />
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
