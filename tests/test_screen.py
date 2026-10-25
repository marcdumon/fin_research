"""Tests for screening.

Almost all of the weight here is on point-in-time discipline, because that is the property whose failure is
invisible: a screen that can see the future produces better-looking results, not worse ones, and nothing in
the output says so. The fixtures are built to contain exactly the traps the real data contains -- a report
published before its period ended, and a restatement carrying a later correction under its original date.
"""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from fin_research.screen import market, rules
from fin_research.screen.factors import rank_percentile
from fin_research.screen.rules import Condition, Screen, Weight, apply

MARKET = market.MARKET_ALIAS


def _rows(*records) -> pd.DataFrame:
    """Statement rows in the shape the attached database uses."""
    return pd.DataFrame(records)


@pytest.fixture
def con():
    """An in-memory stand-in for the attached market database, carrying the real data's defects."""
    connection = duckdb.connect()
    connection.execute(f'CREATE SCHEMA {MARKET}')

    frame = _rows(
        # Ordinary: two annual reports, each published after its period ended.
        {'Ticker': 'GOOD', 'Period': 'A', 'Report Date': date(2023, 12, 31),
         'Publish Date': date(2024, 2, 20), 'Restated Date': None, 'IsRestated': False,
         'Revenue': 100.0, 'Gross Profit': 40.0, 'Operating Income (Loss)': 20.0,
         'Net Income': 10.0, 'Shares (Diluted)': 100.0},
        {'Ticker': 'GOOD', 'Period': 'A', 'Report Date': date(2024, 12, 31),
         'Publish Date': date(2025, 2, 20), 'Restated Date': None, 'IsRestated': False,
         'Revenue': 120.0, 'Gross Profit': 50.0, 'Operating Income (Loss)': 25.0,
         'Net Income': 12.0, 'Shares (Diluted)': 100.0},
        # A restatement of the 2023 period, corrected in 2026 but still stamped with the original publish date.
        {'Ticker': 'GOOD', 'Period': 'A', 'Report Date': date(2023, 12, 31),
         'Publish Date': date(2024, 2, 20), 'Restated Date': date(2026, 4, 1), 'IsRestated': True,
         'Revenue': 999.0, 'Gross Profit': 40.0, 'Operating Income (Loss)': 20.0,
         'Net Income': 10.0, 'Shares (Diluted)': 100.0},
        # Impossible: a full year supposedly published nine months before it ended.
        {'Ticker': 'BAD', 'Period': 'A', 'Report Date': date(2025, 12, 31),
         'Publish Date': date(2025, 3, 1), 'Restated Date': None, 'IsRestated': False,
         'Revenue': 500.0, 'Gross Profit': 200.0, 'Operating Income (Loss)': 100.0,
         'Net Income': 50.0, 'Shares (Diluted)': 100.0},
    )
    # Registered explicitly rather than relying on DuckDB finding the local name, which reads as a
    # dead variable to anything analysing the Python alone.
    connection.register('rows', frame)
    connection.execute(f'CREATE TABLE {MARKET}.income AS SELECT * FROM rows')
    return connection


# --- point in time ------------------------------------------------------------
def test_a_report_published_after_the_date_is_invisible(con):
    """The whole point: a screen dated before a filing must not see it."""
    early = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2024, 6, 1))).df()
    assert list(early['report_date']) == [pd.Timestamp(2023, 12, 31)]
    assert early['revenue'].iloc[0] == 100.0


def test_a_later_restatement_does_not_reach_an_earlier_date(con):
    """The restated figure is stamped with the original publish date; only the restated date tells the truth."""
    before = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2025, 6, 1), periods_back=1)).df()
    after = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2026, 6, 1), periods_back=1)).df()

    assert before['revenue'].iloc[0] == 100.0, 'a 2026 correction informed a 2025 screen'
    assert after['revenue'].iloc[0] == 999.0, 'the correction should be visible once it exists' 


def test_a_report_that_predates_its_own_period_is_discarded(con):
    """Around a thousand rows in the real data claim this, and each would hand a screen the future."""
    frame = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2026, 6, 1))).df()
    assert 'BAD' not in set(frame['Ticker'])


def test_stepping_back_a_period_reaches_a_different_report(con):
    """Restatement duplicates made 'this year' and 'last year' the same row, so every growth rate was zero."""
    current = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2025, 6, 1))).df()
    prior = con.execute(market._select(market.INCOME_FIELDS, 'income', date(2025, 6, 1), periods_back=1)).df()

    assert current['report_date'].iloc[0] == pd.Timestamp(2024, 12, 31)
    assert prior['report_date'].iloc[0] == pd.Timestamp(2023, 12, 31)
    assert current['revenue'].iloc[0] != prior['revenue'].iloc[0]


