"""Tests for the EDGAR submissions client.

Everything here is offline: `respx` intercepts httpx so no test ever reaches sec.gov. The cases worth having
are the ones where EDGAR's shape is surprising -- chunked filing history, and rows that cannot be fetched later.
"""

import asyncio
import json
import time
from datetime import date

import httpx
import pytest
import respx

from fin_research.config import Config, EdgarConfig
from fin_research.ingest import edgar
from fin_research.ingest.edgar import EdgarClient, Filing, _parse_date, _Throttle


@pytest.fixture
def cfg(tmp_path):
    """Config pointing at a throwaway data root, with throttling effectively disabled."""
    return Config(data_root=tmp_path, edgar=EdgarConfig(rate_limit_seconds=0.0, cache_ttl_days=7))


def _submissions(recent: dict, files: list[dict] | None = None) -> dict:
    """Minimal submissions payload in EDGAR's parallel-array shape."""
    return {'cik': 320193, 'filings': {'recent': recent, 'files': files or []}}


# --- pure helpers -------------------------------------------------------------
def test_parse_date_handles_bad_input():
    assert _parse_date('2025-09-27') == date(2025, 9, 27)
    assert _parse_date('') is None
    assert _parse_date(None) is None
    assert _parse_date('not-a-date') is None


def test_filing_urls_strip_dashes_from_accession():
    filing = Filing(cik=320193, accession='0000320193-25-000079', form='10-K', filed=date(2025, 10, 31),
                    report_date=date(2025, 9, 27), primary_document='aapl-20250927.htm')
    assert filing.accession_nodash == '000032019325000079'
    assert filing.primary_url == (
        'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm'
    )
    assert filing.filing_summary_url.endswith('/FilingSummary.xml')


async def test_throttle_paces_concurrent_callers():
    """Ten coroutines through a 20ms gate must take at least the serialised time, not run all at once."""
    throttle = _Throttle(interval=0.02)
    start = time.monotonic()
    await asyncio.gather(*(throttle.acquire() for _ in range(10)))
    assert time.monotonic() - start >= 0.02 * 9


# --- client -------------------------------------------------------------------
@respx.mock
async def test_older_chunks_are_skipped_unless_asked_for(cfg):
    """A large filer has 21 chunks worth tens of megabytes; a normal run must not pay for them."""
    recent = {'form': ['10-K'], 'accessionNumber': ['0000320193-25-000079'],
              'primaryDocument': ['aapl-2025.htm'], 'reportDate': ['2025-09-27'],
              'filingDate': ['2025-10-31'], 'items': ['']}
    chunk = {'form': ['10-K'], 'accessionNumber': ['0000320193-15-000001'],
             'primaryDocument': ['aapl-2015.htm'], 'reportDate': ['2015-09-26'],
             'filingDate': ['2015-10-28'], 'items': ['']}

    respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent, [{'name': 'CIK0000320193-older.json'}])))
    chunk_route = respx.get(edgar.CHUNK_URL.format(filename='CIK0000320193-older.json')).mock(
        return_value=httpx.Response(200, json=chunk))

    async with EdgarClient(cfg) as client:
        default = await client.filings(320193)
        assert chunk_route.call_count == 0
        assert [f.filed for f in default] == [date(2025, 10, 31)]

        full = await client.filings(320193, full_history=True)

    assert [f.filed for f in full] == [date(2025, 10, 31), date(2015, 10, 28)]


@respx.mock
async def test_since_older_than_recent_page_pulls_history(cfg):
    """`since` reaching past what `filings.recent` covers must transparently fetch the chunks."""
    recent = {'form': ['10-K'], 'accessionNumber': ['a-1'], 'primaryDocument': ['new.htm'],
              'reportDate': ['2025-09-27'], 'filingDate': ['2025-10-31'], 'items': ['']}
    chunk = {'form': ['10-K'], 'accessionNumber': ['a-2'], 'primaryDocument': ['old.htm'],
             'reportDate': ['2015-09-26'], 'filingDate': ['2015-10-28'], 'items': ['']}

    respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent, [{'name': 'older.json'}])))
    chunk_route = respx.get(edgar.CHUNK_URL.format(filename='older.json')).mock(
        return_value=httpx.Response(200, json=chunk))

    async with EdgarClient(cfg) as client:
        reaching_back = await client.filings(320193, since=date(2010, 1, 1))
        assert chunk_route.call_count == 1
        assert len(reaching_back) == 2

        recent_only = await client.filings(320193, since=date(2025, 1, 1))

    assert [f.primary_document for f in recent_only] == ['new.htm']


