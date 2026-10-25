import { useQuery } from '@tanstack/react-query'
import { getFilings } from '../../api'

export function Filings({ ticker }: { ticker: string }) {
  const filings = useQuery({ queryKey: ['filings', ticker], queryFn: () => getFilings(ticker) })

  if (filings.isLoading) return <p className="p-6 text-small text-faint">Loading.</p>
  if (filings.error) return <p className="p-6 text-small text-danger">Could not load filings.</p>

  const rows = filings.data ?? []
  if (rows.length === 0) {
    return (
      <p className="px-6 py-5 text-small text-muted max-w-2xl">
        Nothing ingested for {ticker}. A research run fetches the filings first.
      </p>
    )
  }

  return (
    <div className="px-6 py-5 max-w-4xl">
      <table className="w-full text-small">
        <thead>
          <tr className="text-micro uppercase tracking-wider text-faint border-b border-rule">
            <th className="text-left font-normal py-1.5 pr-3">Form</th>
            <th className="text-left font-normal py-1.5 pr-3">Filed</th>
            <th className="text-left font-normal py-1.5 pr-3">Period</th>
            <th className="text-left font-normal py-1.5 pr-3 hidden sm:table-cell">What it reports</th>
            <th className="text-right font-normal py-1.5 px-3">Sections</th>
            <th className="text-left font-normal py-1.5 pl-3">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((filing) => (
            <tr key={filing.accession} className="border-b border-rule/60 hover:bg-raised/50">
              <td className="py-1.5 pr-3 font-medium">{filing.form}</td>
              <td className="py-1.5 pr-3 tabular-nums text-muted">{filing.filed}</td>
              <td className="py-1.5 pr-3 tabular-nums text-faint">{filing.report_date ?? '—'}</td>
              <td className="py-1.5 pr-3 text-muted hidden sm:table-cell">
                {filing.items.length ? filing.items.join('; ') : <span className="text-faint">—</span>}
              </td>
              <td className="py-1.5 px-3 text-right tabular-nums text-muted">
                {filing.sections || <span className="text-faint">—</span>}
              </td>
              <td className="py-1.5 pl-3">
                {filing.url
                  ? <a href={filing.url} target="_blank" rel="noreferrer"
                       className="text-accent hover:underline text-tiny">sec.gov ↗</a>
                  : <span className="text-faint text-tiny">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-micro text-faint mt-2">
        Sections are the parts of an annual report that were located and can be read by an agent.
      </p>
    </div>
  )
}
