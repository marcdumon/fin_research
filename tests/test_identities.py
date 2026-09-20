"""Tests for the accounting identity checks.

These checks exist to validate the vendored concept mapping, so the tests care most about the three-valued
result: a check that cannot run must say so rather than pass quietly, since a vacuous pass is exactly the
failure the design is meant to rule out.
"""

from datetime import date

from fin_research.ingest.facts import Fact, as_of
from fin_research.ingest.identities import (
    IdentityReport,
    Status,
    check_balance_sheet,
    check_cash_flow,
    check_filer,
    check_gross_profit,
    within_tolerance,
)

END = date(2024, 12, 31)


def _fact(metric: str | None, value: float, tag: str = '', **kwargs) -> Fact:
    """A Fact carrying just the fields the identities read."""
    base = dict(cik=1, accession='a-1', taxonomy='us-gaap', tag=tag or (metric or 'T'), metric=metric,
                statement=None, unit='USD', value=value, period_start=date(2024, 1, 1), period_end=END,
                period_type='A', form='10-K', filed=date(2025, 2, 1))
    return Fact(**(base | kwargs))


# --- tolerance ---------------------------------------------------------------
def test_tolerance_absorbs_filing_rounding_but_not_real_gaps():
    assert within_tolerance(1_000_000_000, 1_000_050_000)
    assert not within_tolerance(1_000_000_000, 1_200_000_000)


def test_tolerance_has_an_absolute_floor_near_zero():
    """Relative tolerance alone makes any difference infinite when the expected value is zero."""
    assert within_tolerance(0.0, 1_000.0)
    assert not within_tolerance(0.0, 50_000.0)


# --- three-valued results ----------------------------------------------------
def test_missing_inputs_yield_skipped_and_name_what_was_absent():
    result = check_balance_sheet([_fact('Total Assets', 100e9)], END)
    assert result.status is Status.SKIPPED
    assert set(result.missing) == {'Total Liabilities', 'Stockholders Equity'}


def test_a_bank_skips_gross_profit_rather_than_failing_it():
    """Banks report no cost of revenue at all; that is not a mapping error."""
    result = check_gross_profit([_fact('Revenue', 100e9)], END)
    assert result.status is Status.SKIPPED
    assert 'Cost of Revenue' in result.missing


def test_balance_sheet_balances():
    facts = [_fact('Total Assets', 300e9), _fact('Total Liabilities', 200e9), _fact('Stockholders Equity', 100e9)]
    assert check_balance_sheet(facts, END).status is Status.PASS


def test_balance_sheet_counts_mezzanine_equity():
    """Redeemable interests belong to neither side; ignoring them looks exactly like a mapping error."""
    facts = [_fact('Total Assets', 310e9), _fact('Total Liabilities', 200e9), _fact('Stockholders Equity', 100e9)]
    assert check_balance_sheet(facts, END).status is Status.FAIL

    facts.append(_fact(None, 10e9, tag='RedeemableNoncontrollingInterestEquityCarryingAmount'))
    assert check_balance_sheet(facts, END).status is Status.PASS


def test_cash_flow_reconciles_with_the_currency_effect():
    """The currency effect is reported on a different base by some filers, so it is searched independently."""
    facts = [
        _fact('Operating Cash Flow', 12_625e6), _fact('Investing Cash Flow', -2_765e6),
        _fact('Financing Cash Flow', -6_786e6),
        _fact(None, -159e6, tag='EffectOfExchangeRateOnCashAndCashEquivalents'),
        _fact(None, 2_915e6, tag='CashCashEquivalentsRestrictedCashAndRestricted'
                                 'CashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect'),
    ]
    assert check_cash_flow(facts, END).status is Status.PASS


def test_cash_flow_adds_back_discontinued_operations():
    """When sections are continuing-operations only, the discontinued portion is tagged separately."""
    facts = [
        _fact('Operating Cash Flow', 100e9, tag='NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'),
        _fact('Investing Cash Flow', -50e9, tag='NetCashProvidedByUsedInInvestingActivitiesContinuingOperations'),
        _fact('Financing Cash Flow', -20e9, tag='NetCashProvidedByUsedInFinancingActivitiesContinuingOperations'),
        _fact(None, 30e9, tag='CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations'),
        _fact(None, 60e9, tag='CashAndCashEquivalentsPeriodIncreaseDecrease'),
    ]
    assert check_cash_flow(facts, END).status is Status.PASS


def test_wrong_sign_convention_would_be_caught():
    """If outflows were stored as filed -- positive -- the statement would stop reconciling. That is the point."""
    facts = [
        _fact('Operating Cash Flow', 100e9), _fact('Investing Cash Flow', 50e9),
        _fact('Financing Cash Flow', -20e9),
        _fact(None, 30e9, tag='CashAndCashEquivalentsPeriodIncreaseDecrease'),
    ]
    assert check_cash_flow(facts, END).status is Status.FAIL


# --- the verdict -------------------------------------------------------------
def test_everything_skipped_is_not_a_clean_bill_of_health():
    """This is the whole reason the result is three-valued."""
    from fin_research.ingest.identities import IdentityResult
    skipped = IdentityResult(name='x', status=Status.SKIPPED, period_end=END, detail='')
    assert IdentityReport(results=(skipped, skipped)).trustworthy is False


def test_a_single_failure_makes_the_filer_untrustworthy():
    from fin_research.ingest.identities import IdentityResult
    good = IdentityResult(name='x', status=Status.PASS, period_end=END, detail='', left=1.0, right=1.0)
    bad = IdentityResult(name='y', status=Status.FAIL, period_end=END, detail='', left=1.0, right=2.0)
    assert IdentityReport(results=(good,)).trustworthy is True
    assert IdentityReport(results=(good, bad)).trustworthy is False


# --- against real payloads ---------------------------------------------------
def test_a_clean_filer_reconciles_on_real_data(aapl_facts):
    report = check_filer(as_of(aapl_facts), limit=2)
    assert report.trustworthy
    assert report.passed >= 4 and not report.failed


def test_a_filer_with_mezzanine_equity_reconciles_on_real_data(unh_facts):
    """This filer's balance sheet is out by 1.6bn until redeemable interests are counted."""
    result = check_balance_sheet(as_of(unh_facts), date(2025, 12, 31))
    assert result.status is Status.PASS
    assert 'redeemable' in result.detail
