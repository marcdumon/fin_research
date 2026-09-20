"""Compute the measures a screen ranks on, from point-in-time statements and prices.

Every factor is a ratio of two figures that were both public on the screening date. Growth compares this
year's report against the report that stood one year earlier *as it stood then*, rather than against today's
restated history -- comparing a restated figure to an original one manufactures growth that never happened.

Nothing here is clever. The value of a screen is that it is cheap, reproducible and honest about what was
knowable; sophistication belongs in the reading that follows it.
"""

import logging
from datetime import date

import duckdb
import numpy as np
import pandas as pd

from fin_research.screen import market

log = logging.getLogger(__name__)

# Descriptions are carried alongside the computation so a screen can explain itself in the output, and so a
# factor cannot be added without saying what it means.
FACTOR_DESCRIPTIONS = {
    'market_cap': 'shares outstanding times the last close',
    'earnings_yield': 'net income over market capitalisation',
    'fcf_yield': 'operating cash flow less capital expenditure, over market capitalisation',
    'price_to_book': 'market capitalisation over shareholders equity',
    'roe': 'net income over shareholders equity',
    'gross_margin': 'gross profit over revenue',
    'operating_margin': 'operating income over revenue',
    'net_debt_to_equity': 'debt less cash, over shareholders equity',
    'accruals': 'net income less operating cash flow, over total assets',
    'revenue_growth': 'revenue against the prior annual report as it stood a year ago',
    'momentum': 'total return over twelve months, excluding the most recent one',
}


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide, returning NaN where the denominator is zero, missing or negative-by-definition."""
    clean = denominator.replace(0, np.nan)
    return numerator / clean


def compute(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Build the factor table for every company with enough data, as of a date."""
    current = market.fundamentals(con, as_of)
    prior = market.fundamentals(con, as_of, periods_back=1)
    prices = market.last_close(con, as_of)
    momentum = market.returns(con, as_of)
    profiles = market.profiles(con)

    frame = (current
             .merge(prices, on='Ticker', how='inner')
             .merge(momentum[['Ticker', 'momentum']], on='Ticker', how='left')
             .merge(profiles, on='Ticker', how='left')
             .merge(prior[['Ticker', 'revenue']].rename(columns={'revenue': 'revenue_prior'}),
                    on='Ticker', how='left'))

    frame['market_cap'] = frame['close'] * frame['shares_diluted']
    frame['free_cash_flow'] = frame['operating_cash_flow'] + frame['capex']

    frame['earnings_yield'] = _safe_divide(frame['net_income'], frame['market_cap'])
    frame['fcf_yield'] = _safe_divide(frame['free_cash_flow'], frame['market_cap'])
    frame['price_to_book'] = _safe_divide(frame['market_cap'], frame['total_equity'])
    frame['roe'] = _safe_divide(frame['net_income'], frame['total_equity'])
    frame['gross_margin'] = _safe_divide(frame['gross_profit'], frame['revenue'])
    frame['operating_margin'] = _safe_divide(frame['operating_income'], frame['revenue'])
    frame['net_debt_to_equity'] = _safe_divide(frame['long_term_debt'] - frame['cash'], frame['total_equity'])

    # Earnings not backed by cash. A large positive reading is the classic sign that reported profit is being
    # produced by accounting choices rather than by the business.
    frame['accruals'] = _safe_divide(frame['net_income'] - frame['operating_cash_flow'], frame['total_assets'])
    frame['revenue_growth'] = _safe_divide(frame['revenue'] - frame['revenue_prior'], frame['revenue_prior'].abs())

    # Negative equity makes book-value ratios meaningless rather than extreme, so they are dropped instead of
    # being allowed to rank at the top of a value screen.
    negative_equity = frame['total_equity'] <= 0
    frame.loc[negative_equity, ['price_to_book', 'roe', 'net_debt_to_equity']] = np.nan

    frame['as_of'] = as_of
    log.info(f'{len(frame):,} companies with a price and a published annual report as of {as_of}')
    return frame


def rank_percentile(frame: pd.DataFrame, factor: str, ascending: bool = False) -> pd.Series:
    """Percentile rank of a factor across the universe, ignoring missing values.

    Percentiles rather than raw values, because the factors are on wildly different scales and a combined
    score built from raw numbers is dominated by whichever happens to be largest.
    """
    return frame[factor].rank(pct=True, ascending=ascending, na_option='keep')
