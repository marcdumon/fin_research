"""HTTP API for the dashboard.

Read-mostly by design. The agent run is a separate process writing to SQLite; this reads that ledger and
streams it. The only writes are approval decisions, which is the one place a human has to reach into a run
that is already moving.

There is no authentication and the server binds to localhost. This is a personal research tool reading a
local database, and adding accounts would be ceremony around a single-user machine -- but that assumption is
written down here because it stops holding the moment anyone exposes the port.
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fin_research import paths
from fin_research.config import Config, read
from fin_research.core.state import State
from fin_research.ingest import store as store_module
from fin_research.web.events import run_snapshot, stream

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).parent / 'static'


class _NoCacheIndex(StaticFiles):
    """Static files, except that the entry page is never cached.

    Asset filenames carry a content hash and can be cached forever, but `index.html` names which assets to
    load. Letting a browser cache it means a rebuilt front end keeps serving the previous bundle, and the
    symptom is a page that looks fine while behaving like older code -- which cost two debugging sessions
    before the cause was obvious.
    """

    async def get_response(self, path: str, scope):
        """Serve normally, then forbid caching of the entry page."""
        response = await super().get_response(path, scope)
        if path in ('.', '', 'index.html'):
            response.headers['Cache-Control'] = 'no-store, must-revalidate'
        return response


class Decision(BaseModel):
    """A human's answer to an approval request."""

    approved: bool
    note: str | None = None


class Launch(BaseModel):
    """A request to start a research run."""

    ticker: str
    budget_usd: float = 2.0
    gate_tiers: list[str] = []


