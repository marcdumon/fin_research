import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'wouter'
import type { Company } from '../../api'

interface Action {
  id: string
  label: string
  hint: string
  run: () => void
}

// One entry point for every verb in the application. A dense screen has nowhere good to put a form,
// and a form permanently pinned to the navigation -- which is where the run form used to live -- puts the
// rarest action in the most valuable space.
export function CommandPalette({ open, onClose, companies, onResearch }: {
  open: boolean
  onClose: () => void
  companies: Company[]
  onResearch: (ticker: string) => void
}) {
  const [, navigate] = useLocation()
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  // Focus is a real synchronisation with the browser, so it belongs in an effect. Clearing the
  // query does not: the palette is remounted on open by its key, which starts it empty by construction.
  useEffect(() => { input.current?.focus() }, [])

  const actions = useMemo<Action[]>(() => {
    const typed = query.trim().toUpperCase()
    const matches = companies.filter((c) =>
      !typed || c.ticker.includes(typed) || (c.name ?? '').toUpperCase().includes(typed))

    const found: Action[] = matches.slice(0, 8).map((c) => ({
      id: `open-${c.ticker}`,
      label: c.ticker,
      hint: c.name ?? 'open',
      run: () => navigate(`/c/${c.ticker}`),
    }))

    // Offered only when what was typed could be a ticker and is not one already on file, so the
    // common case -- jumping to a company already researched -- never has a paid action above it.
    if (typed && /^[A-Z]{1,5}$/.test(typed) && !companies.some((c) => c.ticker === typed)) {
      found.push({
        id: `research-${typed}`,
        label: `Research ${typed}`,
        hint: 'asks before spending anything',
        run: () => onResearch(typed),
      })
    }

    found.push(
      { id: 'discover', label: 'Discover', hint: 'screen for new ideas', run: () => navigate('/discover') },
      { id: 'activity', label: 'Activity', hint: 'runs, cost, traces', run: () => navigate('/activity') },
    )
    return found
  }, [query, companies, navigate, onResearch])

  if (!open) return null

  // Clamped here rather than corrected by an effect: typing shortens the list, and a cursor past
  // the end for one render would be an out-of-range read before the effect could fix it.
  const at = Math.min(cursor, Math.max(actions.length - 1, 0))

  const choose = (action: Action) => {
    action.run()
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-ground/70 flex items-start justify-center pt-[12vh] px-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-lg bg-panel border border-rule-strong rounded shadow-2xl slide-in"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          ref={input}
          value={query}
          onChange={(event) => { setQuery(event.target.value); setCursor(0) }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') onClose()
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setCursor((c) => (c + 1) % actions.length)
            }
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              setCursor((c) => (c - 1 + actions.length) % actions.length)
            }
            if (event.key === 'Enter' && actions[at]) choose(actions[at])
          }}
          placeholder="Ticker, or a command"
          aria-label="Search companies and commands"
          className="w-full px-3.5 py-3 bg-transparent text-lede outline-none placeholder:text-faint
                     border-b border-rule"
        />
        <ul className="max-h-80 overflow-y-auto py-1">
          {actions.map((action, index) => (
            <li key={action.id}>
              <button
                onMouseEnter={() => setCursor(index)}
                onClick={() => choose(action)}
                className={`w-full flex items-baseline gap-3 px-3.5 py-1.5 text-left
                  ${index === at ? 'bg-raised' : ''}`}
              >
                <span className="font-medium text-small">{action.label}</span>
                <span className="text-tiny text-faint truncate">{action.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
