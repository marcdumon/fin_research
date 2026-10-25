import { useQuery } from '@tanstack/react-query'
import { getNote } from '../../api'
import { CitedText } from './citations'
import { Evidence } from './Evidence'
import { useEvidence } from './useEvidence'

const STANCE: Record<string, { title: string; text: string; rule: string }> = {
  bull: { title: 'Constructive', text: 'text-constructive', rule: 'border-constructive' },
  bear: { title: 'Sceptical', text: 'text-cautious', rule: 'border-cautious' },
  neutral: { title: 'Neither side', text: 'text-neutral', rule: 'border-neutral' },
}

export function Debate({ ticker }: { ticker: string }) {
  const note = useQuery({ queryKey: ['note', ticker], queryFn: () => getNote(ticker), retry: false })
  const evidence = useEvidence(note.data?.sources ?? [])

  if (note.isLoading) return <p className="p-6 text-small text-faint">Loading.</p>
  if (note.error || !note.data) {
    return <p className="p-6 text-small text-muted">There is no completed research for {ticker}.</p>
  }

  const theses = note.data.theses
  if (theses.length === 0) {
    return (
      <p className="px-6 py-5 text-small text-muted max-w-2xl">
        Neither side produced an argument in this run. What each stage returned is under Runs.
      </p>
    )
  }

  return (
    <div className={`px-6 py-5 ${evidence.source ? 'sm:pr-[34rem]' : ''}`}>
      <div className="max-w-3xl space-y-6">
        <p className="text-tiny text-faint">
          The constructive case, then the sceptical case written after reading it, then one reply from the
          constructive side. Three exchanges and no more.
        </p>

        {theses.map((thesis, index) => {
          const style = STANCE[thesis.stance] ?? STANCE.neutral
          return (
            <section key={index} className={`border-l-2 pl-4 ${style.rule}`} aria-label={style.title}>
              <div className="flex items-baseline gap-2">
                <h2 className={`text-small font-medium ${style.text}`}>{style.title}</h2>
                {thesis.round > 1 && <span className="text-micro text-faint">reply</span>}
              </div>
              <p className="text-small text-muted mt-1.5">
                <CitedText text={thesis.summary} onCite={evidence.open} />
              </p>

              {thesis.claims.length > 0 && (
                <ul className="mt-2.5 space-y-1.5">
                  {thesis.claims.map((claim, n) => (
                    <li key={n} className="text-small flex gap-2">
                      <span className="text-faint select-none">·</span>
                      <span><CitedText text={claim} onCite={evidence.open} /></span>
                    </li>
                  ))}
                </ul>
              )}

              {thesis.falsifiers.length > 0 && (
                <div className="mt-3">
                  <h3 className="text-micro text-faint uppercase tracking-wider">This side is wrong if</h3>
                  <ul className="mt-1 space-y-1">
                    {thesis.falsifiers.map((falsifier, n) => (
                      <li key={n} className="text-tiny text-muted flex gap-2">
                        <span className="text-faint select-none font-mono">☐</span>
                        <span>{falsifier}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )
        })}
      </div>

      <Evidence source={evidence.source} onClose={evidence.close} onStep={evidence.step} count={evidence.count} />
    </div>
  )
}
