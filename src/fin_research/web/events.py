"""Turn the run ledger into a live event stream.

The agent process writes to SQLite and the dashboard is a separate process reading it. That works only
because SQLite in WAL mode allows concurrent readers alongside a writer -- under DuckDB the reader could not
open the file at all while a run was in progress, which is the whole reason run state lives here.

Polling rather than a push channel: the writer is a different process, so there is nothing to subscribe to.
A query every half second over a few hundred rows costs nothing measurable, and the alternative would be a
message broker to move data between two processes on the same machine.

Events carry a monotonic cursor so a browser that reconnects resumes where it stopped instead of replaying
the run from the beginning.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fin_research.core.state import State

log = logging.getLogger(__name__)

POLL_SECONDS = 0.5
# Sent when nothing has happened, so proxies do not close an idle connection.
HEARTBEAT_SECONDS = 15.0


@dataclass
class Cursor:
    """How far a subscriber has read, per stream."""

    calls: str = ''
    tasks: str = ''
    approvals: str = ''
    seen: set[str] = field(default_factory=set)


def _sse(event: str, data: dict) -> str:
    """One server-sent event frame."""
    return f'event: {event}\ndata: {json.dumps(data, default=str)}\n\n'


def _rows(state: State, sql: str, params: tuple) -> list[dict]:
    """Query rows as plain dicts."""
    return [dict(row) for row in state.query(sql, params)]


def new_calls(state: State, run_id: str, cursor: Cursor) -> list[dict]:
    """Model calls recorded since the last poll."""
    rows = _rows(state, 'SELECT call_id, agent, tier, model, cost_usd, prompt_tokens, completion_tokens, '
                        'latency_ms, cache_hit, parse_mode, retries, error, created_at FROM llm_calls '
                        'WHERE run_id = ? AND created_at >= ? ORDER BY created_at', (run_id, cursor.calls))
    fresh = [r for r in rows if r['call_id'] not in cursor.seen]
    for row in fresh:
        cursor.seen.add(row['call_id'])
        cursor.calls = max(cursor.calls, row['created_at'])
    return fresh


def changed_tasks(state: State, run_id: str, cursor: Cursor) -> list[dict]:
    """Tasks whose state has moved since the last poll.

    Keyed on task and status together, because a task appears twice -- once running, once finished -- and the
    dashboard needs both to show work in progress rather than only completed work.
    """
    rows = _rows(state, 'SELECT task_id, agent, stage, status, detail, cost_usd, error, started_at, '
                        'finished_at FROM tasks WHERE run_id = ? ORDER BY started_at', (run_id,))
    fresh = []
    for row in rows:
        key = f"{row['task_id']}:{row['status']}"
        if key not in cursor.seen:
            cursor.seen.add(key)
            fresh.append(row)
    return fresh


def pending_approvals(state: State, run_id: str, cursor: Cursor) -> list[dict]:
    """Approval requests that have appeared and not yet been answered."""
    rows = _rows(state, "SELECT approval_id, subject, prompt, status, requested_at FROM approvals "
                        "WHERE run_id = ? AND status = 'pending' ORDER BY requested_at", (run_id,))
    fresh = []
    for row in rows:
        if row['approval_id'] not in cursor.seen:
            cursor.seen.add(row['approval_id'])
            fresh.append(row)
    return fresh


def run_snapshot(state: State, run_id: str) -> dict | None:
    """Everything about a run as it stands right now."""
    summary = state.run(run_id)
    if summary is None:
        return None

    return {
        'run_id': summary.run_id, 'ticker': summary.ticker, 'status': summary.status,
        'budget_usd': summary.budget_usd, 'spend_usd': summary.spend_usd,
        'remaining_usd': summary.remaining, 'calls': summary.calls,
        'started_at': summary.started_at, 'finished_at': summary.finished_at,
        'by_agent': _rows(state, 'SELECT agent, tier, count(*) AS calls, sum(cost_usd) AS cost_usd, '
                                 'sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS '
                                 'completion_tokens, sum(cache_hit) AS cache_hits FROM llm_calls '
                                 'WHERE run_id = ? GROUP BY agent, tier ORDER BY cost_usd DESC', (run_id,)),
        'tasks': _rows(state, 'SELECT task_id, agent, stage, status, cost_usd, error, started_at, '
                              'finished_at FROM tasks WHERE run_id = ? ORDER BY stage, started_at', (run_id,)),
    }


async def stream(state: State, run_id: str) -> AsyncIterator[str]:
    """Yield events for one run until it finishes, then stop.

    Sends a snapshot first so a browser arriving mid-run sees the whole picture rather than only what happens
    next, which is what makes reconnecting cheap.
    """
    cursor = Cursor()
    snapshot = run_snapshot(state, run_id)
    if snapshot is None:
        yield _sse('error', {'message': f'no run {run_id}'})
        return

    yield _sse('snapshot', snapshot)

    # Advance the cursor past everything the snapshot already showed, so a browser joining a run in progress
    # is not sent the first half of it again as a burst of events.
    changed_tasks(state, run_id, cursor)
    new_calls(state, run_id, cursor)

    idle = 0.0
    while True:
        await asyncio.sleep(POLL_SECONDS)
        moved = False

        for row in changed_tasks(state, run_id, cursor):
            yield _sse('task', row)
            moved = True
        for row in new_calls(state, run_id, cursor):
            yield _sse('call', row)
            moved = True
        for row in pending_approvals(state, run_id, cursor):
            yield _sse('approval', row)
            moved = True

        summary = state.run(run_id)
        if moved and summary is not None:
            yield _sse('run', {'status': summary.status, 'spend_usd': summary.spend_usd,
                               'remaining_usd': summary.remaining, 'calls': summary.calls})

        if summary is not None and summary.status != 'running':
            yield _sse('done', run_snapshot(state, run_id) or {})
            return

        idle = 0.0 if moved else idle + POLL_SECONDS
        if idle >= HEARTBEAT_SECONDS:
            idle = 0.0
            yield ': keep-alive\n\n'
