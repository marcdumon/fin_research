"""Tests for XBRL fact normalisation.

The cases here are the ones that cost real debugging time: SEC's own period labels being wrong, filers tagging
several anchors for one metric, and outflows arriving as positive magnitudes.
"""

from datetime import date

import pytest
from conftest import company_facts

from fin_research.ingest.facts import (
    OUTFLOW_METRICS,
    TAG_INDEX,
    Fact,
    as_of,
    by_period,
    classify_period,
    find_tag,
    normalise_sign,
    parse_facts,
    periods,
    series,
)


def _fact(**kwargs) -> Fact:
    """A Fact with sensible defaults, so each test states only what it is about."""
    base = dict(cik=1, accession='a-1', taxonomy='us-gaap', tag='T', metric='Revenue', statement='IS',
                unit='USD', value=1.0, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
                period_type='A', form='10-K', filed=date(2025, 2, 1))
    return Fact(**(base | kwargs))


# --- period classification ---------------------------------------------------
def test_span_beats_sec_own_labels():
    """A row tagged fp='FY', form='10-K' can span ninety days. The label lies; the span does not."""
    assert classify_period(date(2018, 7, 1), date(2018, 9, 29), '10-K', date(2018, 11, 5)) == 'Q'
    assert classify_period(date(2024, 9, 29), date(2025, 9, 27), '10-K', date(2025, 10, 31)) == 'A'


@pytest.mark.parametrize(('start', 'end'), [
    (date(2025, 1, 1), date(2025, 6, 30)),    # half year
    (date(2025, 1, 1), date(2025, 9, 30)),    # nine months
])
def test_cumulative_periods_are_discarded(start, end):
    """Year-to-date rows carry no marker of their own and would otherwise pose as annual figures."""
    assert classify_period(start, end, '10-Q', date(2025, 10, 31)) is None


def test_instant_facts_are_classified_by_form():
    assert classify_period(None, date(2025, 9, 27), '10-K', date(2025, 10, 31)) == 'A'
    assert classify_period(None, date(2025, 6, 30), '10-Q', date(2025, 7, 31)) == 'Q'
    assert classify_period(None, date(2025, 6, 30), '8-K', date(2025, 7, 31)) is None


def test_stale_filings_are_rejected():
    """A number filed years after its period is a restatement artefact, not an original report."""
    assert classify_period(date(2019, 1, 1), date(2019, 12, 31), '10-K', date(2025, 1, 1)) is None


# --- signs -------------------------------------------------------------------
def test_outflows_become_negative():
    """Capital expenditure arrives as a positive magnitude and must not be stored that way."""
    assert normalise_sign('CapEx', 8_165_000_000.0) == -8_165_000_000.0
    assert normalise_sign('Dividends Paid', 10_528.0) == -10_528.0
    assert normalise_sign('Operating Cash Flow', 5_470.0) == 5_470.0
    assert normalise_sign(None, -42.0) == -42.0


def test_outflow_normalisation_is_idempotent():
    """An already-negative outflow must not be flipped back."""
    assert normalise_sign('CapEx', -100.0) == -100.0


def test_every_outflow_metric_is_a_real_metric():
    from fin_research.ingest.anchors import METRIC_ANCHORS
    assert set(METRIC_ANCHORS) >= OUTFLOW_METRICS


# --- anchor priority ---------------------------------------------------------
def test_tag_index_records_anchor_position():
    """Position in the anchor list is what decides between competing concepts later."""
    metric, statement, rank = TAG_INDEX['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest']
    assert (metric, statement, rank) == ('Stockholders Equity', 'BS', 0)
    assert TAG_INDEX['StockholdersEquity'][2] == 1


def test_by_period_prefers_the_higher_priority_anchor():
    """A REIT tags both parent-only and including-NCI equity; only the latter makes the balance sheet balance."""
    facts = [
        _fact(tag='StockholdersEquity', metric='Stockholders Equity', anchor_rank=1, value=53_193.0),
        _fact(tag='StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
              metric='Stockholders Equity', anchor_rank=0, value=57_754.0),
    ]
    assert by_period(facts, date(2024, 12, 31))['Stockholders Equity'].value == 57_754.0


# --- point in time -----------------------------------------------------------
def test_as_of_hides_what_had_not_been_filed():
    """A restatement published later must not inform an earlier decision."""
    original = _fact(value=100.0, filed=date(2025, 2, 1), accession='a-1')
    restated = _fact(value=110.0, filed=date(2026, 2, 1), accession='a-2')

    assert as_of([original, restated])[0].value == 110.0
    assert as_of([original, restated], cutoff=date(2025, 6, 1))[0].value == 100.0
    assert as_of([original, restated], cutoff=date(2024, 1, 1)) == []


def test_as_of_keeps_every_concept_not_just_one_per_metric():
    """Deduping by metric silently discarded anchors and broke the balance sheet; dedupe by concept."""
    facts = [
        _fact(tag='StockholdersEquity', metric='Stockholders Equity', anchor_rank=1),
        _fact(tag='StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
              metric='Stockholders Equity', anchor_rank=0),
    ]
    assert len(as_of(facts)) == 2


def test_find_tag_honours_the_order_it_is_given():
    facts = [_fact(tag='Second', metric=None), _fact(tag='First', metric=None)]
    assert find_tag(facts, ('First', 'Second'), date(2024, 12, 31)).tag == 'First'
    assert find_tag(facts, ('Missing',), date(2024, 12, 31)) is None


# --- against a real payload --------------------------------------------------
def test_real_payload_maps_to_canonical_metrics(aapl_facts):
    metrics = {f.metric for f in aapl_facts if f.metric}
    assert {'Revenue', 'Operating Cash Flow', 'Total Assets', 'CapEx'} <= metrics


def test_real_capex_is_stored_negative(aapl_facts):
    values = [f.value for f in series(as_of(aapl_facts), 'CapEx')]
    assert values and all(v < 0 for v in values)


def test_real_periods_are_annual_and_ordered(aapl_facts):
    ends = periods(as_of(aapl_facts))
    assert ends == sorted(ends)
    assert all(e.year >= 2023 for e in ends)


def test_verification_concepts_survive_the_mapped_only_filter():
    """They have no canonical metric, so an earlier version dropped them and the checks went blind."""
    facts = parse_facts(company_facts('unh'), cik=731766)
    tags = {f.tag for f in facts if f.metric is None}
    assert any('PeriodIncreaseDecrease' in t for t in tags)
    assert any('RedeemableNoncontrollingInterest' in t for t in tags)
