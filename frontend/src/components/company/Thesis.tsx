import { useQuery } from '@tanstack/react-query'
import { getNote, type CitedClaim, type Note } from '../../api'
import { CitedText } from './citations'
import { Evidence } from './Evidence'
import { useEvidence } from './useEvidence'

const DIRECTION: Record<string, string> = {
  constructive: 'text-constructive border-constructive',
  cautious: 'text-cautious border-cautious',
  neutral: 'text-neutral border-neutral',
}

// The verifier's categories, in a reader's words. A fact or a figure is the default and needs no tag;
// the tag marks the claims that are the agent's own reasoning or opinion rather than what the filing says.
const KIND: Record<string, string> = { inference: 'reasoning', judgement: 'opinion' }

function Flag({ kind }: { kind: CitedClaim['match_kind'] }) {
  if (kind === 'positional') {
    return <span className="text-micro text-positional ml-1.5"
                 title="Located by passage number only; the wording was not checked">not checked</span>
  }
  if (kind === 'none') return <span className="text-micro text-unverified ml-1.5">not verified</span>
  return null
}

function Claims({ claims, onCite, empty }: { claims: CitedClaim[]; onCite: (n: number) => void; empty: string }) {
  if (claims.length === 0) return <p className="text-small text-faint">{empty}</p>
  return (
    <ul className="space-y-1.5">
      {claims.map((claim) => (
        <li key={claim.claim_id} className="text-small flex gap-2">
          <span className="text-faint select-none">·</span>
          <span>
            <CitedText text={claim.text} onCite={onCite} />
            {claim.citations.map((n) => (
              <button key={n} className="cite ml-0.5" onClick={() => onCite(n)}
                      aria-label={`Show source ${n}`}>[{n}]</button>
            ))}
            {KIND[claim.claim_type] && (
              <span className="text-micro text-faint ml-1.5">{KIND[claim.claim_type]}</span>
            )}
            <Flag kind={claim.match_kind} />
          </span>
        </li>
      ))}
    </ul>
  )
}

// Said in counts rather than as a percentage, and only when the run recorded them. A share computed
// here from the claims that survived would be 100% by construction, which is what this line used to say.
function Checked({ note }: { note: Note }) {
  if (note.groundedness == null || note.checked == null) {
    return <span>verification figures not recorded for this run</span>
  }
  const kept = note.checked - (note.dropped ?? 0)
  const figures = note.numeric ?? {}
  const agreed = figures.confirmed ?? 0
  return (
    <span>
      {kept} of {note.checked} checkable claims verified against the filings
      {note.dropped ? `, ${note.dropped} dropped` : ''}
      {agreed ? ` · ${agreed} figures matched the filed statements` : ''}
    </span>
  )
}

function Heading({ children, tone = 'text-muted' }: { children: string; tone?: string }) {
  return <h2 className={`text-micro font-medium mb-2 uppercase tracking-wider ${tone}`}>{children}</h2>
}

export function Thesis({ ticker }: { ticker: string }) {
  const note = useQuery({ queryKey: ['note', ticker], queryFn: () => getNote(ticker), retry: false })
  const evidence = useEvidence(note.data?.sources ?? [])

  if (note.isLoading) return <p className="p-6 text-small text-faint">Loading the note.</p>

  if (note.error || !note.data) {
    return (
      <p className="p-6 max-w-xl text-small text-muted">
        There is no completed research for {ticker}. Press <kbd className="font-mono text-tiny">⌘K</kbd> and
        choose Research to produce a note.
      </p>
    )
  }

  const data = note.data
  const cite = evidence.open

  return (
    <div className={`px-6 py-5 ${evidence.source ? 'sm:pr-[34rem]' : ''}`}>
      <div className="max-w-3xl space-y-7">
        <section aria-label="Verdict">
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`px-2 py-0.5 text-small border rounded-sm ${DIRECTION[data.direction]}`}>
              {data.direction}
            </span>
            <span className="text-small text-muted">{data.confidence} confidence</span>
          </div>
          <p className="text-tiny text-faint mt-1.5">
            Written {data.as_of} · <Checked note={data} />
          </p>
          <p className="text-lede mt-3 leading-relaxed"><CitedText text={data.thesis} onCite={cite} /></p>
        </section>

        <section aria-label="What the numbers say">
          <Heading>What the numbers say</Heading>
          <Claims claims={data.numbers} onCite={cite} empty="No financial claim survived verification." />
        </section>

        <div className="grid md:grid-cols-2 gap-6">
          <section aria-label="The constructive case">
            <Heading tone="text-constructive">Constructive</Heading>
            <Claims claims={data.bull_case} onCite={cite} empty="Nothing supported by the evidence." />
          </section>
          <section aria-label="The sceptical case">
            <Heading tone="text-cautious">Sceptical</Heading>
            <Claims claims={data.bear_case} onCite={cite} empty="Nothing supported by the evidence." />
          </section>
        </div>

        {data.falsifiers.length > 0 && (
          <section aria-label="What would make this wrong">
            <Heading>What would make this wrong</Heading>
            <ul className="space-y-1.5">
              {data.falsifiers.map((falsifier, index) => (
                <li key={index} className="text-small flex gap-2">
                  <span className="text-faint select-none font-mono text-tiny">☐</span>
                  <span>{falsifier}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {data.disagreements.length > 0 && (
          <section aria-label="Where the two sides differ">
            <Heading>Where the two sides differ</Heading>
            <div className="space-y-4">
              {data.disagreements.map((item, index) => (
                <div key={index} className="border-l-2 border-rule-strong pl-3">
                  <p className="text-small font-medium">{item.question}</p>
                  <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-small">
                    <dt className="text-constructive text-tiny">for</dt>
                    <dd className="text-muted"><CitedText text={item.bull_position} onCite={cite} /></dd>
                    <dt className="text-cautious text-tiny">against</dt>
                    <dd className="text-muted"><CitedText text={item.bear_position} onCite={cite} /></dd>
                  </dl>
                  <p className={`text-tiny mt-1.5 ${item.resolved ? 'text-ink' : 'text-faint'}`}>
                    {item.resolved && item.resolution
                      ? <CitedText text={item.resolution} onCite={cite} />
                      : 'Not settled by the evidence available.'}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        <section aria-label="Sources" className="border-t border-rule pt-4">
          <Heading>{`Sources · ${data.sources.length}`}</Heading>
          <ol className="space-y-1">
            {data.sources.map((source) => (
              <li key={source.n} className="text-tiny flex gap-2">
                <button className="cite shrink-0" onClick={() => cite(source.n)}>[{source.n}]</button>
                <span className="text-muted truncate">{source.text}</span>
              </li>
            ))}
          </ol>
          <p className="text-tiny text-faint mt-3">
            A claim whose cited words could not be found in the filing was dropped before the note was written.
            Verification confirms the filing says what is claimed, not that the claim is right.
          </p>
        </section>
      </div>

      <Evidence source={evidence.source} onClose={evidence.close} onStep={evidence.step} count={evidence.count} />
    </div>
  )
}
