import { useEffect, useState } from 'react'
import { money, type Task } from '../api'

function elapsed(startedAt: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(startedAt).getTime()) / 1000))
  if (seconds < 90) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

// Elapsed time is computed while rendering, and events only arrive when a task changes state. Without a
// clock of its own the figure froze at whatever it was when the task started, so a step running for ten
// minutes read "working 1s" the whole time.
function useTick(active: boolean): void {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [active])
}

// Numbered because the route genuinely is a sequence: readers first, then the statements, then a debate that
// only makes sense in order, then the write-up. Not numbered for decoration.
const STAGE_NAMES: Record<number, string> = {
  0: 'Fetch the filings',
  1: 'Read the filing',
  2: 'Read the statements',
  3: 'Argue the constructive case',
  4: 'Argue the sceptical case',
  5: 'Rebut, and value',
  6: 'Write the note',
}

const STATUS_STYLE: Record<string, string> = {
  running: 'text-link',
  done: 'text-ink',
  failed: 'text-expensive',
  skipped: 'text-dropped',
}

export function Stages({ tasks }: { tasks: Task[] }) {
  const stages = [...new Set(tasks.map((t) => t.stage))].sort((a, b) => a - b)
  useTick(tasks.some((task) => task.status === 'running'))

  if (!stages.length) {
    return <p className="text-muted text-[15px]">No agent has started yet.</p>
  }

  const working = tasks.filter((task) => task.status === 'running')

  return (
    <>
      {working.length > 0 && (
        <p className="mb-3 text-[13.5px] text-muted">
          {working.length} reading now. Each one works through a whole section of the filing, so the first
          results take a minute or two to come back.
        </p>
      )}
    <ol className="border-t border-rule">
      {stages.map((stage) => {
        const inStage = tasks.filter((t) => t.stage === stage)
        return (
          <li key={stage} className="border-b border-rule py-3 grid grid-cols-[2.5rem_1fr] gap-x-3">
            <span className="text-[13px] text-muted pt-0.5">{stage}</span>
            <div>
              <h3 className="text-[15px] font-medium">{STAGE_NAMES[stage] ?? `Stage ${stage}`}</h3>
              <ul className="mt-1.5 space-y-1">
                {inStage.map((task) => (
                  <li key={task.task_id}
                      className="grid grid-cols-[10rem_5rem_auto] gap-x-4 items-baseline text-[14px]">
                    <span className="truncate">{task.agent}</span>
                    <span className={`text-[12.5px] ${STATUS_STYLE[task.status] ?? 'text-muted'}`}>
                      {task.status === 'running' ? `working ${elapsed(task.started_at)}` : task.status}
                    </span>
                    <span className="text-muted">{money(task.cost_usd)}</span>
                  </li>
                ))}
              </ul>
              {inStage.filter((t) => t.error).map((task) => (
                <p key={`${task.task_id}-error`} className="mt-1 text-[13px] text-expensive">{task.error}</p>
              ))}
            </div>
          </li>
        )
      })}
    </ol>
    </>
  )
}
