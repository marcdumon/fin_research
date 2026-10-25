import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { cheaperModel, decide, listPendingApprovals, type ApprovalDecision } from '../../api'

// A run started with the approval gate stops before each expensive step and waits, for up to an hour.
// It used to ask only on the Activity page, so a run started from a company page waited on a question nobody
// could see. The question is shown wherever you are.
export function Approvals() {
  const [open, setOpen] = useState(false)
  const queries = useQueryClient()
  const pending = useQuery({ queryKey: ['approvals'], queryFn: listPendingApprovals, refetchInterval: 3_000 })

  const answer = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: ApprovalDecision }) => decide(id, decision),
    onSettled: () => void queries.invalidateQueries({ queryKey: ['approvals'] }),
  })

  const waiting = pending.data ?? []
  if (waiting.length === 0) return null

  return (
    <div className="relative mr-2">
      <button onClick={() => setOpen(!open)} aria-expanded={open}
              className="px-2.5 py-1 text-tiny rounded-sm bg-mid text-ground font-medium animate-pulse">
        {waiting.length} waiting for you
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-50 w-[26rem] bg-panel border border-rule-strong rounded
                        shadow-2xl p-3 space-y-3">
          {waiting.map((approval) => {
            const cheaper = cheaperModel(approval.prompt)
            return (
              <div key={approval.approval_id} className="space-y-2">
                <p className="text-small">{approval.prompt}</p>
                <div className="flex gap-2 flex-wrap">
                  <button onClick={() => answer.mutate({ id: approval.approval_id, decision: 'approved' })}
                          className="px-2.5 py-1 text-tiny rounded-sm bg-accent text-ground">Run it</button>
                  {cheaper && (
                    <button onClick={() => answer.mutate({ id: approval.approval_id, decision: 'downgraded' })}
                            className="px-2.5 py-1 text-tiny rounded-sm border border-rule">
                      Use {cheaper.split('/').pop()}
                    </button>
                  )}
                  <button onClick={() => answer.mutate({ id: approval.approval_id, decision: 'rejected' })}
                          className="px-2.5 py-1 text-tiny rounded-sm border border-rule text-muted">Skip it</button>
                </div>
              </div>
            )
          })}
          {answer.error && <p className="text-tiny text-danger">That answer was not recorded. Try again.</p>}
        </div>
      )}
    </div>
  )
}
