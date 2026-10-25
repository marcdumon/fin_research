import { useEffect, useRef, useState } from 'react'

// Every action that calls a paid model goes through this. Starting research used to be one keypress in
// the search box, with a two-dollar ceiling and the approval gate off; the form it replaced had the gate on
// by default and the ceiling in plain view. Spending money is a decision, so it is asked for as one: what will
// happen, what it can cost at most, and whether to be asked again before the expensive steps.
export function SpendConfirm({ title, detail, budget: initial, gated: gatedDefault = true, allowGating = true,
  fixed = false, confirmLabel = 'Start', onConfirm, onCancel }: {
  title: string
  detail: string
  budget: number
  gated?: boolean
  allowGating?: boolean
  /** The ceiling is set by the server, so it is stated rather than offered as a field it would ignore. */
  fixed?: boolean
  confirmLabel?: string
  onConfirm: (budget: number, gated: boolean) => void
  onCancel: () => void
}) {
  const [budget, setBudget] = useState(initial.toFixed(2))
  const [gated, setGated] = useState(gatedDefault)
  const field = useRef<HTMLInputElement>(null)
  // Set by the first key or pointer pressed inside the dialog, and required before it will submit.
  // Opening it from the search box with Enter used to confirm it with the same keystroke: the box acts on
  // the key going down, the dialog mounts and takes focus within that event, and the browser then delivers
  // the rest of the keystroke to the dialog's field, which submits the form. One press started a paid run
  // without the dialog ever being seen.
  const armed = useRef(false)
  const amount = Number(budget)
  const valid = Number.isFinite(amount) && amount > 0 && amount <= 20

  useEffect(() => { field.current?.select() }, [])

  return (
    <div className="fixed inset-0 z-50 bg-ground/70 flex items-start justify-center pt-[14vh] px-4"
         onClick={onCancel} role="presentation">
      <form
        role="dialog"
        aria-label={title}
        className="w-full max-w-md bg-panel border border-rule-strong rounded shadow-2xl p-4 slide-in"
        onClick={(event) => event.stopPropagation()}
        onPointerDown={() => { armed.current = true }}
        onKeyDown={(event) => {
          armed.current = true
          if (event.key === 'Escape') onCancel()
        }}
        onSubmit={(event) => {
          event.preventDefault()
          if (armed.current && valid) onConfirm(amount, gated)
        }}
      >
        <h2 className="text-base font-medium">{title}</h2>
        <p className="text-small text-muted mt-1">{detail}</p>

        {fixed ? (
          <p className="mt-4 text-small">
            <span className="text-muted">Spends at most </span>
            <span className="tabular-nums">${initial.toFixed(2)}</span>
          </p>
        ) : (
          <label className="flex items-center gap-2 mt-4 text-small">
            <span className="text-muted">Spend at most</span>
            <span className="text-faint">$</span>
            <input ref={field} value={budget} onChange={(event) => setBudget(event.target.value)}
                   inputMode="decimal" aria-label="Most it may spend, in dollars"
                   className="w-20 px-2 py-1 bg-ground border border-rule rounded-sm tabular-nums
                              focus:border-accent outline-none" />
            {!valid && <span className="text-tiny text-danger">between $0.01 and $20</span>}
          </label>
        )}

        {allowGating && (
          <label className="flex items-start gap-2 mt-3 text-small cursor-pointer">
            <input type="checkbox" checked={gated} onChange={(event) => setGated(event.target.checked)}
                   className="mt-1 accent-[var(--color-accent)]" />
            <span>
              Ask me before each expensive step
              <span className="block text-tiny text-faint">
                The valuation and the final note run on the most expensive model. You will be asked in the
                top bar before either starts.
              </span>
            </span>
          </label>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onCancel}
                  className="px-3 py-1 text-small border border-rule rounded-sm text-muted hover:text-ink">
            Cancel
          </button>
          <button type="submit" disabled={!valid}
                  className="px-3 py-1 text-small rounded-sm bg-accent text-ground disabled:opacity-40">
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  )
}
