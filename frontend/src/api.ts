// Shapes the Python side returns. Kept narrow on purpose: the dashboard reads the run ledger and writes
// nothing except an approval decision.

export type Tier = 'cheap' | 'mid' | 'expensive'

export interface Run {
  run_id: string
  ticker: string
  status: string
  as_of: string
  budget_usd: number
  spend_usd: number
  started_at: string
  finished_at: string | null
  calls: number
}

export interface AgentCost {
  agent: string
  tier: Tier
  calls: number
  cost_usd: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  cache_hits: number | null
}

export interface Task {
  task_id: string
  agent: string
  stage: number
  status: string
  cost_usd: number
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface Call {
  call_id: string
  agent: string
  tier: Tier
  model: string
  cost_usd: number
  prompt_tokens: number | null
  completion_tokens: number | null
  latency_ms: number | null
  cache_hit: number
  parse_mode: string | null
  retries: number
  error: string | null
  created_at: string
}

export interface Snapshot {
  run_id: string
  ticker: string
  status: string
  budget_usd: number
  spend_usd: number
  remaining_usd: number
  calls: number
  started_at: string
  finished_at: string | null
  by_agent: AgentCost[]
  tasks: Task[]
}

export interface Approval {
  approval_id: string
  subject: string
  prompt: string
  status: string
  requested_at: string
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${response.status} from ${path}`)
  return response.json() as Promise<T>
}

export const listRuns = () => get<Run[]>('/api/runs')
export const getRun = (id: string) => get<Snapshot>(`/api/runs/${id}`)
export const getCalls = (id: string) => get<Call[]>(`/api/runs/${id}/calls`)
export const listApprovals = (id: string) => get<Approval[]>(`/api/approvals?run_id=${id}`)

export async function decide(approvalId: string, approved: boolean): Promise<void> {
  await fetch(`/api/approvals/${approvalId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved }),
  })
}

export const isRunning = (status: string) => status === 'running'

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  // Sub-cent amounts are the normal case on the cheap tier, so four places rather than two.
  return value >= 1 ? `$${value.toFixed(2)}` : `$${value.toFixed(4)}`
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export interface MonitorAlert {
  ticker: string
  check_name: string
  severity: 'high' | 'medium' | 'low'
  headline: string
  detail: string | null
  reference: string | null
  as_of: string
  created_at: string
}

export const listAlerts = () => get<MonitorAlert[]>('/api/alerts?limit=60')

export async function acknowledgeAlerts(ticker?: string): Promise<void> {
  const query = ticker ? `?ticker=${encodeURIComponent(ticker)}` : ''
  await fetch(`/api/alerts/acknowledge${query}`, { method: 'POST' })
}

export async function startRun(ticker: string, budgetUsd: number, gateTiers: string[]): Promise<string | null> {
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, budget_usd: budgetUsd, gate_tiers: gateTiers }),
  })
  if (!response.ok) throw new Error((await response.json()).detail ?? 'could not start the run')
  return (await response.json()).run_id
}
