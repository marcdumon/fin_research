"""Tests for the DuckDB store.

Two properties carry the most weight. Re-ingesting must converge rather than duplicate, because a failed run
has to be safe to simply repeat. And a stored section must slice out of the stored document exactly as it did
in memory -- if that ever drifts, every citation in the system is quietly wrong.
"""

from datetime import date

import pandas as pd
import pytest
from conftest import filing_html

from fin_research.config import Config
from fin_research.ingest import store as store_module
from fin_research.ingest.edgar import Filing
from fin_research.ingest.facts import Fact
from fin_research.ingest.sections import split
from fin_research.ingest.store import (
    connect,
    count_rows,
    digest,
    section_text,
    store,
    store_document,
    store_facts,
    store_filings,
    store_sections,
)
from fin_research.ingest.text import clean

FILING = Filing(cik=320193, accession='0000320193-25-000079', form='10-K', filed=date(2025, 10, 31),
                report_date=date(2025, 9, 27), primary_document='aapl-20250927.htm')


@pytest.fixture
def con(tmp_path):
    """A throwaway store."""
    with connect(Config(data_root=tmp_path)) as connection:
        yield connection


def _fact(**kwargs) -> Fact:
    base = dict(cik=320193, accession='a-1', taxonomy='us-gaap', tag='Revenues', metric='Revenue',
                statement='IS', unit='USD', value=100.0, period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31), period_type='A', form='10-K', filed=date(2025, 2, 1))
    return Fact(**(base | kwargs))


# --- identifiers -------------------------------------------------------------
def test_digest_is_stable_and_discriminating():
    assert digest('a', 'b') == digest('a', 'b')
    assert digest('a', 'b') != digest('b', 'a')


# --- upsert ------------------------------------------------------------------
def test_schema_is_created_on_connect(con):
    for table in ('filings', 'documents', 'sections', 'facts'):
        assert count_rows(con, table) == 0


def test_store_is_idempotent(con):
    """Re-ingesting a company must converge, so a failed run can simply be repeated."""
    assert store_filings(con, [FILING], 'AAPL') == 1
    assert store_filings(con, [FILING], 'AAPL') == 1


def test_store_updates_matched_rows(con):
    store_filings(con, [FILING], 'AAPL')
    store_filings(con, [FILING], 'AAPLX')
    assert store_module.scalar(con, 'SELECT ticker FROM filings') == 'AAPLX'


def test_storing_nothing_is_not_an_error(con):
    assert store(con, 'filings', pd.DataFrame(), ['accession']) == 0


# --- point-in-time and provenance --------------------------------------------
def test_documents_record_the_cleaner_version_and_filing_date(con):
    """Offsets are only meaningful against one cleaner version, and the filing date cannot be recovered later."""
    document = clean('<p>Hello world</p>')
    store_document(con, FILING, 'x.htm', 'primary', document)

    version, filed = con.execute('SELECT cleaner_version, filed FROM documents').fetchone()
    assert version == document.cleaner_version
    assert filed == FILING.filed


def test_facts_keep_the_filing_date_that_makes_them_point_in_time(con):
    store_facts(con, [_fact()], 'AAPL')
    assert store_module.scalar(con, 'SELECT filed FROM facts') == date(2025, 2, 1)


def test_restatement_updates_in_place_rather_than_duplicating(con):
    """Same concept, same period, same filing -- a corrected value replaces rather than accumulating."""
    store_facts(con, [_fact(value=100.0)], 'AAPL')
    store_facts(con, [_fact(value=110.0)], 'AAPL')
    assert count_rows(con, 'facts') == 1
    assert store_module.scalar(con, 'SELECT value FROM facts') == 110.0


def test_the_same_concept_in_different_filings_is_kept_separately(con):
    store_facts(con, [_fact(accession='a-1'), _fact(accession='a-2')], 'AAPL')
    assert count_rows(con, 'facts') == 2


# --- the property every citation depends on ----------------------------------
def test_stored_sections_slice_out_of_the_stored_document(con):
    """The whole citation contract rests on this round trip holding through persistence."""
    document = clean(filing_html('aapl_tech'))
    document_id = store_document(con, FILING, FILING.primary_document, 'primary', document)
    result = split(document)
    store_sections(con, document_id, FILING.accession, result)

    expected = result.by_item('1A')
    stored = section_text(con, digest(document_id, '1A'))
    assert stored == expected.text(document.text)
    assert stored.lower().startswith('item 1a')


def test_redirect_state_survives_storage(con):
    """'This item points elsewhere' must not be confused with 'this item is empty'."""
    document = clean(filing_html('jpm_bank'))
    document_id = store_document(con, FILING, 'jpm.htm', 'primary', document)
    store_sections(con, document_id, FILING.accession, split(document))

    is_redirect, target = con.execute(
        "SELECT is_redirect, redirect_to FROM sections WHERE item_key = '7'").fetchone()
    assert is_redirect is True
    assert target is None


def test_missing_market_database_is_survivable(con, tmp_path):
    """A research run without prices is degraded, not broken -- but it must know."""
    assert store_module.attach_market_data(con, tmp_path / 'absent.duckdb') is False
