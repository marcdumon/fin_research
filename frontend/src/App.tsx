import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Route, Switch, useLocation } from 'wouter'
import { isRunning, listCompanies, listRuns } from './api'
import { Activity } from './components/activity/Activity'
import { Company } from './components/company/Company'
import { Discover } from './components/discover/Discover'
import { CommandPalette } from './components/shell/CommandPalette'
import { CompanyRail } from './components/shell/CompanyRail'
import { TopBar } from './components/shell/TopBar'
import { useResearch } from './components/shell/useResearch'
import { Watchlist } from './components/Watchlist'

export default function App() {
  const [palette, setPalette] = useState(false)
  const [, navigate] = useLocation()
  const research = useResearch()

  const companies = useQuery({ queryKey: ['companies'], queryFn: listCompanies })

  // Polled rather than streamed: the per-run stream answers what one run is doing, and the only
  // thing the chrome needs is whether anything at all is moving.
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => listRuns(), refetchInterval: 5_000 })
  const live = (runs.data ?? []).filter((run) => isRunning(run.status)).length

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault()
        setPalette((open) => !open)
      }
      if (event.key === 'Escape') setPalette(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="h-dvh flex flex-col">
      <TopBar onCommand={() => setPalette(true)} liveRuns={live} />

      <div className="flex-1 flex min-h-0">
        <CompanyRail
          companies={companies.data ?? []}
          loading={companies.isLoading}
          error={companies.error}
        />

        <main className="flex-1 min-w-0 overflow-y-auto">
          <Switch>
            <Route path="/" component={Watchlist} />
            <Route path="/c/:ticker">{(params) => <Company ticker={params.ticker.toUpperCase()} />}</Route>
            <Route path="/discover" component={Discover} />
            <Route path="/activity">{() => <Activity />}</Route>
            <Route path="/activity/:runId">
              {(params) => <Activity key={params.runId} runId={params.runId} />}
            </Route>
            <Route>
              <div className="p-8 text-muted">
                <p>Nothing here.</p>
                <button onClick={() => navigate('/')} className="text-accent hover:underline mt-2">
                  Back to the watchlist
                </button>
              </div>
            </Route>
          </Switch>
        </main>
      </div>

      <CommandPalette
        key={palette ? 'open' : 'closed'}
        open={palette}
        onClose={() => setPalette(false)}
        companies={companies.data ?? []}
        onResearch={research.ask}
      />
      {research.dialog}
      {research.error && (
        <div className="fixed bottom-3 left-1/2 -translate-x-1/2 z-50 px-3 py-2 bg-panel border border-danger
                        rounded-sm text-small text-danger">
          Could not start the run: {research.error.message}
        </div>
      )}
    </div>
  )
}
