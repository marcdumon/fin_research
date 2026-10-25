import { useEffect, useState } from 'react'
import { Link, useLocation } from 'wouter'
import { applyTheme, storedTheme, type Theme } from '../../lib/theme'
import { Approvals } from './Approvals'

function Moon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M13.5 9.6A6 6 0 0 1 6.4 2.5a6 6 0 1 0 7.1 7.1Z" fill="currentColor" />
    </svg>
  )
}

function Sun() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="3.1" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
        <path d="M8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.2 1.2M11.8 11.8 13 13M13 3l-1.2 1.2M4.2 11.8 3 13" />
      </g>
    </svg>
  )
}

const TABS = [
  ['/', 'Watchlist'],
  ['/discover', 'Discover'],
  ['/activity', 'Activity'],
] as const

export function TopBar({ onCommand, liveRuns }: { onCommand: () => void; liveRuns: number }) {
  const [location] = useLocation()
  const [theme, setTheme] = useState<Theme>(storedTheme)

  useEffect(() => applyTheme(theme), [theme])

  return (
    <header className="h-11 shrink-0 flex items-center gap-1 px-3 border-b border-rule bg-panel">
      <Link href="/" className="font-mono text-small tracking-tight text-muted hover:text-ink mr-3">
        fin<span className="text-accent">_</span>research
      </Link>

      <nav className="flex items-center gap-0.5">
        {TABS.map(([href, label]) => {
          const active = href === '/' ? location === '/' || location.startsWith('/c/') : location.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={`px-2.5 py-1 text-small rounded-sm ${active
                ? 'text-ink bg-raised'
                : 'text-muted hover:text-ink'}`}
            >
              {label}
            </Link>
          )
        })}
      </nav>

      <div className="flex-1" />

      <Approvals />

      {liveRuns > 0 && (
        <span className="flex items-center gap-1.5 text-tiny text-accent mr-3" aria-live="polite">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" aria-hidden="true" />
          {liveRuns} running
        </span>
      )}

      <button
        onClick={onCommand}
        className="flex items-center gap-2 px-2.5 py-1 text-small text-muted border border-rule
                   rounded-sm hover:text-ink hover:border-rule-strong"
      >
        Search
        <kbd className="font-mono text-micro text-faint">⌘K</kbd>
      </button>

      <div className="ml-2 flex items-center border border-rule rounded-sm overflow-hidden"
           role="group" aria-label="Theme">
        {(['dark', 'light'] as const).map((name) => (
          <button
            key={name}
            onClick={() => setTheme(name)}
            aria-pressed={theme === name}
            title={`${name === 'dark' ? 'Dark' : 'Light'} theme`}
            className={`flex items-center gap-1.5 px-2 py-1 text-tiny ${theme === name
              ? 'bg-raised text-ink'
              : 'text-faint hover:text-muted'}`}
          >
            {name === 'dark' ? <Moon /> : <Sun />}
            <span className="hidden sm:inline">{name === 'dark' ? 'Dark' : 'Light'}</span>
          </button>
        ))}
      </div>
    </header>
  )
}