# --- conditions ---------------------------------------------------------------
@pytest.fixture
def universe() -> pd.DataFrame:
    return pd.DataFrame({
        'Ticker': ['A', 'B', 'C', 'D'],
        'sector': ['Technology', 'Financial Services', 'Technology', 'Technology'],
        'earnings_yield': [0.10, 0.20, 0.02, None],
        'roe': [0.20, 0.30, 0.05, 0.25],
        'accruals': [0.01, 0.02, 0.03, 0.04],
    })


def test_a_condition_filters_on_a_floor(universe):
    kept = universe[Condition('earnings_yield', minimum=0.05).mask(universe)]
    assert list(kept['Ticker']) == ['A', 'B']


def test_a_missing_value_does_not_pass_by_default(universe):
    """An unknown is not a pass. Treating it as one lets companies through on absent data."""
    kept = universe[Condition('earnings_yield', minimum=0.05).mask(universe)]
    assert 'D' not in list(kept['Ticker'])

    permissive = universe[Condition('earnings_yield', minimum=0.05, allow_missing=True).mask(universe)]
    assert 'D' in list(permissive['Ticker'])


def test_sectors_can_be_excluded(universe):
    screen = Screen(name='t', exclude_sectors=('Financial Services',), top=10)
    assert 'B' not in list(apply(screen, universe).survivors['Ticker'])


def test_the_funnel_reports_where_the_universe_went(universe):
    """A screen returning nothing must say which condition emptied it, or it is indistinguishable from broken."""
    screen = Screen(name='t', require=(Condition('earnings_yield', minimum=0.9),), top=10)
    result = apply(screen, universe)

    assert len(result.survivors) == 0
    assert result.considered == 4
    assert any(removed > 0 for removed in result.dropped.values())
    assert 'earnings_yield' in result.summary()


# --- ranking ------------------------------------------------------------------
def test_ranking_uses_percentiles_not_raw_values(universe):
    """Raw values on different scales let whichever factor is largest decide the whole ordering."""
    screen = Screen(name='t', rank=(Weight('earnings_yield'), Weight('roe')), top=10)
    scored = apply(screen, universe).survivors
    assert scored['score'].between(0, 1).all()


def test_lower_is_better_inverts_the_ranking(universe):
    ascending = rank_percentile(universe, 'accruals', ascending=True)
    descending = rank_percentile(universe, 'accruals', ascending=False)
    assert ascending.idxmax() == descending.idxmin()


def test_a_screen_naming_an_unknown_factor_fails_loudly():
    """Config typos should not silently produce an empty screen."""
    with pytest.raises(KeyError, match='unknown factor'):
        rules.parse('broken', {'require': [{'factor': 'nonexistent', 'min': 1}]})


def test_shipped_screens_are_valid():
    """The screens that ship must parse and reference real factors."""
    screens = rules.load(Path(__file__).parents[1] / 'screens.toml')
    assert screens
    for screen in screens.values():
        assert screen.require, f'{screen.name} filters nothing'
        assert screen.explain()


def _prices(connection, closes: dict[str, date]) -> None:
    """A price table whose last close per ticker is the given date."""
    frame = pd.DataFrame([{'Ticker': ticker, 'Date': day, 'O': 1.0, 'H': 1.0, 'L': 1.0, 'C': 10.0, 'V': 1,
                           'AC': 10.0} for ticker, day in closes.items()])
    connection.register('price_rows', frame)
    connection.execute(f'CREATE TABLE {MARKET}.prices AS SELECT * FROM price_rows')


def test_a_database_that_is_behind_does_not_make_every_company_look_delisted():
    """The market database stopped on 11 September. Screened on the 22nd, eleven days on, every company fell
    outside the ten-day window, so a screen considered nothing and every screen member looked like it had left."""
    from fin_research.screen.market import last_close

    connection = duckdb.connect()
    connection.execute(f'CREATE SCHEMA {MARKET}')
    _prices(connection, {'LIVE': date(2026, 9, 11), 'ALSO': date(2026, 9, 10), 'GONE': date(2026, 7, 1)})

    current = set(last_close(connection, date(2026, 9, 22))['Ticker'])

    assert current == {'LIVE', 'ALSO'}, 'a company that stopped trading months ago should still be dropped'


def test_the_latest_close_is_judged_as_of_the_date_asked_for():
    """Point in time: a screen dated in the past must not learn that the database carries on beyond it."""
    from fin_research.screen.market import last_close

    connection = duckdb.connect()
    connection.execute(f'CREATE SCHEMA {MARKET}')
    _prices(connection, {'EARLY': date(2025, 6, 30), 'LATE': date(2026, 9, 11)})

    frame = last_close(connection, date(2025, 6, 30))
    assert set(frame['Ticker']) == {'EARLY'}