def create_app(cfg: Config | None = None) -> FastAPI:
    """Build the dashboard application.

    A factory rather than a module-level app so tests can point it at a throwaway database instead of the
    real one.
    """
    settings = cfg or read(paths.PROJECT_ROOT / 'fin_research.toml')
    app = FastAPI(title='fin_research', docs_url='/api/docs')

    def state() -> State:
        """A connection to the run ledger.

        Opened per request rather than held: SQLite in WAL mode makes readers cheap, and a long-lived handle
        in a server would be one more thing to invalidate when a run process rewrites the file.
        """
        return State(settings.state_path)

    @app.get('/api/runs')
    def list_runs(limit: int = 50) -> list[dict]:
        """Every run, newest first.

        Stale rows are closed first, so a run whose process was killed does not keep reporting itself as
        working. The dashboard is where that would otherwise be seen and believed.
        """
        with state() as store:
            store.reconcile()
            return [dict(row) for row in store.query(
                'SELECT r.run_id, r.ticker, r.status, r.as_of, r.budget_usd, r.spend_usd, r.started_at, '
                'r.finished_at, (SELECT count(*) FROM llm_calls c WHERE c.run_id = r.run_id) AS calls '
                'FROM runs r ORDER BY r.started_at DESC LIMIT ?', (limit,))]

    @app.get('/api/runs/{run_id}')
    def get_run(run_id: str) -> dict:
        """One run in full: cost by agent, and every task."""
        with state() as store:
            snapshot = run_snapshot(store, run_id)
        if snapshot is None:
            raise HTTPException(404, f'no run {run_id}')
        return snapshot

    @app.get('/api/runs/{run_id}/calls')
    def get_calls(run_id: str, limit: int = 500) -> list[dict]:
        """The model calls a run made, newest first."""
        with state() as store:
            return [dict(row) for row in store.query(
                'SELECT call_id, agent, tier, model, cost_usd, prompt_tokens, completion_tokens, latency_ms, '
                'cache_hit, parse_mode, retries, error, created_at FROM llm_calls WHERE run_id = ? '
                'ORDER BY created_at DESC LIMIT ?', (run_id, limit))]

    @app.get('/api/runs/{run_id}/artifacts')
    def get_artifacts(run_id: str) -> list[dict]:
        """What each agent produced."""
        with state() as store:
            return store.load_artifacts(run_id)

    @app.get('/api/runs/{run_id}/stream')
    async def stream_run(run_id: str) -> StreamingResponse:
        """Live events for a run, as server-sent events.

        `X-Accel-Buffering` is set because a proxy that buffers the response turns a live trace into a single
        delivery at the end, which is the opposite of the point.
        """
        store = state()

        async def events():
            try:
                async for frame in stream(store, run_id):
                    yield frame
            finally:
                store.close()

        return StreamingResponse(events(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})

    @app.get('/api/approvals')
    def list_approvals(run_id: str | None = None) -> list[dict]:
        """Approval requests still waiting on a decision."""
        with state() as store:
            return [dict(row) for row in store.pending_approvals(run_id)]

    @app.post('/api/approvals/{approval_id}')
    def decide(approval_id: str, decision: Decision) -> dict:
        """Answer an approval request, releasing or stopping the step that is waiting on it."""
        with state() as store:
            store.decide_approval(approval_id, 'approved' if decision.approved else 'rejected', decision.note)
        return {'approval_id': approval_id, 'status': 'approved' if decision.approved else 'rejected'}

    @app.get('/api/reports/{ticker}')
    def get_report(ticker: str) -> FileResponse:
        """The most recent written note for a company."""
        directory = paths.PROJECT_ROOT / 'reports'
        found = sorted(directory.glob(f'{ticker.upper()}_*.md'), reverse=True)
        if not found:
            raise HTTPException(404, f'no report for {ticker}')
        return FileResponse(found[0], media_type='text/markdown')

    @app.get('/api/alerts')
    def list_alerts(ticker: str | None = None, limit: int = 50) -> list[dict]:
        """Change the monitor noticed and nobody has acknowledged yet."""
        from fin_research.monitor.store import open_alerts

        with store_module.connect(settings, read_only=True) as con:
            try:
                return open_alerts(con, ticker, limit).to_dict('records')
            except duckdb.Error:
                return []

    @app.post('/api/alerts/acknowledge')
    def acknowledge_alerts(ticker: str | None = None) -> dict:
        """Mark alerts as seen."""
        from fin_research.monitor.store import acknowledge

        with store_module.connect(settings) as con:
            acknowledge(con, ticker)
        return {'acknowledged': ticker or 'all'}

    @app.post('/api/runs')
    async def start_run(launch: Launch) -> dict:
        """Start a research run as a separate process.

        Spawned rather than run in the server, for two reasons. A research run takes minutes and would block
        or complicate the event loop serving the very dashboard watching it. And because run state lives in
        SQLite, a separate process is already the supported arrangement -- the stream endpoint reads its rows
        exactly as it reads those of a run started from the command line.
        """
        ticker = launch.ticker.strip().upper()
        if not ticker.isalnum():
            raise HTTPException(400, f'not a ticker: {launch.ticker!r}')

        command = [sys.executable, '-m', 'fin_research.agents', 'run',
                   '--ticker', ticker, '--budget', str(launch.budget_usd), '--log-level', 'INFO']
        if launch.gate_tiers:
            command += ['--gate', *launch.gate_tiers]

        before = {row['run_id'] for row in state().query('SELECT run_id FROM runs', ())}

        # Kept rather than discarded. A run started from here used to send its output to /dev/null, so when
        # one stalled there was nothing to look at -- the ledger says a call is in flight but not what the
        # provider said about it.
        logs = settings.data_root / 'runs'
        logs.mkdir(parents=True, exist_ok=True)
        log_file = logs / f'{ticker}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.log'
        handle = log_file.open('w')

        process = await asyncio.create_subprocess_exec(
            *command, cwd=paths.PROJECT_ROOT, stdout=handle, stderr=asyncio.subprocess.STDOUT)

        # The run registers itself on startup, so the dashboard is told which row to follow rather than
        # having to guess which of several concurrent runs it just caused.
        for _ in range(60):
            await asyncio.sleep(0.25)
            fresh = [row['run_id'] for row in state().query(
                'SELECT run_id FROM runs WHERE ticker = ? ORDER BY started_at DESC', (ticker,))
                if row['run_id'] not in before]
            if fresh:
                return {'run_id': fresh[0], 'ticker': ticker, 'pid': process.pid, 'log': str(log_file)}

        return {'run_id': None, 'ticker': ticker, 'pid': process.pid, 'log': str(log_file),
                'note': 'started, but it has not registered yet'}

    @app.get('/api/agents')
    def list_agents() -> list[dict]:
        """The roster, and which model each agent runs on."""
        from fin_research.agents import AGENT_DESCRIPTIONS, AGENTS

        return [{'name': name, 'stage': agent.stage, 'output_type': agent.output_type,
                 'tier': settings.llm.tier_for(name), 'model': settings.llm.model_for(name),
                 'description': AGENT_DESCRIPTIONS[name]}
                for name, agent in sorted(AGENTS.items(), key=lambda kv: (kv[1].stage, kv[0]))]

    if FRONTEND.is_dir():
        app.mount('/', _NoCacheIndex(directory=FRONTEND, html=True), name='frontend')

    return app


app = create_app()
