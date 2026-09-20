import { compact, money, type AgentCost, type Tier } from '../api'

const TIER_FILL: Record<Tier, string> = {
  cheap: 'var(--color-cheap)',
  mid: 'var(--color-mid)',
  expensive: 'var(--color-expensive)',
}

const TIER_ORDER: Tier[] = ['cheap', 'mid', 'expensive']

const TIER_MEANING: Record<Tier, string> = {
  cheap: 'output a check can verify',
  mid: 'checkable figures, unverifiable reading',
  expensive: 'judgement no check catches',
}

// The hero, and it answers "where did the money go" rather than "how much budget is left".
//
// An earlier version filled this bar against the budget. That was wrong: a budget is a ceiling set well above
// expected spend, so the bar was permanently a sliver and the legend beneath it carried all the meaning. The
// proportions worth seeing are the tiers against each other, because the system's whole premise is that most
// work belongs on a cheap model. Budget is real but secondary, so it is stated rather than drawn.
export function SpendTape({ byAgent, spend, budget, status, calls }: {
  byAgent: AgentCost[]
  spend: number
  budget: number
  status: string
  calls: number
}) {
  const spent = byAgent.filter((a) => (a.cost_usd ?? 0) > 0)
  const stopped = status === 'budget_exceeded'

  const byTier = TIER_ORDER.map((tier) => {
    const rows = spent.filter((a) => a.tier === tier)
    return {
      tier,
      cost: rows.reduce((total, a) => total + (a.cost_usd ?? 0), 0),
      calls: rows.reduce((total, a) => total + a.calls, 0),
      agents: rows.map((a) => a.agent),
    }
  }).filter((t) => t.cost > 0)

  const tokens = spent.reduce((total, a) => total + (a.prompt_tokens ?? 0) + (a.completion_tokens ?? 0), 0)

  if (!spent.length) {
    return (
      <section aria-label="Spend">
        <p className="text-[15px] text-muted">
          {stopped ? 'Stopped before spending anything: the ceiling was already reached.'
                   : 'Nothing spent yet.'}
        </p>
      </section>
    )
  }

  return (
    <section aria-label="Spend by tier">
      <div className="flex flex-wrap items-baseline gap-x-10 gap-y-2">
        <span className="text-4xl font-bold tracking-tight">{money(spend)}</span>
        <span className="text-[15px] text-muted">{compact(tokens)} tokens</span>
        <span className="text-[15px] text-muted">{calls} calls</span>
        <span className={`text-[15px] ${stopped ? 'text-expensive font-medium' : 'text-muted'}`}>
          {stopped ? 'stopped at the ceiling' : `ceiling ${money(budget)}`}
        </span>
      </div>

      <div className="mt-5 flex h-14 w-full overflow-hidden" role="img"
           aria-label={byTier.map((t) => `${t.tier} ${money(t.cost)}`).join(', ')}>
        {byTier.map((tier) => (
          <div key={tier.tier} style={{ width: `${(tier.cost / spend) * 100}%`, background: TIER_FILL[tier.tier] }}
               className="relative border-r-2 border-ground last:border-r-0 transition-[width] duration-700">
            <span className="absolute inset-0 flex items-center px-3 text-surface text-[13px] font-medium
                             whitespace-nowrap overflow-hidden">
              {tier.tier}
            </span>
          </div>
        ))}
      </div>

      <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-3">
        {byTier.map((tier) => (
          <div key={tier.tier} className="border-t-2 pt-2" style={{ borderColor: TIER_FILL[tier.tier] }}>
            <dt className="flex items-baseline justify-between gap-2 text-[14px]">
              <span className="font-medium">{money(tier.cost)}</span>
              <span className="text-muted">{Math.round((tier.cost / spend) * 100)}%</span>
            </dt>
            <dd className="text-[13px] text-muted mt-0.5">
              {TIER_MEANING[tier.tier]}
              <span className="block mt-0.5">{tier.agents.join(', ')}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
