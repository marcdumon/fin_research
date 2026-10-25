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
  run_id?: string
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
export const listPendingApprovals = () => get<Approval[]>('/api/approvals')

export type ApprovalDecision = 'approved' | 'downgraded' | 'rejected'

// Checked, because a decision the server did not record looks exactly like one it did: the prompt
// disappears either way, and the run goes on waiting for an answer it never received.
export async function decide(approvalId: string, decision: ApprovalDecision): Promise<void> {
  const response = await fetch(`/api/approvals/${approvalId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
  if (!response.ok) throw new Error(`${response.status} recording the decision`)
}

// The gate prompt names a cheaper model when one exists. Pulling it out lets the button say which.
export function cheaperModel(prompt: string): string | null {
  return prompt.match(/Cheaper: ([^\s(]+)/)?.[1] ?? null
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

export interface SchemaCheck {
  schema: string
  model: string
  accepted: boolean
  detail: string
}

export async function checkSchemas(): Promise<SchemaCheck[]> {
  const response = await fetch('/api/schema-check', { method: 'POST' })
  if (!response.ok) throw new Error('the check could not run')
  return response.json() as Promise<SchemaCheck[]>
}

export interface Screen {
  name: string
  description: string
  requires: string[]
  excludes: string[]
  top: number
  notes: string
}

export interface Survivor {
  Ticker: string
  name: string | null
  sector: string | null
  market_cap: number | null
  earnings_yield: number | null
  fcf_yield: number | null
  roe: number | null
  gross_margin: number | null
  revenue_growth: number | null
  momentum: number | null
  score: number | null
}

export interface ScreenResult {
  screen: string
  as_of: string
  prices_as_of: string | null
  considered: number
  dropped: Record<string, number>
  survivors: Survivor[]
}

export interface VerifyResult {
  ticker: string
  trustworthy: boolean
  passed: number
  failed: number
  skipped: number
  results: { name: string; period_end: string; status: string; detail: string }[]
}

export const listScreens = () => get<Screen[]>('/api/screens')
export const getWatchlist = () => get<string[]>('/api/monitor/watchlist')
export const verifyStatements = (ticker: string) => get<VerifyResult>(`/api/verify/${ticker}?periods=4`)

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`${response.status} from ${path}`)
  return response.json() as Promise<T>
}

export const runScreen = (name: string) => post<ScreenResult>(`/api/screens/${name}/run`)
export const startPatrol = (tickers: string[] = []) => post<{ pid: number }>('/api/monitor/patrol', { tickers })
export const startFunnel = (screen: string, budgetUsd: number, limit: number) =>
  post<{ pid: number }>('/api/screens/funnel', { screen, budget_usd: budgetUsd, limit })

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

export function billions(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value / 1e9).toFixed(2)}`
}

// Company-shaped reads. The run ledger answers "what did the machine do"; these answer "what do I
// know about this company", which is the question the workbench is actually for.

export type VerdictName = 'buy' | 'watch' | 'pass' | 'reject'
export type Conviction = 'low' | 'medium' | 'high'

export interface Company {
  ticker: string
  name: string | null
  sector: string | null
  industry: string | null
  employees: number | null
  summary: string | null
  website: string | null
  cik: number | null
  price: number | null
  price_date: string | null
  change_pct: number | null
  runs: number
  latest_run_id: string | null
  latest_status: string | null
  researched_at: string | null
  spend_usd: number
  verdict: VerdictName | null
  conviction: Conviction | null
  reviewed_at: string | null
  watched: boolean
  open_alerts: number
  last_filed: string | null
  shares: number | null
  market_cap: number | null
}

export interface CompanyDetail extends Company {
  recent_filings: Filing[]
}

export interface Filing {
  accession: string
  form: string
  filed: string
  report_date: string | null
  url: string | null
  items: string[]
  sections: number
}

export interface PriceBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface PriceSeries {
  available: boolean
  bars: PriceBar[]
  markers: { time: string; form: string; accession: string }[]
}

export interface FactPoint {
  period_end: string
  value: number
  filed: string
  form: string
  tag: string
  meaning: string | null
}

export interface Fundamentals {
  headline: string[]
  series: Record<string, FactPoint[]>
}

export interface SourceExcerpt {
  source_id: string
  part: string
  form: string | null
  filed: string | null
  url: string | null
  accession: string
  excerpt: string
  span_start: number | null
  span_end: number | null
  total_chars: number
  in_range: boolean
  truncated_before?: boolean
  truncated_after?: boolean
}

export const listCompanies = () => get<Company[]>('/api/companies')
export const getCompany = (ticker: string) => get<CompanyDetail>(`/api/companies/${ticker}`)
export const getPrices = (ticker: string, years = 5) =>
  get<PriceSeries>(`/api/companies/${ticker}/prices?years=${years}`)
export const getFundamentals = (ticker: string) => get<Fundamentals>(`/api/companies/${ticker}/fundamentals`)
export const getFilings = (ticker: string) => get<Filing[]>(`/api/companies/${ticker}/filings`)
export const getSource = (sourceId: string, start?: number | null, end?: number | null) =>
  get<SourceExcerpt>(`/api/sources/${sourceId}`
    + (start != null && end != null ? `?start=${start}&end=${end}` : ''))
export const setVerdict = (ticker: string, body: {
  verdict: VerdictName; conviction?: Conviction | null; note?: string | null; run_id?: string | null
}) => post<{ ticker: string; verdict: string }>(`/api/companies/${ticker}/verdict`, body)

// Rounded before the sign is decided. A move of -0.03% used to print as a red '-0.0%', which says
// the price fell by nothing and colours it as a loss.
export function signed(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const rounded = Math.round(value * 1000) / 10
  if (rounded === 0) return '0.0%'
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)}%`
}

export function direction(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined) return 'flat'
  const rounded = Math.round(value * 1000) / 10
  return rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat'
}

export const TONE = { up: 'text-up', down: 'text-down', flat: 'text-faint' } as const

export function price(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(2)
}

export function marketCap(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1e12) return `${(value / 1e12).toFixed(2)}T`
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  return `${(value / 1e6).toFixed(0)}M`
}

// Figures come off the filings in full units. A note reads in the scale a person would say aloud.
export function figure(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const size = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (size >= 1e12) return `${sign}${(size / 1e12).toFixed(2)}T`
  if (size >= 1e9) return `${sign}${(size / 1e9).toFixed(2)}B`
  if (size >= 1e6) return `${sign}${(size / 1e6).toFixed(1)}M`
  if (size >= 1e3) return `${sign}${(size / 1e3).toFixed(1)}K`
  return `${sign}${size.toFixed(0)}`
}

export interface CitedClaim {
  claim_id: string
  text: string
  claim_type: 'fact' | 'numeric' | 'inference' | 'judgement'
  polarity: 'bull' | 'bear' | 'neutral'
  metric: string | null
  match_kind: 'exact' | 'whitespace' | 'unicode' | 'case' | 'positional' | 'none' | null
  citations: number[]
}

export interface Source {
  n: number
  kind: 'span' | 'fact' | 'market'
  source_id: string
  char_start: number | null
  char_end: number | null
  match_kind: string
  text: string
}

export interface Disagreement {
  question: string
  bull_position: string
  bear_position: string
  resolution: string | null
  resolved: boolean
}

export interface Assumption {
  name: string
  value: number
  unit: string
  rationale: string
}

export interface Scenario {
  name: 'bear' | 'base' | 'bull'
  value_per_share: number
  probability: number
  drivers: string[]
}

export interface Note {
  run_id: string
  ticker: string
  as_of: string
  thesis: string
  direction: 'constructive' | 'cautious' | 'neutral'
  confidence: 'low' | 'medium' | 'high'
  numbers: CitedClaim[]
  bull_case: CitedClaim[]
  bear_case: CitedClaim[]
  falsifiers: string[]
  theses: {
    stance: 'bull' | 'bear' | 'neutral'
    round: number
    summary: string
    falsifiers: string[]
    claims: string[]
  }[]
  disagreements: Disagreement[]
  valuation: {
    method: string
    assumptions: Assumption[]
    scenarios: Scenario[]
    current_price: number | null
  } | null
  expected_value: number | null
  groundedness: number | null
  checked: number | null
  dropped: number | null
  numeric: Record<string, number>
  match_kinds: Record<string, number>
  cost_usd: number | null
  sources: Source[]
}

export const getNote = (ticker: string) => get<Note>(`/api/companies/${ticker}/note`)
export const getRunNote = (runId: string) => get<Note>(`/api/runs/${runId}/note`)

export const jobStatus = (pid: number) =>
  get<{ pid: number; running: boolean; exit_code: number | null }>(`/api/jobs/${pid}`)

export interface TriageVerdict {
  ticker: string
  verdict: 'escalate' | 'pass' | 'reject'
  rationale: string
  strengths: string[]
  concerns: string[]
  open_questions: string[]
}

export interface TriageResult {
  run_id: string | null
  status: string | null
  spend_usd: number
  started_at: string | null
  verdicts: TriageVerdict[]
}

export const getTriage = (screen: string) => get<TriageResult>(`/api/screens/${screen}/triage`)
