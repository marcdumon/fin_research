import { useQuery } from '@tanstack/react-query'
import { getNote, percent, price } from '../../api'

// An assumption the filings do not report is a guess, however well argued, and a valuation resting
// on one is worth less than a valuation resting on the balance sheet. The prompt asks the model to say so
// in the rationale; this finds it having said so, and marks the number rather than leaving it to be read.
const ESTIMATED = /\b(assum|estimat|not (?:disclosed|reported|given)|no (?:cash|share|figure)|proxy|rough)/i

const SCENARIO_TONE: Record<string, string> = {
  bear: 'bg-down',
  base: 'bg-neutral',
  bull: 'bg-up',
}

export function Valuation({ ticker }: { ticker: string }) {
  const note = useQuery({ queryKey: ['note', ticker], queryFn: () => getNote(ticker), retry: false })

  if (note.isLoading) return <p className="p-6 text-small text-faint">Loading.</p>
  if (note.error) return <p className="p-6 text-small text-muted">No completed research for {ticker}.</p>

  const data = note.data!
  const valuation = data.valuation

  if (!valuation || valuation.scenarios.length === 0) {
    return (
      <div className="px-6 py-5 max-w-2xl">
        <p className="text-small text-muted">
          This run produced no valuation. The stage ran but returned nothing the schema accepts, which the
          run records rather than inventing a number to fill the gap.
        </p>
      </div>
    )
  }

  const current = valuation.current_price ?? null
  const values = valuation.scenarios.map((s) => s.value_per_share)
  const low = Math.min(...values, current ?? Infinity)
  const high = Math.max(...values, current ?? -Infinity)
  const span = high - low || 1
  const place = (value: number) => ((value - low) / span) * 100

  return (
    <div className="px-6 py-5 max-w-3xl space-y-7">
      <section aria-label="Scenarios">
        <div className="flex items-baseline gap-3 mb-4">
          <h2 className="text-micro font-medium text-muted uppercase tracking-wider">
            What it could be worth
          </h2>
          {data.expected_value != null && (
            <span className="text-small ml-auto">
              <span className="text-faint">expected </span>
              <span className="tabular-nums font-medium">{price(data.expected_value)}</span>
              {current != null && (
                <span className={`ml-2 tabular-nums ${data.expected_value >= current
                  ? 'text-up' : 'text-down'}`} title="Against the price on the day it was valued">
                  {signedGap(data.expected_value, current)}
                </span>
              )}
            </span>
          )}
        </div>

        <div className="relative h-20 mb-2">
          {valuation.scenarios.map((scenario) => (
            <div key={scenario.name} className="absolute top-0 -translate-x-1/2 text-center"
                 style={{ left: `${place(scenario.value_per_share)}%` }}>
              <div className="text-tiny text-muted">{scenario.name}</div>
              <div className="text-small tabular-nums font-medium">{price(scenario.value_per_share)}</div>
              <div className={`w-0.5 h-5 mx-auto mt-1 ${SCENARIO_TONE[scenario.name] ?? 'bg-neutral'}`} />
              <div className="text-micro text-faint mt-0.5">{percent(scenario.probability)}</div>
            </div>
          ))}
          {current != null && (
            <div className="absolute bottom-0 -translate-x-1/2 text-center"
                 style={{ left: `${place(current)}%` }}>
              <div className="w-px h-6 mx-auto bg-accent" />
              <div className="text-micro text-accent whitespace-nowrap">
                price {price(current)} on {data.as_of}
              </div>
            </div>
          )}
        </div>
        <div className="h-px bg-rule-strong" />

        <p className="text-tiny text-faint mt-6">
          <span className="text-muted">Method:</span> {valuation.method}
        </p>
        <p className="text-tiny text-faint mt-1">
          Compared with the price on {data.as_of}, the day it was valued, not today's.
        </p>
      </section>

      <section aria-label="Assumptions">
        <h2 className="text-micro font-medium text-muted uppercase tracking-wider mb-2">
          What it assumes
        </h2>
        <ul className="space-y-2.5">
          {valuation.assumptions.map((assumption) => {
            const guessed = ESTIMATED.test(assumption.rationale)
            return (
              <li key={assumption.name} className="text-small">
                <div className="flex items-baseline gap-2">
                  <span className="font-medium">{assumption.name}</span>
                  <span className="tabular-nums text-muted">
                    {assumption.value.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                    <span className="text-faint"> {assumption.unit}</span>
                  </span>
                  {guessed && (
                    <span className="text-micro text-positional border border-positional/50 px-1 rounded-sm"
                          title="the rationale says this was not taken from the statements">
                      estimated
                    </span>
                  )}
                </div>
                <p className="text-tiny text-muted mt-0.5">{assumption.rationale}</p>
              </li>
            )
          })}
        </ul>
      </section>
    </div>
  )
}

function signedGap(value: number, against: number): string {
  const gap = (value - against) / against
  return `${gap >= 0 ? '+' : ''}${(gap * 100).toFixed(0)}%`
}
