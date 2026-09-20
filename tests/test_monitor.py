"""Tests for monitoring.

The property that matters most is what happens on first contact. A company added to the watchlist today has
not *changed*, so the first pass must record its state and raise nothing -- otherwise every new name floods
the list with its entire filing history and buries the one thing that moves tomorrow.
"""

from datetime import date, timedelta

import duckdb
import httpx
import pytest
import respx

from fin_research.config import Config, EdgarConfig
from fin_research.ingest import edgar
from fin_research.ingest.edgar import EdgarClient
from fin_research.monitor import store as monitor_store
from fin_research.monitor.checks import ADVERSE_ITEMS, CHECKS, Watch, material_events, new_filings, screen_membership
from fin_research.monitor.store import Alert, Severity, acknowledge, open_alerts, raise_alerts, unwatch, watch, watchlist

TODAY = date(2026, 9, 20)


@pytest.fixture
def con():
    """An in-memory store with the monitoring schema."""
    connection = duckdb.connect()
    monitor_store.ensure_schema(connection)
    return connection


@pytest.fixture
def cfg(tmp_path):
    """Caching disabled, so a second pass genuinely re-reads rather than replaying the first."""
    return Config(data_root=tmp_path, edgar=EdgarConfig(rate_limit_seconds=0.0, cache_ttl_days=0))


def _submissions(*filings) -> dict:
    """Submissions payload in EDGAR's parallel-array shape."""
    keys = ('form', 'accessionNumber', 'primaryDocument', 'reportDate', 'filingDate', 'items')
    return {'filings': {'recent': {key: [f[i] for f in filings] for i, key in enumerate(keys)}, 'files': []}}


async def _watch_for(con, cfg, filings) -> Watch:
    """A Watch pointed at a mocked filer."""
    respx.get(edgar.SUBMISSIONS_URL.format(cik=1)).mock(
        return_value=httpx.Response(200, json=_submissions(*filings)))
    client = await EdgarClient(cfg).__aenter__()
    return Watch(con=con, client=client, ticker='TEST', as_of=TODAY, cik=1)


# --- the watchlist ------------------------------------------------------------
def test_watching_and_unwatching(con):
    watch(con, ['aapl', 'KO'])
    assert watchlist(con) == ['AAPL', 'KO']

    unwatch(con, ['AAPL'])
    assert watchlist(con) == ['KO']
    assert 'AAPL' in watchlist(con, include_inactive=True), 'history should survive unwatching'


# --- first contact ------------------------------------------------------------
@respx.mock
async def test_first_pass_records_state_and_raises_nothing(con, cfg):
    """A company added today has not changed. Alerting on its whole history would bury tomorrow's news."""
    filings = [('10-K', 'a-1', 'k.htm', '2025-12-31', '2026-02-20', ''),
               ('10-Q', 'a-2', 'q.htm', '2026-03-31', '2026-05-01', '')]
    w = await _watch_for(con, cfg, filings)

    assert await new_filings(w) == []
    assert monitor_store.last_seen(con, 'TEST', 'new_filings') is not None


@respx.mock
async def test_a_new_filing_is_detected_on_the_second_pass(con, cfg):
    first = [('10-Q', 'a-2', 'q.htm', '2026-03-31', '2026-05-01', '')]
    w = await _watch_for(con, cfg, first)
    await new_filings(w)

    respx.get(edgar.SUBMISSIONS_URL.format(cik=1)).mock(return_value=httpx.Response(
        200, json=_submissions(('10-K', 'a-3', 'k.htm', '2026-06-30', '2026-08-01', ''), *first)))
    alerts = await new_filings(w)

    assert [a.headline for a in alerts] == ['10-K filed 2026-08-01']
    assert alerts[0].severity is Severity.HIGH, 'an annual report outranks a quarterly one'


@respx.mock
async def test_nothing_is_raised_when_nothing_moved(con, cfg):
    filings = [('10-K', 'a-1', 'k.htm', '2025-12-31', '2026-02-20', '')]
    w = await _watch_for(con, cfg, filings)
    await new_filings(w)
    assert await new_filings(w) == []


