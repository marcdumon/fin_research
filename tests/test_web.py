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


# --- companies -----------------------------------------------------------------
def test_a_company_gathers_its_runs_into_one_row(cfg, client):
    """The rail used to list a company once per attempt, so a week of debugging looked like five companies."""
    with State(cfg.state_path) as state:
        for _ in range(3):
            state.start_run('VSAT', 66004, date(2026, 9, 21), budget_usd=2.0)

    listed = client.get('/api/companies').json()
    assert [c['ticker'] for c in listed] == ['VSAT']
    assert listed[0]['runs'] == 3


def test_a_run_that_is_not_about_a_company_is_not_a_company(cfg, client):
    """A funnel run carries the screen's name and a mistyped one carries a company's; neither is a ticker."""
    with State(cfg.state_path) as state:
        state.start_run('screen:quality_value', None, date(2026, 9, 1), budget_usd=1.0)
        state.start_run('NVIDIA', None, date(2026, 9, 20), budget_usd=2.0)
        state.start_run('NVDA', 1045810, date(2026, 9, 20), budget_usd=2.0)

    assert [c['ticker'] for c in client.get('/api/companies').json()] == ['NVDA']


def test_a_verdict_is_recorded_and_replaces_the_one_before_it(cfg, client):
    with State(cfg.state_path) as state:
        state.start_run('VSAT', 66004, date(2026, 9, 21), budget_usd=2.0)

    assert client.post('/api/companies/vsat/verdict',
                       json={'verdict': 'watch', 'conviction': 'low'}).status_code == 200
    assert client.post('/api/companies/VSAT/verdict',
                       json={'verdict': 'buy', 'conviction': 'high', 'note': 'margin turned'}).status_code == 200

    company = client.get('/api/companies/VSAT').json()
    assert company['verdict'] == 'buy'
    assert company['conviction'] == 'high'


def test_a_verdict_that_is_not_one_is_refused(client):
    assert client.post('/api/companies/VSAT/verdict', json={'verdict': 'maybe'}).status_code == 422


def test_a_company_with_no_research_is_still_answerable(client):
    """Opening a ticker nobody has read must give a page, not an error."""
    company = client.get('/api/companies/ZZZZ').json()
    assert company['ticker'] == 'ZZZZ'
    assert company['runs'] == 0
    assert company['verdict'] is None


def test_asking_for_a_note_that_does_not_exist_says_so(client):
    assert client.get('/api/companies/ZZZZ/note').status_code == 404
    assert client.get('/api/runs/nosuchrun/note').status_code == 404


def test_a_citation_resolves_to_the_filing_text_it_points_at(cfg, client):
    """The endpoint the evidence panel runs on: a span in, the surrounding filing text out."""
    from conftest import filing_html

    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.sections import split
    from fin_research.ingest.text import clean

    annual = Filing(cik=320193, accession='acc-1', form='10-K', filed=date(2026, 1, 31),
                    report_date=date(2025, 12, 31), primary_document='aapl.htm')
    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'AAPL')
        document = clean(filing_html('aapl_tech'))
        document_id = store_module.store_document(con, annual, 'aapl.htm', 'primary', document)
        store_module.store_sections(con, document_id, annual.accession, split(document))
        row = con.execute("SELECT section_id, char_start FROM sections WHERE item_key = '1'").fetchone()

    section_id, start = row[0], 0
    excerpt = client.get(f'/api/sources/{section_id}?start={start + 20}&end={start + 60}').json()

    assert excerpt['part'].startswith('Item 1')
    assert excerpt['form'] == '10-K'
    assert excerpt['span_start'] is not None
    cited = excerpt['excerpt'][excerpt['span_start']:excerpt['span_end']]
    assert len(cited) == 40
    assert excerpt['total_chars'] > 1000


def test_an_unknown_source_is_a_404(client):
    assert client.get('/api/sources/nosuchsource').status_code == 404


def test_a_company_route_serves_the_application(client):
    """A company is a real address, so reloading one or sending someone the link has to work."""
    from fin_research.web.api import FRONTEND

    if not FRONTEND.is_dir():
        pytest.skip('the frontend has not been built')
    assert client.get('/c/VSAT').status_code == 200
    assert client.get('/assets/does-not-exist.js').status_code == 404


def test_a_figure_cited_from_the_statements_opens_on_that_figure(cfg, client):
    """Statements are several pages read as one. A revenue figure from the income statement used to open onto
    the balance sheet, at an offset past its end, because the viewer fetched the first page on its own."""
    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.text import clean

    annual = Filing(cik=66004, accession='acc-9', form='10-K', filed=date(2026, 5, 29),
                    report_date=date(2026, 3, 31), primary_document='vsat.htm')
    balance = clean('<p>Total liabilities and equity | $ 15,226,596</p>')
    income = clean('<p>Revenues: | Total revenues | $ 4,640,280 | $ 4,519,571</p>')

    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'VSAT')
        first = store_module.store_document(con, annual, 'R2.htm', 'statements', balance)
        store_module.store_document(con, annual, 'R4.htm', 'statements', income)
        source_id, joined = store_module.statements_source(con, 'acc-9')

    start = joined.index('Total revenues')
    end = start + len('Total revenues | $ 4,640,280')
    assert start > len(balance.text), 'the figure must lie beyond the first page for this to test anything'

    for cited_as in (source_id, first):
        excerpt = client.get(f'/api/sources/{cited_as}?start={start}&end={end}').json()
        shown = excerpt['excerpt'][excerpt['span_start']:excerpt['span_end']]
        assert shown == 'Total revenues | $ 4,640,280', f'{cited_as} opened on the wrong text'
        assert excerpt['part'] == 'Financial statements'
        assert excerpt['form'] == '10-K'


def test_a_citation_that_does_not_fit_its_source_says_so(cfg, client):
    """Clamping a span to the text would highlight the nearest thing and hide that the citation is broken."""
    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.text import clean

    annual = Filing(cik=66004, accession='acc-9', form='10-K', filed=date(2026, 5, 29),
                    report_date=date(2026, 3, 31), primary_document='vsat.htm')
    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'VSAT')
        document_id = store_module.store_document(con, annual, 'ex99.htm', 'press_release',
                                                  clean('<p>A short release.</p>'))

    excerpt = client.get(f'/api/sources/{document_id}?start=5000&end=5020').json()
    assert excerpt['in_range'] is False
    assert excerpt['span_start'] is None
