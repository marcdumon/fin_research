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
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Literal

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

# Runs the system makes about itself rather than about a company.
DIAGNOSTIC_RUN = 'schema-check'


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
    """A human's answer to an approval request.

    A choice among outcomes rather than a boolean: refusing an expensive step on the last stage of a run
    discards everything before it, so "run it on something cheaper" is usually the answer wanted.
    """

    decision: Literal['approved', 'downgraded', 'rejected'] = 'approved'
    note: str | None = None


class Patrol(BaseModel):
    """A request to check the watchlist for change."""

    tickers: list[str] = []


class Funnel(BaseModel):
    """A request to screen, then read the survivors."""

    screen: str
    budget_usd: float = 1.0
    limit: int = 20


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
            # Diagnostic runs are excluded. They are real rows with real costs, but listing them beside
            # research pushes the work someone is actually watching off the screen.
            return [dict(row) for row in store.query(
                'SELECT r.run_id, r.ticker, r.status, r.as_of, r.budget_usd, r.spend_usd, r.started_at, '
                'r.finished_at, (SELECT count(*) FROM llm_calls c WHERE c.run_id = r.run_id) AS calls '
                'FROM runs r WHERE r.ticker NOT IN (?) ORDER BY r.started_at DESC LIMIT ?',
                (DIAGNOSTIC_RUN, limit))]

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
        """Answer an approval request, releasing, downgrading or stopping the step waiting on it."""
        with state() as store:
            store.decide_approval(approval_id, decision.decision, decision.note)
        return {'approval_id': approval_id, 'status': decision.decision}

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

    @app.post('/api/schema-check')
    async def schema_check() -> list[dict]:
        """Send each artifact schema to the model that will receive it, and report what is accepted.

        A provider rejecting a schema otherwise surfaces as a 400 partway through a run, after the cheap
        stages have been paid for.
        """
        from fin_research.agents import AGENTS
        from fin_research.core.schemas import ARTIFACTS
        from fin_research.llm.client import ModelClient, ModelRequest

        owners: dict[str, str] = {}
        for name, agent in AGENTS.items():
            owners.setdefault(agent.output_type, name)

        results = []
        with state() as store:
            run_id = store.start_run(DIAGNOSTIC_RUN, None, date.today(), 0.5, {'check': True})
            async with ModelClient(cfg=settings, state=store, run_id=run_id) as client:
                for artifact_type, schema in ARTIFACTS.items():
                    agent = owners.get(artifact_type)
                    if agent is None:
                        continue
                    request = ModelRequest(agent=agent, system='Reply with the requested structure.',
                                           user='Produce a minimal valid example.', schema=schema,
                                           max_tokens=2000)
                    try:
                        await client.call(request)
                        results.append({'schema': artifact_type, 'model': settings.llm.model_for(agent),
                                        'accepted': True, 'detail': ''})
                    except Exception as error:
                        results.append({'schema': artifact_type, 'model': settings.llm.model_for(agent),
                                        'accepted': False, 'detail': str(error)[:200]})
            store.finish_run(run_id, 'done')
        return results

    def _spawn(command: list[str], name: str) -> dict:
        """Start a background command and keep its output.

        Screening and patrolling are quick enough to answer inline; the funnel and a patrol over a long
        watchlist are not, and holding the request open for minutes gives the browser nothing to show in the
        meantime.
        """
        logs = settings.data_root / 'runs'
        logs.mkdir(parents=True, exist_ok=True)
        log_file = logs / f'{name}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.log'
        handle = log_file.open('w')
        process = subprocess.Popen([sys.executable, '-m', *command], cwd=paths.PROJECT_ROOT,
                                   stdout=handle, stderr=subprocess.STDOUT)
        return {'pid': process.pid, 'log': str(log_file)}

    @app.get('/api/screens')
    def list_screens() -> list[dict]:
        """The screens that are defined, and what each one requires."""
        from fin_research.screen import rules

        path = paths.PROJECT_ROOT / 'screens.toml'
        if not path.exists():
            return []
        return [{'name': screen.name, 'description': screen.description,
                 'requires': [c.describe() for c in screen.require],
                 'excludes': list(screen.exclude_sectors), 'top': screen.top,
                 'notes': screen.notes.strip()}
                for screen in rules.load(path).values()]

    @app.post('/api/screens/{name}/run')
    def run_screen(name: str, as_of: str | None = None) -> dict:
        """Run one screen and return the companies that survive it.

        Answered inline: screening reads no filings and calls no model, so it completes in about a second
        over a couple of thousand companies.
        """
        from fin_research.screen import factors, rules

        screens = rules.load(paths.PROJECT_ROOT / 'screens.toml')
        if name not in screens:
            raise HTTPException(404, f'no screen named {name!r}')

        when = date.fromisoformat(as_of) if as_of else date.today()
        con = store_module.connect(settings, read_only=True)
        try:
            if not settings.investalyze_db or not store_module.attach_market_data(con, settings.investalyze_db):
                raise HTTPException(503, 'screening needs the market database, which could not be attached')
            table = factors.compute(con, when)
        finally:
            con.close()

        result = rules.apply(screens[name], table)
        columns = ['Ticker', 'name', 'sector', 'market_cap', 'earnings_yield', 'fcf_yield', 'roe',
                   'gross_margin', 'revenue_growth', 'momentum', 'score']
        present = [c for c in columns if c in result.survivors.columns]
        survivors = result.survivors[present].where(result.survivors[present].notna(), None)

        return {'screen': name, 'as_of': str(when), 'considered': result.considered,
                'dropped': result.dropped, 'survivors': survivors.to_dict('records')}

    @app.post('/api/screens/funnel')
    def run_funnel(request: Funnel) -> dict:
        """Screen, then read each survivor and decide which deserve a deep dive. This one spends money."""
        return _spawn(['fin_research.screen', 'funnel', '--screen', request.screen,
                       '--budget', str(request.budget_usd), '--limit', str(request.limit),
                       '--log-level', 'INFO'], f'funnel-{request.screen}')

    @app.post('/api/monitor/patrol')
    def patrol(request: Patrol) -> dict:
        """Check the watchlist for change. Calls no model."""
        command = ['fin_research.monitor', 'patrol', '--log-level', 'INFO']
        if request.tickers:
            command += ['--tickers', *[t.upper() for t in request.tickers if t.isalnum()]]
        return _spawn(command, 'patrol')

    @app.get('/api/monitor/watchlist')
    def get_watchlist() -> list[str]:
        """Companies being patrolled."""
        from fin_research.monitor.store import watchlist

        with store_module.connect(settings, read_only=True) as con:
            try:
                return watchlist(con)
            except duckdb.Error:
                return []

    @app.get('/api/verify/{ticker}')
    async def verify_statements(ticker: str, periods: int = 5) -> dict:
        """Whether a company's own reported statements reconcile.

        This is what says a number can be trusted before anything reasons about it, so it belongs where the
        numbers are read rather than behind a command.
        """
        from fin_research.ingest.edgar import EdgarClient
        from fin_research.ingest.facts import as_of as facts_as_of
        from fin_research.ingest.facts import fetch_facts
        from fin_research.ingest.identities import check_filer

        async with EdgarClient(settings) as client:
            cik = await client.resolve_filer(ticker.upper())
            facts = facts_as_of(await fetch_facts(client, cik))

        report = check_filer(facts, limit=periods)
        return {
            'ticker': ticker.upper(), 'trustworthy': report.trustworthy,
            'passed': report.passed, 'failed': len(report.failed), 'skipped': report.skipped,
            'results': [{'name': r.name, 'period_end': str(r.period_end), 'status': r.status.value,
                         'detail': r.detail} for r in report.results],
        }

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
