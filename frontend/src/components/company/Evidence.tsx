import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { getSource, type Source } from '../../api'

// What was checked, said the way a reader would ask it. The verifier distinguishes four ways the
// quoted words can be found -- exactly, or allowing for spacing, typographic characters or capitals -- and
// to a reader all four mean the same thing: the words are in the filing. The distinction that matters is
// between that, a passage located by number that nothing corroborated, and a citation that failed.
const CHECK: Record<string, { label: string; tone: string; says: string }> = {
  exact: { label: 'Verified', tone: 'text-verified',
    says: 'The cited words appear in the filing. They are highlighted below.' },
  whitespace: { label: 'Verified', tone: 'text-verified',
    says: 'The cited words appear in the filing. They are highlighted below.' },
  unicode: { label: 'Verified', tone: 'text-verified',
    says: 'The cited words appear in the filing. They are highlighted below.' },
  case: { label: 'Verified', tone: 'text-verified',
    says: 'The cited words appear in the filing. They are highlighted below.' },
  positional: { label: 'Located, not checked', tone: 'text-positional',
    says: 'The agent pointed at this passage by number and quoted nothing from it, so nothing confirms it '
      + 'is the passage it meant. Read it before relying on the claim.' },
  none: { label: 'Not verified', tone: 'text-unverified',
    says: 'The cited words were not found in this filing.' },
}

export function Evidence({ source, onClose, onStep, count }: {
  source: Source | null
  onClose: () => void
  onStep: (by: number) => void
  count: number
}) {
  const isText = source?.kind === 'span'
  const excerpt = useQuery({
    queryKey: ['source', source?.source_id, source?.char_start, source?.char_end],
    queryFn: () => getSource(source!.source_id, source!.char_start, source!.char_end),
    enabled: source !== null && isText,
  })

  useEffect(() => {
    if (!source) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') { event.preventDefault(); onStep(1) }
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') { event.preventDefault(); onStep(-1) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [source, onClose, onStep])

  if (!source) return null

  const check = isText ? (CHECK[source.match_kind] ?? CHECK.none) : null
  const found = excerpt.data

  return (
    // Fixed to the window rather than placed in the page. It used to sit at the top of the note's
    // row, so a citation clicked at the bottom of a long note opened a panel scrolled out of sight.
    <aside
      aria-label={`Source ${source.n}`}
      className="fixed right-0 top-11 bottom-0 z-40 w-full sm:w-[32rem] border-l border-rule-strong
                 bg-panel shadow-2xl flex flex-col slide-in"
    >
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-rule">
        <span className="font-mono text-small text-accent">[{source.n}]</span>
        {check
          ? <span className={`text-small font-medium ${check.tone}`}>{check.label}</span>
          : <span className="text-small font-medium text-muted">Reported figure</span>}
        <span className="ml-auto flex items-center gap-1 text-tiny text-faint">
          <button onClick={() => onStep(-1)} aria-label="Previous source"
                  className="px-1.5 hover:text-ink">↑</button>
          {source.n} of {count}
          <button onClick={() => onStep(1)} aria-label="Next source"
                  className="px-1.5 hover:text-ink">↓</button>
        </span>
        <button onClick={onClose} aria-label="Close source" className="text-muted hover:text-ink px-1">✕</button>
      </div>

      {!isText ? (
        <div className="px-4 py-3 space-y-2">
          <p className="text-small">
            A figure the company reported in its filed statements, under the line{' '}
            <span className="font-medium">{source.source_id}</span>.
          </p>
          {source.text && <p className="text-small text-muted">As the claim put it: “{source.text}”</p>}
          <p className="text-tiny text-faint">
            There is no passage of text to show. The figure itself was compared with what the company reported.
          </p>
        </div>
      ) : excerpt.isLoading ? (
        <p className="px-4 py-3 text-small text-faint">Opening the filing.</p>
      ) : excerpt.error || !found ? (
        <p className="px-4 py-3 text-small text-danger">
          The filing this citation names is no longer in the store, so it cannot be shown.
        </p>
      ) : (
        <>
          <div className="px-4 py-2.5 border-b border-rule space-y-1">
            <p className="text-small">
              <span className="font-medium">{found.form ?? 'Filing'}</span>
              {found.filed && <span className="text-muted"> filed {found.filed}</span>}
              <span className="text-muted"> · {found.part}</span>
            </p>
            {check && <p className="text-tiny text-muted">{check.says}</p>}
          </div>

          {found.in_range ? (
            <div className="flex-1 overflow-y-auto px-4 py-3 text-small leading-relaxed whitespace-pre-wrap">
              {found.truncated_before && <span className="text-faint">… </span>}
              <span className="text-muted">
                {found.span_start != null ? found.excerpt.slice(0, found.span_start) : found.excerpt}
              </span>
              {found.span_start != null && found.span_end != null && (
                <mark ref={(node) => node?.scrollIntoView({ block: 'center' })}
                      className="bg-accent/25 text-ink px-0.5 rounded-sm">
                  {found.excerpt.slice(found.span_start, found.span_end)}
                </mark>
              )}
              {found.span_end != null && (
                <span className="text-muted">{found.excerpt.slice(found.span_end)}</span>
              )}
              {found.truncated_after && <span className="text-faint"> …</span>}
            </div>
          ) : (
            <p className="px-4 py-3 text-small text-danger">
              This citation points past the end of the text it names, so it cannot be shown. It is reported as
              broken rather than matched to the nearest passage.
            </p>
          )}

          {found.url && (
            <a href={found.url} target="_blank" rel="noreferrer"
               className="px-4 py-2 border-t border-rule text-tiny text-accent hover:underline">
              Open the full filing at sec.gov ↗
            </a>
          )}
        </>
      )}
    </aside>
  )
}
