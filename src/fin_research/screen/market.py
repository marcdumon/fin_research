"""Point-in-time views over the attached market data.

Every query here filters on when a number was *published*, not the period it describes. That distinction is
the whole difference between a screen you can trust and one that quietly knows the future: a 2023 screen that
reads a figure restated in 2025 will look brilliant and be worthless.

The attached database carries both the original filing and later restatements of the same period, so rows are
deduplicated to the most recently published version that existed on the date being screened.
"""

import logging
from datetime import date

import duckdb
import pandas as pd

from fin_research.ingest.store import MARKET_ALIAS

log = logging.getLogger(__name__)

# The fundamentals worth screening on, and the column each lives in. Names carry spaces and ampersands, so
# they are quoted everywhere they appear.
INCOME_FIELDS = {
    'revenue': 'Revenue',
    'gross_profit': 'Gross Profit',
    'operating_income': 'Operating Income (Loss)',
    'net_income': 'Net Income',
    'shares_diluted': 'Shares (Diluted)',
}

BALANCE_FIELDS = {
    'total_assets': 'Total Assets',
    'total_equity': 'Total Equity',
    'total_liabilities': 'Total Liabilities',
    'cash': 'Cash, Cash Equivalents & Short Term Investments',
    'long_term_debt': 'Long Term Debt',
}

CASHFLOW_FIELDS = {
    'operating_cash_flow': 'Net Cash from Operating Activities',
    'capex': 'Change in Fixed Assets & Intangibles',
}


# When a row's figures actually became public. A restated row keeps the original filing's publish date while
# carrying the corrected numbers, so filtering on publish date alone lets a later correction inform an earlier
# decision -- one company here has a restatement stamped with a 2023 publish date and a 2026 restated date.
KNOWN_ON = ('CASE WHEN IsRestated THEN coalesce("Restated Date", "Publish Date") '
            'ELSE "Publish Date" END')

# A report cannot become known before the period it covers has ended. Around a thousand annual rows in the
# source violate this, and each one would hand a screen figures from the future. The test applies to whichever
# date actually governs the row, not to the publish date alone -- a restated row is governed by its restated
# date, and checking the wrong column leaves a leak behind. Enforced inside `_select`.


def _select(fields: dict[str, str], table: str, as_of: date, periods_back: int = 0) -> str:
    """SQL for one statement, point-in-time, one row per ticker.

    `periods_back` steps to an earlier annual report as it stood on the same date, which is how a growth rate
    is computed without comparing today's restated figure against an original one.
    """
    columns = ',\n               '.join(f'"{source}" AS {alias}' for alias, source in fields.items())
    return f"""
        SELECT Ticker, report_date, publish_date, {', '.join(fields)}
        FROM (
            SELECT Ticker, report_date, publish_date, {', '.join(fields)},
                   row_number() OVER (PARTITION BY Ticker ORDER BY report_date DESC) AS recency
            FROM (
                -- One row per report: the same annual period appears once as originally filed and again for
                -- each restatement, so the best version available on the date is chosen before periods are
                -- counted back. Skipping this makes "last year" and "this year" the same report, and every
                -- growth rate comes out as exactly zero.
                SELECT Ticker, "Report Date" AS report_date, known_on AS publish_date, {columns},
                       row_number() OVER (
                           PARTITION BY Ticker, "Report Date" ORDER BY known_on DESC
                       ) AS version
                FROM (
                    SELECT *, {KNOWN_ON} AS known_on FROM {MARKET_ALIAS}.{table}
                    WHERE Period = 'A'
                )
                WHERE known_on >= report_date AND known_on <= DATE '{as_of}'
            )
            WHERE version = 1
        )
        WHERE recency = {periods_back + 1}
    """


# An annual report older than this means the company stopped reporting: acquired, delisted, or gone dark.
# Carrying it forward is how a screen recommends something that no longer trades.
STALE_REPORT_MONTHS = 18