@respx.mock
async def test_a_filing_dated_after_the_patrol_is_invisible(con, cfg):
    """Patrolling a past date must not see the future, same as everywhere else."""
    future = (TODAY + timedelta(days=30)).isoformat()
    w = await _watch_for(con, cfg, [('10-K', 'a-1', 'k.htm', '2026-06-30', future, '')])
    await new_filings(w)
    assert monitor_store.last_seen(con, 'TEST', 'new_filings')['payload']['accessions'] == []


# --- events -------------------------------------------------------------------
@respx.mock
async def test_adverse_event_codes_outrank_routine_ones(con, cfg):
    """A director leaving and an earnings release are both 8-Ks; only one of them is news."""
    w = await _watch_for(con, cfg, [('8-K', 'a-1', 'e.htm', '', '2026-02-01', '2.02,9.01')])
    await material_events(w)

    respx.get(edgar.SUBMISSIONS_URL.format(cik=1)).mock(return_value=httpx.Response(200, json=_submissions(
        ('8-K', 'a-2', 'e.htm', '', '2026-03-01', '5.02'),
        ('8-K', 'a-1', 'e.htm', '', '2026-02-01', '2.02,9.01'))))
    alerts = await material_events(w)

    assert len(alerts) == 1
    assert alerts[0].severity is Severity.HIGH
    assert '5.02' in ADVERSE_ITEMS


@respx.mock
async def test_routine_only_filings_raise_nothing(con, cfg):
    """A shareholder vote is an 8-K but not a reason to look."""
    w = await _watch_for(con, cfg, [('8-K', 'a-1', 'e.htm', '', '2026-01-01', '5.07,9.01')])
    await material_events(w)

    respx.get(edgar.SUBMISSIONS_URL.format(cik=1)).mock(return_value=httpx.Response(200, json=_submissions(
        ('8-K', 'a-2', 'e.htm', '', '2026-02-01', '5.07'),
        ('8-K', 'a-1', 'e.htm', '', '2026-01-01', '5.07,9.01'))))
    assert await material_events(w) == []


# --- screens ------------------------------------------------------------------
def test_entering_a_screen_outranks_leaving_it(con):
    """Crossing a threshold someone set deliberately is a better reason to look than falling out of one."""
    w = Watch(con=con, client=None, ticker='TEST', as_of=TODAY, screen_members={'value': set()})
    assert screen_membership(w) == []

    w.screen_members = {'value': {'TEST'}}
    entered = screen_membership(w)
    assert len(entered) == 1 and entered[0].severity is Severity.MEDIUM
    assert 'now passes' in entered[0].headline

    w.screen_members = {'value': set()}
    left = screen_membership(w)
    assert len(left) == 1 and left[0].severity is Severity.LOW


def test_no_screens_configured_is_a_no_op(con):
    assert screen_membership(Watch(con=con, client=None, ticker='TEST', as_of=TODAY)) == []


# --- alerts -------------------------------------------------------------------
def test_the_same_change_raised_twice_collapses(con):
    """A patrol running hourly must not multiply one event into a wall of duplicates."""
    alert = Alert(ticker='AAPL', check_name='new_filings', severity=Severity.HIGH, headline='10-K filed')
    raise_alerts(con, [alert], TODAY)
    raise_alerts(con, [alert], TODAY)
    assert len(open_alerts(con)) == 1


def test_alerts_are_ordered_by_severity(con):
    raise_alerts(con, [
        Alert('A', 'c', Severity.LOW, 'low one'),
        Alert('B', 'c', Severity.HIGH, 'high one'),
        Alert('C', 'c', Severity.MEDIUM, 'medium one'),
    ], TODAY)
    assert list(open_alerts(con)['severity']) == ['high', 'medium', 'low']


def test_acknowledging_clears_the_list(con):
    raise_alerts(con, [Alert('AAPL', 'c', Severity.HIGH, 'something')], TODAY)
    acknowledge(con)
    assert len(open_alerts(con)) == 0


def test_acknowledging_one_company_leaves_the_others(con):
    raise_alerts(con, [Alert('AAPL', 'c', Severity.HIGH, 'a'), Alert('KO', 'c', Severity.HIGH, 'b')], TODAY)
    acknowledge(con, 'AAPL')
    assert list(open_alerts(con)['ticker']) == ['KO']


def test_every_check_is_described():
    """A check nobody can explain is one nobody will trust."""
    from fin_research.monitor.checks import CHECK_DESCRIPTIONS
    assert set(CHECKS) == set(CHECK_DESCRIPTIONS)
