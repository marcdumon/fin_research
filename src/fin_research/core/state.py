"""Operational state: runs, tasks, model calls, artifacts and approvals.

Kept in SQLite rather than alongside the filings in DuckDB, for one concrete reason. DuckDB allows a single
writing process, and the supervision this system is built around means a second process -- a dashboard --
reading live traces while the agent process writes them. Under DuckDB that reader simply cannot open the file;
the neighbouring workbench carries a whole job-serialisation module because of it. SQLite in WAL mode gives
unlimited concurrent readers alongside one writer, which is exactly the shape of the problem.

The other reason is cost control. Every model call writes a row here, including the ones that fail, and the
run's running total is updated in the same transaction. That makes the budget a fact in the database rather
than a number held in memory by a process that might die, and it is what lets a stopped run resume without
re-spending.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    cik         INTEGER,
    as_of       TEXT NOT NULL,
    status      TEXT NOT NULL,
    budget_usd  REAL NOT NULL,
    spend_usd   REAL NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    error       TEXT,
    pid         INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    agent       TEXT NOT NULL,
    stage       INTEGER NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    cost_usd    REAL NOT NULL DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_run ON tasks (run_id, stage);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id           TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    task_id           TEXT,
    agent             TEXT NOT NULL,
    tier              TEXT NOT NULL,
    model             TEXT NOT NULL,
    provider          TEXT,
    prompt_sha256     TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          REAL NOT NULL DEFAULT 0,
    latency_ms        INTEGER,
    retries           INTEGER NOT NULL DEFAULT 0,
    cache_hit         INTEGER NOT NULL DEFAULT 0,
    parse_mode        TEXT,
    finish_reason     TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_calls_run ON llm_calls (run_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    task_id       TEXT,
    agent         TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_artifacts_run ON artifacts (run_id, artifact_type);

CREATE TABLE IF NOT EXISTS prompt_cache (
    cache_key     TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 0
);

-- Written by the dashboard in a later phase. The table exists from the start so that arriving does not
-- require a migration, and so the supervisor can already model a gate it does not yet present.
CREATE TABLE IF NOT EXISTS approvals (
    approval_id  TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    task_id      TEXT,
    subject      TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    decided_at   TEXT,
    decision     TEXT
);

-- What the analyst concluded, as distinct from what the agents found. A run produces a note; this
-- records what was decided about it, which is the thing that makes the dashboard a workflow rather than a
-- viewer. Here rather than in DuckDB because DuckDB takes one writer at a time, and a patrol holding that
-- lock must never be the reason recording a decision fails.
CREATE TABLE IF NOT EXISTS verdicts (
    ticker      TEXT PRIMARY KEY,
    verdict     TEXT NOT NULL,
    conviction  TEXT,
    note        TEXT,
    run_id      TEXT,
    reviewed_at TEXT NOT NULL
);
"""


class BudgetExceeded(RuntimeError):
    """Raised when a call would take a run past the budget it was given."""


@dataclass(frozen=True)
class RunSummary:
    """What a run cost and how it ended."""

    run_id: str
    ticker: str
    status: str
    budget_usd: float
    spend_usd: float
    calls: int
    started_at: str
    finished_at: str | None

    @property
    def remaining(self) -> float:
        """Budget left, never negative."""
        return max(self.budget_usd - self.spend_usd, 0.0)


def new_id() -> str:
    """Short unique identifier for a run, task, call or artifact."""
    return uuid.uuid4().hex[:16]


def _now() -> str:
    """Current time as an ISO string, which is how SQLite stores it here."""
    return datetime.now(UTC).isoformat(timespec='seconds')


