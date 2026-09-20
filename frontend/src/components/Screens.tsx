import { useEffect, useState } from 'react'
import {
  billions, listScreens, percent, runScreen, startFunnel,
  type Screen, type ScreenResult,
} from '../api'

// Screening reads no filings and calls no model, so it answers in about a second over a couple of thousand
// companies. The funnel that follows reads each survivor and does cost money, which is why it is a separate,
// explicit action rather than something that happens automatically after a screen.
export function Screens({ onResearch }: { onResearch: (ticker: string) => void }) {
  const [screens, setScreens] = useState<Screen[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [result, setResult] = useState<ScreenResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [funnelStarted, setFunnelStarted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listScreens().then((found) => {
      setScreens(found)
      setSelected((current) => current ?? found[0]?.name ?? null)
    }).catch(() => setError('no screens are defined'))
  }, [])

  async function run(name: string) {
    setSelected(name)
    setBusy(true)
    setError(null)
    setResult(null)
    setFunnelStarted(false)
    try {
      setResult(await runScreen(name))
    } catch {
      setError('the screen could not run. It needs the market database.')
    } finally {
      setBusy(false)
    }
  }

  const screen = screens.find((s) => s.name === selected)

  return (
    <div className="max-w-[64rem]">
      <h2 className="text-2xl font-semibold tracking-tight">Screens</h2>
      <p className="mt-1 text-muted text-[15px]">
        Numbers only, as they stood on the day. Nothing here reads a filing, so it is free.
      </p>

      <div className="mt-5 flex flex-wrap gap-2">
        {screens.map((s) => (
          <button
            key={s.name}
            onClick={() => run(s.name)}
            aria-current={s.name === selected ? 'true' : undefined}
            className={`px-3 py-1.5 text-[13.5px] border ${
              s.name === selected ? 'border-ink bg-surface font-medium' : 'border-rule hover:bg-surface/60'
            }`}
          >
            {s.name.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {screen ? (
        <div className="mt-4 text-[14px]">
          <p>{screen.description}</p>
          <p className="mt-1 text-[13px] text-muted">
            {screen.requires.join(' · ')}
            {screen.excludes.length ? ` · excludes ${screen.excludes.join(', ')}` : ''}
          </p>
          {screen.notes ? <p className="mt-1 text-[13px] text-muted">{screen.notes}</p> : null}
        </div>
      ) : null}

      {error ? <p className="mt-4 text-[14px] text-expensive">{error}</p> : null}
      {busy ? <p className="mt-4 text-muted text-[15px]">Screening.</p> : null}

      {result ? (
        <>
          <div className="mt-6 flex items-baseline justify-between gap-4 flex-wrap border-b border-rule pb-2">
            <p className="text-[14px]">
              {result.considered.toLocaleString()} considered, {result.survivors.length} survive
            </p>
            <button
              onClick={async () => {
                await startFunnel(result.screen, 1.0, 20)
                setFunnelStarted(true)
              }}
              disabled={funnelStarted || !result.survivors.length}
              className="px-3 py-1 text-[13px] bg-ink text-surface disabled:opacity-40"
            >
              {funnelStarted ? 'Reading the survivors' : 'Read the survivors'}
            </button>
          </div>

          {funnelStarted ? (
            <p className="mt-2 text-[13px] text-muted">
              Each survivor is being read and judged. This costs about a third of a cent per company and
              takes a few minutes.
            </p>
          ) : null}

          <details className="mt-3">
            <summary className="text-[13px] text-muted cursor-pointer">Where the universe went</summary>
            <ul className="mt-1.5 text-[13px] text-muted">
              {Object.entries(result.dropped).map(([reason, count]) => (
                <li key={reason}>−{count.toLocaleString()} {reason}</li>
              ))}
            </ul>
          </details>

          <table className="mt-4 w-full text-[13.5px] border-collapse">
            <thead>
              <tr className="text-left text-muted border-b border-rule">
                <th className="font-normal py-1.5 pr-3">Company</th>
                <th className="font-normal py-1.5 pr-3 hidden sm:table-cell">Sector</th>
                <th className="font-normal py-1.5 pr-3 text-right">$bn</th>
                <th className="font-normal py-1.5 pr-3 text-right">Earnings yield</th>
                <th className="font-normal py-1.5 pr-3 text-right hidden sm:table-cell">Cash yield</th>
                <th className="font-normal py-1.5 pr-3 text-right hidden sm:table-cell">Return on equity</th>
                <th className="font-normal py-1.5" />
              </tr>
            </thead>
            <tbody>
              {result.survivors.map((s) => (
                <tr key={s.Ticker} className="border-b border-rule/60">
                  <td className="py-1.5 pr-3">
                    <span className="font-medium">{s.Ticker}</span>
                    <span className="block text-[12px] text-muted">{s.name ?? ''}</span>
                  </td>
                  <td className="py-1.5 pr-3 text-muted hidden sm:table-cell">{s.sector ?? ''}</td>
                  <td className="py-1.5 pr-3 text-right">{billions(s.market_cap)}</td>
                  <td className="py-1.5 pr-3 text-right">{percent(s.earnings_yield)}</td>
                  <td className="py-1.5 pr-3 text-right hidden sm:table-cell">{percent(s.fcf_yield)}</td>
                  <td className="py-1.5 pr-3 text-right hidden sm:table-cell">{percent(s.roe)}</td>
                  <td className="py-1.5 text-right">
                    <button onClick={() => onResearch(s.Ticker)}
                            className="text-[12.5px] text-link underline underline-offset-2">
                      Research
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  )
}
