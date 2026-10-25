import { useMutation, useQueryClient } from '@tanstack/react-query'
import { direction, marketCap, price, setVerdict, signed, TONE, type CompanyDetail, type Conviction,
  type VerdictName } from '../../api'

const CHOICES: { name: VerdictName; label: string; tone: string }[] = [
  { name: 'buy', label: 'Buy', tone: 'text-constructive border-constructive' },
  { name: 'watch', label: 'Watch', tone: 'text-cautious border-cautious' },
  { name: 'pass', label: 'Pass', tone: 'text-muted border-rule-strong' },
  { name: 'reject', label: 'Reject', tone: 'text-down border-down' },
]

const CONVICTIONS: Conviction[] = ['low', 'medium', 'high']

export function Header({ company }: { company: CompanyDetail }) {
  const queries = useQueryClient()
  const record = useMutation({
    mutationFn: (body: { verdict: VerdictName; conviction?: Conviction | null }) =>
      setVerdict(company.ticker, { ...body, run_id: company.latest_run_id }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ['companies'] })
      void queries.invalidateQueries({ queryKey: ['company', company.ticker] })
    },
  })

  return (
    <header className="px-6 pt-4 pb-3 border-b border-rule">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-display font-semibold tracking-tight">{company.ticker}</h1>
        <span className="text-lede text-muted">{company.name ?? '—'}</span>
        <span className="text-tiny text-faint ml-auto">
          {[company.sector, company.industry].filter(Boolean).join(' · ') || 'sector unknown'}
        </span>
      </div>

      <div className="flex items-baseline gap-5 mt-2 flex-wrap text-small">
        <span className="text-title font-medium tabular-nums">{price(company.price)}</span>
        <span className={`tabular-nums ${TONE[direction(company.change_pct)]}`}
              title="Change from the close before">
          {signed(company.change_pct)}
        </span>
        {/* The price is a close from the market database, which is updated separately and can be days
            old. Shown without its date it read as a live quote. */}
        {company.price_date && (
          <span className="text-muted"><span className="text-faint">close </span>{company.price_date}</span>
        )}
        <span className="text-muted">
          <span className="text-faint">mkt cap </span>{marketCap(company.market_cap)}
        </span>
        {company.last_filed && (
          <span className="text-muted"><span className="text-faint">last filed </span>{company.last_filed}</span>
        )}
        {company.runs > 0 && (
          <span className="text-muted">
            <span className="text-faint">researched </span>{company.researched_at ?? '—'}
          </span>
        )}
        {record.error && <span className="text-danger">Could not record the verdict.</span>}

        <span className="ml-auto flex items-center gap-1">
          {CHOICES.map((choice) => (
            <button
              key={choice.name}
              onClick={() => record.mutate({ verdict: choice.name, conviction: company.conviction })}
              aria-pressed={company.verdict === choice.name}
              className={`px-2 py-0.5 text-tiny border rounded-sm ${company.verdict === choice.name
                ? choice.tone
                : 'text-faint border-rule hover:border-rule-strong hover:text-muted'}`}
            >
              {choice.label}
            </button>
          ))}
          {company.verdict && (
            <span className="flex items-center gap-1 ml-2">
              {CONVICTIONS.map((level) => (
                <button
                  key={level}
                  onClick={() => record.mutate({ verdict: company.verdict!, conviction: level })}
                  aria-pressed={company.conviction === level}
                  className={`px-1.5 py-0.5 text-micro rounded-sm ${company.conviction === level
                    ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}
                >
                  {level}
                </button>
              ))}
            </span>
          )}
        </span>
      </div>
    </header>
  )
}
