import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { direction, figure, getFundamentals, getPrices, signed, TONE, type FactPoint } from '../../api'
import { PriceChart } from './PriceChart'

const YEARS = [1, 3, 5, 10]

// Drawn by hand rather than by the chart library: these are ten small panels of five points each,
// and a full charting instance per panel costs more than the pixels are worth.
function Sparkbars({ points }: { points: FactPoint[] }) {
  if (points.length < 2) return null
  const values = points.map((p) => p.value)
  const top = Math.max(...values, 0)
  const bottom = Math.min(...values, 0)
  const span = top - bottom || 1
  const zero = ((top - 0) / span) * 100

  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="w-full h-10" aria-hidden="true">
      <line x1="0" y1={(zero / 100) * 40} x2="100" y2={(zero / 100) * 40}
            stroke="var(--color-rule)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      {points.map((point, index) => {
        const width = 100 / points.length
        const x = index * width
        const height = (Math.abs(point.value) / span) * 40
        const y = point.value >= 0 ? (zero / 100) * 40 - height : (zero / 100) * 40
        return (
          <rect key={point.period_end} x={x + width * 0.18} y={y} width={width * 0.64}
                height={Math.max(height, 0.6)}
                fill={point.value >= 0 ? 'var(--color-up)' : 'var(--color-down)'} opacity="0.75" />
        )
      })}
    </svg>
  )
}

function year(point: FactPoint | undefined): number | null {
  return point ? Number(point.period_end.slice(0, 4)) : null
}

// A percentage change means something only when both years are positive. From a loss of 97.5M to a
// profit of 108.1M the card used to say +210.9%, and a capital expenditure that rose from 0.94B to 1.08B,
// being stored as an outflow, read as -14.8% in red. Where the percentage would mislead, the change is given
// as an amount instead, uncoloured, because an amount cannot be read as better or worse by its sign alone.
function Change({ latest, previous }: { latest?: FactPoint; previous?: FactPoint }) {
  if (!latest || !previous) return null
  if (latest.value > 0 && previous.value > 0) {
    const change = (latest.value - previous.value) / previous.value
    return <span className={`text-tiny tabular-nums ${TONE[direction(change)]}`}>{signed(change)}</span>
  }
  const delta = latest.value - previous.value
  return (
    <span className="text-tiny tabular-nums text-muted" title="Change in amount; a percentage would mislead">
      {delta >= 0 ? '+' : ''}{figure(delta)}
    </span>
  )
}

function Metric({ name, points, current }: { name: string; points: FactPoint[]; current: number | null }) {
  const latest = points[points.length - 1]
  const previous = points[points.length - 2]
  const latestYear = year(latest)
  const stale = current !== null && latestYear !== null && latestYear < current

  return (
    <div className={`border rounded-sm p-3 bg-panel ${stale ? 'border-dashed border-rule-strong' : 'border-rule'}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-tiny text-muted truncate"
              title={latest ? `${latest.tag}, as filed ${latest.filed} in the ${latest.form}` : name}>
          {name}
        </span>
        <Change latest={latest} previous={previous} />
      </div>
      {latest?.meaning && <div className="text-micro text-faint truncate">{latest.meaning}</div>}
      <div className="flex items-baseline gap-2 mt-0.5">
        <span className={`text-lede font-medium tabular-nums ${stale ? 'text-muted' : ''}`}>
          {figure(latest?.value)}
        </span>
        <span className={`text-micro ${stale ? 'text-cautious' : 'text-faint'}`}>
          FY{latestYear}{stale ? ' · latest reported' : ''}
        </span>
      </div>
      <Sparkbars points={points} />
      <div className="flex justify-between text-micro text-faint">
        <span>{year(points[0])}</span>
        <span>{latestYear}</span>
      </div>
    </div>
  )
}

export function Numbers({ ticker }: { ticker: string }) {
  const [years, setYears] = useState(5)
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute('data-theme') ?? 'dark')

  // The chart takes its colours from the tokens once, at creation, so it has to be rebuilt when the
  // theme changes. Watching the attribute is how it learns.
  useEffect(() => {
    const watcher = new MutationObserver(() =>
      setTheme(document.documentElement.getAttribute('data-theme') ?? 'dark'))
    watcher.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => watcher.disconnect()
  }, [])

  const prices = useQuery({
    queryKey: ['prices', ticker, years],
    queryFn: () => getPrices(ticker, years),
  })
  const facts = useQuery({ queryKey: ['fundamentals', ticker], queryFn: () => getFundamentals(ticker) })

  // The company's most recent financial year, against which each card's own latest year is judged.
  const current = facts.data
    ? Math.max(0, ...facts.data.headline.map((name) => year(facts.data!.series[name].at(-1)) ?? 0)) || null
    : null

  return (
    <div className="px-6 py-5 space-y-7">
      <section aria-label="Price">
        <div className="flex items-baseline gap-3 mb-2">
          <h2 className="text-micro font-medium text-muted uppercase tracking-wider">
            Price, with every filing marked
          </h2>
          <span className="ml-auto flex gap-1">
            {YEARS.map((span) => (
              <button
                key={span}
                onClick={() => setYears(span)}
                aria-pressed={years === span}
                className={`px-2 py-0.5 text-tiny rounded-sm ${years === span
                  ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}
              >
                {span}y
              </button>
            ))}
          </span>
        </div>
        {prices.isLoading ? (
          <p className="text-small text-faint">Loading prices.</p>
        ) : prices.error ? (
          <p className="text-small text-danger">Could not load prices.</p>
        ) : (
          <PriceChart series={prices.data!} theme={theme} />
        )}
      </section>

      <section aria-label="Reported figures">
        <h2 className="text-micro font-medium text-muted uppercase tracking-wider mb-2">
          What the company reported, by financial year
        </h2>
        {facts.isLoading ? (
          <p className="text-small text-faint">Loading the filed statements.</p>
        ) : (facts.data?.headline.length ?? 0) === 0 ? (
          <p className="text-small text-faint">
            No figures have been extracted for {ticker}. A research run ingests them.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 2xl:grid-cols-5 gap-2">
              {facts.data!.headline.map((name) => (
                <Metric key={name} name={name} points={facts.data!.series[name]} current={current} />
              ))}
            </div>
            <p className="text-micro text-faint mt-2">
              Figures as the company filed them, by financial year. A card with a dashed border has no figure
              for the latest year and shows the last one reported. Outflows such as capital expenditure are
              negative. Hover a name for the exact line and filing it came from.
            </p>
          </>
        )}
      </section>
    </div>
  )
}
