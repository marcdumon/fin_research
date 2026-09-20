"""Tests for the dashboard API.

The regression worth pinning is the read-only one: the dashboard holds a read-only connection, so any read
path that tries to create its schema raises and -- because the error was swallowed -- reported an empty list
while dozens of alerts sat in the database. Silent emptiness is the worst failure a monitor can have.
"""

from datetime import date

import duckdb
import pytest
from fastapi.testclient import TestClient

from fin_research.config import Config
from fin_research.core.state import State
from fin_research.ingest import store as store_module
from fin_research.monitor.store import Alert, Severity, ensure_schema, raise_alerts
from fin_research.web.api import create_app


@pytest.fixture
def cfg(tmp_path) -> Config:
    """A configuration pointing at empty, throwaway stores."""
    settings = Config(data_root=tmp_path)
    with store_module.connect(settings) as con:
        ensure_schema(con)
    with State(settings.state_path):
        pass
    return settings


@pytest.fixture
def client(cfg) -> TestClient:
    return TestClient(create_app(cfg))


def test_an_empty_installation_answers_rather_than_failing(client):
    """Every list endpoint must work before anything has been run."""
    assert client.get('/api/runs').json() == []
    assert client.get('/api/alerts').json() == []
    assert client.get('/api/approvals').json() == []


def test_the_roster_describes_every_agent(client):
    roster = client.get('/api/agents').json()
    assert {a['name'] for a in roster} >= {'business', 'bull', 'bear', 'synthesis', 'triage'}
    assert all(a['tier'] and a['model'] and a['description'] for a in roster)


def test_alerts_are_readable_through_a_read_only_connection(cfg, client):
    """The dashboard reads read-only. A read path that creates its schema silently reports nothing."""
    with store_module.connect(cfg) as con:
        raise_alerts(con, [Alert('AAPL', 'new_filings', Severity.HIGH, '10-K filed')], date(2026, 9, 20))

    alerts = client.get('/api/alerts').json()
    assert [a['ticker'] for a in alerts] == ['AAPL']
    assert alerts[0]['severity'] == 'high'


def test_alerts_can_be_acknowledged(cfg, client):
    with store_module.connect(cfg) as con:
        raise_alerts(con, [Alert('KO', 'price_move', Severity.MEDIUM, 'price fell 20%')], date(2026, 9, 20))

    assert len(client.get('/api/alerts').json()) == 1
    client.post('/api/alerts/acknowledge')
    assert client.get('/api/alerts').json() == []


def test_acknowledging_one_company_leaves_the_others(cfg, client):
    with store_module.connect(cfg) as con:
        raise_alerts(con, [Alert('KO', 'c', Severity.LOW, 'a'), Alert('AAPL', 'c', Severity.LOW, 'b')],
                     date(2026, 9, 20))

    client.post('/api/alerts/acknowledge?ticker=KO')
    assert [a['ticker'] for a in client.get('/api/alerts').json()] == ['AAPL']


@pytest.mark.parametrize('ticker', ['not a ticker!', '../etc/passwd', '', 'AAPL; rm -rf /'])
def test_a_launch_request_rejects_anything_that_is_not_a_ticker(client, ticker):
    """The ticker reaches a subprocess argument list, so it is validated rather than trusted."""
    assert client.post('/api/runs', json={'ticker': ticker}).status_code == 400


def test_an_unknown_run_is_a_404(client):
    assert client.get('/api/runs/nope').status_code == 404


def test_a_run_appears_once_it_exists(cfg, client):
    with State(cfg.state_path) as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)

    listed = client.get('/api/runs').json()
    assert [r['run_id'] for r in listed] == [run_id]

    detail = client.get(f'/api/runs/{run_id}').json()
    assert detail['ticker'] == 'AAPL'
    assert detail['budget_usd'] == 2.0
    assert detail['tasks'] == []


def test_monitoring_tables_absent_is_survivable(tmp_path):
    """A fresh install has never patrolled, so the tables do not exist yet."""
    settings = Config(data_root=tmp_path)
    duckdb.connect(str(settings.db_path)).close()
    assert TestClient(create_app(settings)).get('/api/alerts').json() == []
