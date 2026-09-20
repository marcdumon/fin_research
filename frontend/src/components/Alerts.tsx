import { useState } from 'react'
import { acknowledgeAlerts, type MonitorAlert } from '../api'

function PatrolButton({ onPatrol }: { onPatrol: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      onClick={async () => { setBusy(true); await onPatrol(); setBusy(false) }}
      disabled={busy}
      className="px-3 py-1 text-[13px] border border-rule hover:bg-surface disabled:opacity-40"
    >
      {busy ? 'Checking' : 'Check for changes'}
    </button>
  )
}

// What the monitor noticed. Severity is carried by weight and a rule rather than by a third colour scale:
// the tier ramp already owns green-amber-red on this page, and a second ramp in the same hues would read as
// though a high-severity alert were an expensive one.
const SEVERITY: Record<MonitorAlert['severity'], { rule: string; text: string; label: string }> = {
  // Only the top level gets colour and a heavy rule. A three-step ramp here competed with the tier ramp and
  // left the one alert that mattered reading the same as three routine earnings releases beside it.
  high: { rule: 'border-l-4 border-expensive bg-surface', text: 'font-medium', label: 'look now' },
  medium: { rule: 'border-l-2 border-rule', text: '', label: 'worth a look' },
  low: { rule: 'border-l-2 border-rule', text: 'text-muted', label: 'noted' },
}

const RANK: Record<MonitorAlert['severity'], number> = { high: 0, medium: 1, low: 2 }

const CHECK_WORDING: Record<string, string> = {
  new_filings: 'filing',
  material_events: 'announcement',
  price_move: 'price',
  screen_membership: 'screen',
  statements_reconcile: 'accounts',
}

export function Alerts({ alerts, onAcknowledged, onPatrol }: {
  alerts: MonitorAlert[]
  onAcknowledged: () => void
  onPatrol: () => Promise<void>
}) {
  if (!alerts.length) {
    return (
      <div className="max-w-[48rem]">
        <h2 className="text-2xl font-semibold tracking-tight">Nothing has changed</h2>
        <p className="mt-2 mb-3 text-muted">
          The monitor has raised nothing you have not already seen. Check again:
        </p>
        <PatrolButton onPatrol={onPatrol} />
      </div>
    )
  }

  const byTicker = alerts.reduce<Record<string, MonitorAlert[]>>((grouped, alert) => {
    ;(grouped[alert.ticker] ??= []).push(alert)
    return grouped
  }, {})

  return (
    <div className="max-w-[52rem]">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-2xl font-semibold tracking-tight">
          {alerts.length} {alerts.length === 1 ? 'change' : 'changes'}
        </h2>
        <span className="flex gap-2">
          <PatrolButton onPatrol={onPatrol} />
          <button
            onClick={async () => { await acknowledgeAlerts(); onAcknowledged() }}
            className="px-3 py-1 text-[13px] border border-rule hover:bg-surface"
          >
            Mark all seen
          </button>
        </span>
      </div>

      <div className="mt-8 space-y-8">
        {Object.entries(byTicker).map(([ticker, group]) => (
          <section key={ticker} aria-label={ticker}>
            <div className="flex items-baseline justify-between gap-3 border-b border-rule pb-1.5">
              <h3 className="font-medium">{ticker}</h3>
              <button
                onClick={async () => { await acknowledgeAlerts(ticker); onAcknowledged() }}
                className="text-[12.5px] text-muted hover:text-ink"
              >
                Mark seen
              </button>
            </div>
            <ul>
              {[...group].sort((a, b) => RANK[a.severity] - RANK[b.severity]).map((alert, index) => {
                const style = SEVERITY[alert.severity]
                return (
                  <li key={`${alert.check_name}-${index}`}
                      className={`${style.rule} pl-3 py-2.5 border-b border-rule/50`}>
                    <div className="flex items-baseline justify-between gap-3 flex-wrap">
                      <span className={`text-[14.5px] ${style.text}`}>{alert.headline}</span>
                      <span className="text-[12px] text-muted shrink-0">
                        {CHECK_WORDING[alert.check_name] ?? alert.check_name} · {style.label}
                      </span>
                    </div>
                    {alert.detail ? (
                      <p className="mt-0.5 text-[13px] text-muted">{alert.detail}</p>
                    ) : null}
                    {alert.reference ? (
                      <a href={alert.reference} target="_blank" rel="noreferrer"
                         className="mt-1 inline-block text-[12.5px] text-link underline underline-offset-2">
                        Read the filing
                      </a>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}
