import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getCompany } from '../../api'
import { CompanyRuns } from './CompanyRuns'
import { Debate } from './Debate'
import { Filings } from './Filings'
import { Header } from './Header'
import { Numbers } from './Numbers'
import { Valuation } from './Valuation'
import { Thesis } from './Thesis'

const TABS = ['Thesis', 'Numbers', 'Debate', 'Valuation', 'Filings', 'Runs'] as const
type Tab = typeof TABS[number]

export function Company({ ticker }: { ticker: string }) {
  const [tab, setTab] = useState<Tab>('Thesis')
  const company = useQuery({ queryKey: ['company', ticker], queryFn: () => getCompany(ticker) })

  if (company.error) {
    return <p className="p-6 text-danger text-small">Could not load {ticker}.</p>
  }
  if (!company.data) {
    return <p className="p-6 text-faint text-small">Loading {ticker}.</p>
  }

  return (
    <div className="flex flex-col min-h-full">
      <Header company={company.data} />

      <nav className="flex gap-0 px-6 border-b border-rule" aria-label="Company sections">
        {TABS.map((name) => (
          <button
            key={name}
            onClick={() => setTab(name)}
            aria-current={tab === name ? 'page' : undefined}
            className={`px-3 py-2 text-small border-b-2 -mb-px ${tab === name
              ? 'border-accent text-ink'
              : 'border-transparent text-muted hover:text-ink'}`}
          >
            {name}
          </button>
        ))}
      </nav>

      <div className="flex-1 min-h-0">
        {tab === 'Thesis' && <Thesis ticker={ticker} />}
        {tab === 'Numbers' && <Numbers ticker={ticker} />}
        {tab === 'Debate' && <Debate ticker={ticker} />}
        {tab === 'Valuation' && <Valuation ticker={ticker} />}
        {tab === 'Filings' && <Filings ticker={ticker} />}
        {tab === 'Runs' && <CompanyRuns ticker={ticker} />}
      </div>
    </div>
  )
}