def _process_alive(pid: int | None) -> bool:
    """Whether a process id is still running.

    Signal 0 performs the permission and existence checks without delivering anything, which is the standard
    way to ask. A process owned by another user reports as alive, which is the safe direction: a run is only
    declared dead when it certainly is.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class State:
    """The operational database.

    One connection guarded by a lock rather than a connection per thread: writes here are tiny and infrequent
    next to network calls, so serialising them costs nothing and removes a whole class of threading bug.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Bring an existing database up to the current schema.

        The tables are created with IF NOT EXISTS, so a column added later never appears in a database that
        already exists. Adding them here keeps a working installation working across an upgrade.
        """
        for table, column, kind in (('runs', 'pid', 'INTEGER'), ('llm_calls', 'provider', 'TEXT')):
            existing = {row['name'] for row in self._con.execute(f'PRAGMA table_info({table})')}
            if column not in existing:
                with self._con:
                    self._con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')

    def close(self) -> None:
        """Close the underlying connection."""
        self._con.close()

    def __enter__(self) -> 'State':
        """Use as a context manager so the connection always closes."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close on exit."""
        self.close()

    def _write(self, sql: str, params: tuple = ()) -> None:
        """Run one statement inside the lock and commit it."""
        with self._lock, self._con:
            self._con.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Run a read query. Safe to call while another process is writing, because of WAL."""
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    # --- runs ----------------------------------------------------------------
    def start_run(self, ticker: str, cik: int | None, as_of: date, budget_usd: float,
                  config: dict | None = None) -> str:
        """Open a run and return its id."""
        run_id = new_id()
        self._write(
            'INSERT INTO runs (run_id, ticker, cik, as_of, status, budget_usd, config_json, started_at, pid) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (run_id, ticker, cik, as_of.isoformat(), 'running', budget_usd, json.dumps(config or {}),
             _now(), os.getpid()))
        return run_id

    def reconcile(self) -> int:
        """Close any run whose process is gone, and report how many were closed.

        A run that is killed, crashes or is interrupted never gets to write its own final status, so its row
        says 'running' indefinitely and the dashboard shows work that stopped hours ago. Nothing else detects
        this: the ledger only records what a live process tells it.
        """
        stale = [row['run_id'] for row in self.query("SELECT run_id, pid FROM runs WHERE status = 'running'")
                 if not _process_alive(row['pid'])]
        for run_id in stale:
            self.finish_run(run_id, 'interrupted', 'the process running it is no longer present')
        if stale:
            log.info(f'closed {len(stale)} run(s) whose process had gone')
        return len(stale)

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        """Close a run with a final status."""
        self._write('UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE run_id = ?',
                    (status, _now(), error, run_id))

    def run(self, run_id: str) -> RunSummary | None:
        """Summary of one run, or None if it does not exist."""
        rows = self.query(
            'SELECT r.*, (SELECT count(*) FROM llm_calls c WHERE c.run_id = r.run_id) AS calls '
            'FROM runs r WHERE r.run_id = ?', (run_id,))
        if not rows:
            return None
        row = rows[0]
        return RunSummary(run_id=row['run_id'], ticker=row['ticker'], status=row['status'],
                          budget_usd=row['budget_usd'], spend_usd=row['spend_usd'], calls=row['calls'],
                          started_at=row['started_at'], finished_at=row['finished_at'])

    def check_budget(self, run_id: str, estimated_usd: float) -> None:
        """Raise if this call would take the run past its budget.

        Checked before the request is sent, not after it returns. Metering afterwards is observability;
        refusing beforehand is control, and controlling spend is the point.
        """
        rows = self.query('SELECT budget_usd, spend_usd FROM runs WHERE run_id = ?', (run_id,))
        if not rows:
            return
        budget, spend = rows[0]['budget_usd'], rows[0]['spend_usd']
        if spend + estimated_usd > budget:
            raise BudgetExceeded(
                f'run {run_id} would spend ${spend + estimated_usd:.4f} against a ${budget:.2f} budget')

    # --- tasks ---------------------------------------------------------------
    def start_task(self, run_id: str, agent: str, stage: int, detail: str | None = None) -> str:
        """Open a task and return its id."""
        task_id = new_id()
        self._write('INSERT INTO tasks (task_id, run_id, agent, stage, status, detail, started_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (task_id, run_id, agent, stage, 'running', detail, _now()))
        return task_id

    def finish_task(self, task_id: str, status: str, error: str | None = None) -> None:
        """Close a task, carrying over what its calls cost."""
        self._write(
            'UPDATE tasks SET status = ?, finished_at = ?, error = ?, '
            'cost_usd = (SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE task_id = tasks.task_id) '
            'WHERE task_id = ?', (status, _now(), error, task_id))

    # --- model calls ---------------------------------------------------------
    def record_call(self, run_id: str, agent: str, tier: str, model: str, prompt_sha256: str,
                    *, task_id: str | None = None, prompt_tokens: int | None = None,
                    completion_tokens: int | None = None, cost_usd: float = 0.0,
                    latency_ms: int | None = None, retries: int = 0, cache_hit: bool = False,
                    parse_mode: str | None = None, finish_reason: str | None = None,
                    error: str | None = None, provider: str | None = None) -> str:
        """Record one model call and add its cost to the run, in a single transaction.

        Written for failures too. A run that burned budget on calls that errored has still spent the money,
        and a ledger that omits them would understate what the work cost.
        """
        call_id = new_id()
        with self._lock, self._con:
            self._con.execute(
                'INSERT INTO llm_calls (call_id, run_id, task_id, agent, tier, model, provider, '
                'prompt_sha256, prompt_tokens, completion_tokens, cost_usd, latency_ms, retries, cache_hit, '
                'parse_mode, finish_reason, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (call_id, run_id, task_id, agent, tier, model, provider, prompt_sha256, prompt_tokens,
                 completion_tokens, cost_usd, latency_ms, retries, int(cache_hit), parse_mode,
                 finish_reason, error, _now()))
            self._con.execute('UPDATE runs SET spend_usd = spend_usd + ? WHERE run_id = ?', (cost_usd, run_id))
        return call_id

    def cost_by_agent(self, run_id: str) -> list[sqlite3.Row]:
        """What each agent cost in one run, dearest first."""
        return self.query(
            'SELECT agent, tier, count(*) AS calls, sum(cost_usd) AS cost_usd, '
            'sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS completion_tokens, '
            'sum(cache_hit) AS cache_hits FROM llm_calls WHERE run_id = ? '
            'GROUP BY agent, tier ORDER BY cost_usd DESC', (run_id,))

    # --- artifacts -----------------------------------------------------------
    def save_artifact(self, run_id: str, agent: str, artifact_type: str, payload: dict,
                      task_id: str | None = None) -> str:
        """Store one agent's typed output."""
        artifact_id = new_id()
        self._write('INSERT INTO artifacts (artifact_id, run_id, task_id, agent, artifact_type, '
                    'payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (artifact_id, run_id, task_id, agent, artifact_type, json.dumps(payload, default=str), _now()))
        return artifact_id

    def load_artifacts(self, run_id: str, artifact_type: str | None = None) -> list[dict[str, Any]]:
        """Artifacts produced during a run, oldest first, optionally of one type."""
        sql = 'SELECT agent, artifact_type, payload_json FROM artifacts WHERE run_id = ?'
        params: tuple = (run_id,)
        if artifact_type:
            sql += ' AND artifact_type = ?'
            params += (artifact_type,)
        return [{'agent': r['agent'], 'artifact_type': r['artifact_type'], 'payload': json.loads(r['payload_json'])}
                for r in self.query(sql + ' ORDER BY created_at', params)]

    # --- prompt cache --------------------------------------------------------
    def cached_response(self, cache_key: str) -> dict | None:
        """A previous response for an identical prompt, or None.

        Makes re-running a research run after a crash nearly free, and makes the whole pipeline replayable
        without spending anything.
        """
        rows = self.query('SELECT response_json FROM prompt_cache WHERE cache_key = ?', (cache_key,))
        if not rows:
            return None
        self._write('UPDATE prompt_cache SET hits = hits + 1 WHERE cache_key = ?', (cache_key,))
        return json.loads(rows[0]['response_json'])

    def cache_response(self, cache_key: str, model: str, response: dict) -> None:
        """Remember a response against its prompt."""
        self._write('INSERT OR REPLACE INTO prompt_cache (cache_key, model, response_json, created_at) '
                    'VALUES (?, ?, ?, ?)', (cache_key, model, json.dumps(response), _now()))

    # --- approvals -----------------------------------------------------------
    def request_approval(self, run_id: str, subject: str, prompt: str, task_id: str | None = None) -> str:
        """Record that a run is waiting on a human decision."""
        approval_id = new_id()
        self._write('INSERT INTO approvals (approval_id, run_id, task_id, subject, prompt, requested_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)', (approval_id, run_id, task_id, subject, prompt, _now()))
        return approval_id

    def decide_approval(self, approval_id: str, status: str, decision: str | None = None) -> None:
        """Record the human's answer."""
        self._write('UPDATE approvals SET status = ?, decided_at = ?, decision = ? WHERE approval_id = ?',
                    (status, _now(), decision, approval_id))

    def pending_approvals(self, run_id: str | None = None) -> list[sqlite3.Row]:
        """Approvals still waiting on a decision."""
        if run_id:
            return self.query("SELECT * FROM approvals WHERE status = 'pending' AND run_id = ?", (run_id,))
        return self.query("SELECT * FROM approvals WHERE status = 'pending'")

    def record_verdict(self, ticker: str, verdict: str, conviction: str | None = None,
                       note: str | None = None, run_id: str | None = None) -> None:
        """Record what was decided about a company, replacing any earlier decision.

        One row per company rather than a history: the question the dashboard asks is what you think now.
        The runs behind it are already kept in full, so the reasoning is not lost by overwriting the answer.
        """
        self._write(
            'INSERT INTO verdicts (ticker, verdict, conviction, note, run_id, reviewed_at) '
            'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET '
            'verdict = excluded.verdict, conviction = excluded.conviction, note = excluded.note, '
            'run_id = excluded.run_id, reviewed_at = excluded.reviewed_at',
            (ticker.upper(), verdict, conviction, note, run_id, _now()))

    def verdicts(self) -> dict[str, sqlite3.Row]:
        """Every recorded decision, by ticker."""
        return {row['ticker']: row for row in self.query('SELECT * FROM verdicts')}
