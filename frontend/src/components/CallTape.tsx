import { clockTime, compact, money, type Call, type Tier } from '../api'

const TIER_MARK: Record<Tier, string> = {
  cheap: 'var(--color-cheap)',
  mid: 'var(--color-mid)',
  expensive: 'var(--color-expensive)',
}

// Every model call, newest first. A call that arrived during this session is marked once as it lands, then
// settles -- motion that reports something changed, rather than decoration on every row.
export function CallTape({ calls, justLanded }: { calls: Call[]; justLanded: Set<string> }) {
  if (!calls.length) {
    return <p className="text-muted text-[15px]">No calls yet. They appear here as the run makes them.</p>
  }

  return (
    <table className="w-full text-[13.5px] border-collapse">
      <thead>
        <tr className="text-left text-muted border-b border-rule">
          <th className="font-normal py-1.5 pr-3">Time</th>
          <th className="font-normal py-1.5 pr-3">Agent</th>
          <th className="font-normal py-1.5 pr-3 hidden sm:table-cell">Model</th>
          <th className="font-normal py-1.5 pr-3 text-right">Tokens</th>
          <th className="font-normal py-1.5 pr-3 text-right hidden sm:table-cell">Latency</th>
          <th className="font-normal py-1.5 text-right">Cost</th>
        </tr>
      </thead>
      <tbody>
        {calls.map((call) => (
          <tr key={call.call_id}
              className={`border-b border-rule/60 ${justLanded.has(call.call_id) ? 'landed' : ''}`}>
            <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{clockTime(call.created_at)}</td>
            <td className="py-1.5 pr-3">
              <span className="inline-flex items-baseline gap-2">
                <span aria-hidden className="inline-block w-2 h-2 translate-y-px shrink-0"
                      style={{ background: TIER_MARK[call.tier] }} />
                <span>{call.agent}</span>
                {call.cache_hit ? <span className="text-muted text-[12px]">replayed</span> : null}
                {call.retries > 0 ? <span className="text-mid text-[12px]">retried</span> : null}
                {call.parse_mode === 'failed' ? <span className="text-expensive text-[12px]">unparsed</span> : null}
              </span>
              {call.error ? <div className="text-expensive text-[12.5px] mt-0.5">{call.error}</div> : null}
            </td>
            <td className="py-1.5 pr-3 font-mono text-[12px] text-muted hidden sm:table-cell">{call.model}</td>
            <td className="py-1.5 pr-3 text-right text-muted whitespace-nowrap">
              {compact(call.prompt_tokens)}<span className="text-rule mx-1">/</span>{compact(call.completion_tokens)}
            </td>
            <td className="py-1.5 pr-3 text-right text-muted hidden sm:table-cell">
              {call.latency_ms ? `${(call.latency_ms / 1000).toFixed(1)}s` : '—'}
            </td>
            <td className="py-1.5 text-right whitespace-nowrap">{money(call.cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