def fundamentals(con: duckdb.DuckDBPyConnection, as_of: date, periods_back: int = 0,
                 stale_months: int = STALE_REPORT_MONTHS) -> pd.DataFrame:
    """Latest annual statements for every company, as they stood on `as_of`.

    Companies whose most recent annual report predates `stale_months` are dropped: they have stopped
    reporting, and a screen that keeps them ends up ranking businesses that no longer exist.

    That test applies only to the latest report. A prior-year report is two years old by construction, so
    applying it there would discard the very comparison a growth rate needs -- which it did, silently, until
    coverage fell to two per cent.
    """
    freshness = (f"WHERE i.report_date >= DATE '{as_of}' - INTERVAL {stale_months} MONTH"
                 if periods_back == 0 else '')
    income = _select(INCOME_FIELDS, 'income', as_of, periods_back)
    balance = _select(BALANCE_FIELDS, 'balance', as_of, periods_back)
    cashflow = _select(CASHFLOW_FIELDS, 'cashflow', as_of, periods_back)

    return con.execute(f"""
        SELECT i.*, b.* EXCLUDE (Ticker, report_date, publish_date),
                     c.* EXCLUDE (Ticker, report_date, publish_date)
        FROM ({income}) i
        LEFT JOIN ({balance}) b USING (Ticker)
        LEFT JOIN ({cashflow}) c USING (Ticker)
        {freshness}
    """).df()


def last_close(con: duckdb.DuckDBPyConnection, as_of: date, stale_days: int = 10) -> pd.DataFrame:
    """Most recent close on or before `as_of`, dropping names that stopped trading.

    A price that is weeks old usually means the company was delisted or acquired. Carrying those forward is
    how a screen ends up recommending something that no longer exists.

    Staleness is measured against the latest close the database holds on or before `as_of`, not against the
    calendar. The market database is updated separately and runs days behind; measured against the date, a
    database eleven days behind made every company look delisted, so a screen considered nothing and the
    watchlist patrol read every screen member as having left its screen. A company is stale when it stopped
    trading while others carried on, which is what this now asks.
    """
    return con.execute(f"""
        WITH latest AS (
            SELECT max(Date) AS day FROM {MARKET_ALIAS}.prices WHERE Date <= DATE '{as_of}'
        )
        SELECT Ticker, last(C ORDER BY Date) AS close, max(Date) AS price_date
        FROM {MARKET_ALIAS}.prices, latest
        WHERE Date <= DATE '{as_of}' AND Date >= latest.day - INTERVAL 90 DAY
        GROUP BY Ticker, latest.day
        HAVING max(Date) >= latest.day - INTERVAL {stale_days} DAY
    """).df()


def returns(con: duckdb.DuckDBPyConnection, as_of: date, months: int = 12, skip_months: int = 1) -> pd.DataFrame:
    """Total return over a window ending `skip_months` before `as_of`.

    The skipped month is deliberate. Momentum measured to the last day reverses over short horizons, so the
    convention is to leave the most recent month out.
    """
    return con.execute(f"""
        WITH span AS (
            SELECT Ticker, Date, AC FROM {MARKET_ALIAS}.prices
            WHERE Date <= DATE '{as_of}' - INTERVAL {skip_months} MONTH
              AND Date >= DATE '{as_of}' - INTERVAL {months + skip_months} MONTH
        )
        SELECT Ticker,
               last(AC ORDER BY Date) / nullif(first(AC ORDER BY Date), 0) - 1 AS momentum,
               count(*) AS trading_days
        FROM span GROUP BY Ticker
        HAVING count(*) > {(months - 1) * 18}
    """).df()


def profiles(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Sector and industry for every listed company."""
    return con.execute(f"""
        SELECT Ticker, CompanyName AS name, Sector AS sector, Industry AS industry, CIK AS cik
        FROM {MARKET_ALIAS}.companies
    """).df()
