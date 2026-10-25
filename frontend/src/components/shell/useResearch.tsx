import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'wouter'
import { startRun } from '../../api'
import { SpendConfirm } from './SpendConfirm'

// The one way to start research, used by the search box and by Discover alike. They used to start runs
// separately, with different ceilings and different gating, so the same action meant different things
// depending on where it was clicked.
export function useResearch() {
  const [ticker, setTicker] = useState<string | null>(null)
  const [, navigate] = useLocation()
  const queries = useQueryClient()

  const start = useMutation({
    mutationFn: (request: { ticker: string; budget: number; gated: boolean }) =>
      startRun(request.ticker, request.budget, request.gated ? ['expensive'] : []),
    onSuccess: (_, request) => {
      void queries.invalidateQueries({ queryKey: ['runs'] })
      void queries.invalidateQueries({ queryKey: ['companies'] })
      navigate(`/c/${request.ticker}`)
    },
  })

  const dialog = ticker ? (
    <SpendConfirm
      title={`Research ${ticker}`}
      detail={'Fetches the latest annual report if needed, has the agents read it and argue both sides, then '
        + 'writes a note with every claim checked against the filing. Recent runs cost between $0.14 and '
        + '$0.25 and take ten to twenty minutes.'}
      budget={2}
      confirmLabel="Start research"
      onConfirm={(budget, gated) => { start.mutate({ ticker, budget, gated }); setTicker(null) }}
      onCancel={() => setTicker(null)}
    />
  ) : null

  return { ask: setTicker, dialog, error: start.error as Error | null, starting: start.isPending }
}
