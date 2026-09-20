"""HTTP API for the dashboard.

Read-mostly by design. The agent run is a separate process writing to SQLite; this reads that ledger and
streams it. The only writes are approval decisions, which is the one place a human has to reach into a run
that is already moving.

There is no authentication and the server binds to localhost. This is a personal research tool reading a
local database, and adding accounts would be ceremony around a single-user machine -- but that assumption is
written down here because it stops holding the moment anyone exposes the port.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fin_research import paths
from fin_research.config import Config, read
from fin_research.core.state import State
from fin_research.web.events import run_snapshot, stream

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).parent / 'static'


class Decision(BaseModel):
    """A human's answer to an approval request."""

    approved: bool
    note: str | None = None


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
        """Every run, newest first."""
        with state() as store:
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

    @app.get('/api/agents')
    def list_agents() -> list[dict]:
        """The roster, and which model each agent runs on."""
        from fin_research.agents import analysts, debate, synthesis  # noqa: F401  (registers the roster)
        from fin_research.agents.base import AGENT_DESCRIPTIONS, AGENTS

        return [{'name': name, 'stage': agent.stage, 'output_type': agent.output_type,
                 'tier': settings.llm.tier_for(name), 'model': settings.llm.model_for(name),
                 'description': AGENT_DESCRIPTIONS[name]}
                for name, agent in sorted(AGENTS.items(), key=lambda kv: (kv[1].stage, kv[0]))]

    if FRONTEND.is_dir():
        app.mount('/', StaticFiles(directory=FRONTEND, html=True), name='frontend')

    return app


app = create_app()
