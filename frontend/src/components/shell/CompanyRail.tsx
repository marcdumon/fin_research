import { Link, useRoute } from 'wouter'
import { direction, signed, TONE, type Company } from '../../api'

// What the dot says: whether there is a note on file, and which way it leans. A company nobody has
// read yet gets no colour rather than a neutral one, so unread and undecided do not look alike.
const DIRECTION: Record<string, string> = {
  buy: 'bg-constructive',
  watch: 'bg-cautious',
  pass: 'bg-faint',
  reject: 'bg-down',
}

function Dot({ company }: { company: Company }) {
  if (company.verdict) {
    return <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DIRECTION[company.verdict]}`}
                 title={company.verdict} aria-hidden="true" />
  }
  if (company.latest_status === 'done') {
    return <span className="w-1.5 h-1.5 rounded-full shrink-0 border border-muted"
                 title="researched, no verdict recorded" aria-hidden="true" />
  }
  return <span className="w-1.5 h-1.5 shrink-0" aria-hidden="true" />
}

export function CompanyRail({ companies, loading, error }: {
  companies: Company[]
  loading: boolean
  error: unknown
}) {
  const [, params] = useRoute('/c/:ticker')
  const current = params?.ticker?.toUpperCase()

  return (
    <nav aria-label="Companies"
         className="w-52 shrink-0 border-r border-rule bg-panel overflow-y-auto hidden md:block">
      <div className="px-3 py-2 text-micro uppercase tracking-wider text-faint border-b border-rule">
        Companies
      </div>

      {error ? (
        <p className="px-3 py-3 text-small text-danger">Could not load companies.</p>
      ) : loading ? (
        <p className="px-3 py-3 text-small text-faint">Loading.</p>
      ) : companies.length === 0 ? (
        <p className="px-3 py-3 text-small text-muted">
          Nothing researched yet. Press <kbd className="font-mono text-tiny">⌘K</kbd> to start.
        </p>
      ) : (
        <ul>
          {companies.map((company) => (
            <li key={company.ticker}>
              <Link
                href={`/c/${company.ticker}`}
                aria-current={current === company.ticker ? 'page' : undefined}
                className={`flex items-center gap-2 px-3 py-1.5 border-l-2 ${current === company.ticker
                  ? 'bg-raised border-accent'
                  : 'border-transparent hover:bg-raised/60'}`}
              >
                <Dot company={company} />
                <span className="font-medium text-small w-12 shrink-0">{company.ticker}</span>
                <span className={`text-tiny tabular-nums ml-auto ${TONE[direction(company.change_pct)]}`}>
                  {signed(company.change_pct)}
                </span>
                {company.open_alerts > 0 && (
                  <span className="text-tiny text-cautious" title={`${company.open_alerts} unread`}>●</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </nav>
  )
}