@respx.mock
async def test_filings_filters_by_form_and_drops_unfetchable_rows(cfg):
    """A row with no primary document cannot be fetched or cited, so it must not survive."""
    recent = {'form': ['10-K', '8-K', '10-Q'],
              'accessionNumber': ['a-1', 'a-2', 'a-3'],
              'primaryDocument': ['k.htm', 'e.htm', ''],
              'reportDate': ['2025-09-27', '2025-08-01', '2025-06-28'],
              'filingDate': ['2025-10-31', '2025-08-02', '2025-07-31'],
              'items': ['', '2.02,9.01', '']}
    respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent)))

    async with EdgarClient(cfg) as client:
        assert [f.form for f in await client.filings(320193)] == ['10-K', '8-K']
        events = await client.filings(320193, forms=edgar.EVENT_FORMS)

    assert [f.items for f in events] == ['2.02,9.01']


@respx.mock
async def test_filings_since_excludes_older(cfg):
    recent = {'form': ['10-K', '10-K'], 'accessionNumber': ['a-1', 'a-2'],
              'primaryDocument': ['new.htm', 'old.htm'], 'reportDate': ['2025-09-27', '2015-09-26'],
              'filingDate': ['2025-10-31', '2015-10-28'], 'items': ['', '']}
    respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent)))

    async with EdgarClient(cfg) as client:
        filings = await client.filings(320193, since=date(2020, 1, 1))

    assert [f.primary_document for f in filings] == ['new.htm']


@respx.mock
async def test_second_call_is_served_from_disk_cache(cfg):
    """A fresh cache file must prevent a second network round trip -- that is the whole point of the TTL."""
    recent = {'form': ['10-K'], 'accessionNumber': ['a-1'], 'primaryDocument': ['k.htm'],
              'reportDate': ['2025-09-27'], 'filingDate': ['2025-10-31'], 'items': ['']}
    route = respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent)))

    async with EdgarClient(cfg) as client:
        await client.filings(320193)
        await client.filings(320193)

    assert route.call_count == 1
    assert json.loads((cfg.data_root / 'sec' / 'submissions' / 'CIK0000320193.json').read_text())['cik'] == 320193


@respx.mock
async def test_resolve_cik_is_case_insensitive(cfg):
    respx.get(edgar.TICKERS_URL).mock(return_value=httpx.Response(
        200, json={'0': {'cik_str': 320193, 'ticker': 'AAPL', 'title': 'Apple Inc.'}}))

    async with EdgarClient(cfg) as client:
        assert await client.resolve_cik('aapl') == 320193
        assert await client.resolve_cik('NOPE') is None


async def test_client_outside_context_manager_fails_clearly(cfg):
    with pytest.raises(RuntimeError, match='async context manager'):
        _ = EdgarClient(cfg).client


@respx.mock
async def test_resolve_filer_rejects_a_registrant_with_no_annual_reports(cfg):
    """A reorganised ticker can resolve to a CIK holding only registration statements -- XOM does today."""
    respx.get(edgar.TICKERS_URL).mock(return_value=httpx.Response(
        200, json={'0': {'cik_str': 2115436, 'ticker': 'XOM', 'title': 'Exxon Mobil Corp'}}))
    recent = {'form': ['S-8 POS', '8-K'], 'accessionNumber': ['a-1', 'a-2'],
              'primaryDocument': ['s8.htm', 'e.htm'], 'reportDate': ['', ''],
              'filingDate': ['2026-07-01', '2026-08-28'], 'items': ['', '']}
    respx.get(edgar.SUBMISSIONS_URL.format(cik=2115436)).mock(
        return_value=httpx.Response(200, json=_submissions(recent)))

    async with EdgarClient(cfg) as client:
        with pytest.raises(edgar.NoFilingHistory, match='predecessor CIK'):
            await client.resolve_filer('XOM')


@respx.mock
async def test_resolve_filer_rejects_an_unlisted_ticker(cfg):
    respx.get(edgar.TICKERS_URL).mock(return_value=httpx.Response(200, json={}))
    async with EdgarClient(cfg) as client:
        with pytest.raises(edgar.TickerNotFound):
            await client.resolve_filer('NOPE')


@respx.mock
async def test_resolve_filer_returns_the_cik_when_history_exists(cfg):
    respx.get(edgar.TICKERS_URL).mock(return_value=httpx.Response(
        200, json={'0': {'cik_str': 320193, 'ticker': 'AAPL', 'title': 'Apple Inc.'}}))
    recent = {'form': ['10-K'], 'accessionNumber': ['a-1'], 'primaryDocument': ['k.htm'],
              'reportDate': ['2025-09-27'], 'filingDate': ['2025-10-31'], 'items': ['']}
    respx.get(edgar.SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json=_submissions(recent)))

    async with EdgarClient(cfg) as client:
        assert await client.resolve_filer('AAPL') == 320193
