"""Storage for the watchlist, what was last seen, and what changed.

Detecting change needs a memory of the previous state, so each check records a fingerprint of what it saw.
The next pass compares against that rather than against a recomputed history, which keeps a detector honest
about *when* it noticed something -- a change found today is dated today, even if the underlying event is
older, because that is when it became actionable.

Alerts are kept rather than merely printed. An alert nobody was at the terminal for is exactly the one worth
keeping, and a record of what was raised is what makes it possible to ask later whether the monitoring was
any good.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

import duckdb
import pandas as pd

from fin_research.ingest.store import digest, store

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker   VARCHAR PRIMARY KEY,
    note     VARCHAR,
    added_at TIMESTAMP NOT NULL,
    active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS observations (
    ticker      VARCHAR NOT NULL,
    check_name  VARCHAR NOT NULL,
    fingerprint VARCHAR NOT NULL,
    payload     VARCHAR NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (ticker, check_name)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id   VARCHAR PRIMARY KEY,
    ticker     VARCHAR NOT NULL,
    check_name VARCHAR NOT NULL,
    severity   VARCHAR NOT NULL,
    headline   VARCHAR NOT NULL,
    detail     VARCHAR,
    reference  VARCHAR,
    as_of      DATE NOT NULL,
    created_at TIMESTAMP NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_alerts_ticker ON alerts (ticker, created_at);
"""


class Severity(StrEnum):
    """How much attention an alert deserves.

    Three levels rather than a score: a number invites averaging, and the only decision this feeds is whether
    to go and look.
    """

    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


@dataclass(frozen=True)
class Alert:
    """Something changed that is worth knowing about."""

    ticker: str
    check_name: str
    severity: Severity
    headline: str
    detail: str = ''
    reference: str = ''

    @property
    def alert_id(self) -> str:
        """Content-addressed, so the same change raised twice collapses to one row."""
        return digest(self.ticker, self.check_name, self.headline, self.reference)


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the monitoring tables if they do not exist."""
    con.execute(SCHEMA)


def _has_table(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True if a monitoring table exists.

    Read paths test for the table rather than creating it, because a reader may hold a read-only connection
    -- the dashboard does -- and `CREATE TABLE` there raises. An earlier version called `ensure_schema`
    everywhere and swallowed that error, so the dashboard reported no alerts while dozens were stored.
    """
    return con.execute('SELECT 1 FROM information_schema.tables WHERE table_name = ?',
                       [table]).fetchone() is not None


def watch(con: duckdb.DuckDBPyConnection, tickers: list[str], note: str = '') -> int:
    """Add companies to the watchlist."""
    ensure_schema(con)
    frame = pd.DataFrame([{'ticker': t.upper(), 'note': note, 'added_at': datetime.now(UTC), 'active': True}
                          for t in tickers])
    return store(con, 'watchlist', frame, ['ticker'])


def unwatch(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> None:
    """Stop patrolling companies, without losing what was already recorded about them."""
    ensure_schema(con)
    for ticker in tickers:
        con.execute('UPDATE watchlist SET active = FALSE WHERE ticker = ?', [ticker.upper()])


def watchlist(con: duckdb.DuckDBPyConnection, include_inactive: bool = False) -> list[str]:
    """Companies currently being patrolled."""
    if not _has_table(con, 'watchlist'):
        return []
    clause = '' if include_inactive else 'WHERE active'
    return [row[0] for row in con.execute(f'SELECT ticker FROM watchlist {clause} ORDER BY ticker').fetchall()]


def last_seen(con: duckdb.DuckDBPyConnection, ticker: str, check_name: str) -> dict | None:
    """What a check recorded the last time it ran, or None if it never has."""
    if not _has_table(con, 'observations'):
        return None
    row = con.execute('SELECT fingerprint, payload, observed_at FROM observations '
                      'WHERE ticker = ? AND check_name = ?', [ticker, check_name]).fetchone()
    if row is None:
        return None
    return {'fingerprint': row[0], 'payload': json.loads(row[1]), 'observed_at': row[2]}


def record(con: duckdb.DuckDBPyConnection, ticker: str, check_name: str, fingerprint: str,
           payload: dict) -> None:
    """Remember what a check saw, so the next pass can tell whether it moved."""
    frame = pd.DataFrame([{'ticker': ticker, 'check_name': check_name, 'fingerprint': fingerprint,
                           'payload': json.dumps(payload, default=str), 'observed_at': datetime.now(UTC)}])
    store(con, 'observations', frame, ['ticker', 'check_name'])


def raise_alerts(con: duckdb.DuckDBPyConnection, alerts: list[Alert], as_of: date) -> int:
    """Record alerts, collapsing any that repeat a change already raised."""
    if not alerts:
        return 0
    ensure_schema(con)
    frame = pd.DataFrame([{
        'alert_id': a.alert_id, 'ticker': a.ticker, 'check_name': a.check_name,
        'severity': a.severity.value, 'headline': a.headline, 'detail': a.detail,
        'reference': a.reference, 'as_of': as_of, 'created_at': datetime.now(UTC), 'acknowledged': False,
    } for a in alerts])
    store(con, 'alerts', frame, ['alert_id'])
    return len(alerts)


def open_alerts(con: duckdb.DuckDBPyConnection, ticker: str | None = None, limit: int = 100) -> pd.DataFrame:
    """Alerts nobody has acknowledged, most severe first."""
    if not _has_table(con, 'alerts'):
        return pd.DataFrame()
    clause = 'AND ticker = ?' if ticker else ''
    params = [ticker, limit] if ticker else [limit]
    return con.execute(f"""
        SELECT ticker, check_name, severity, headline, detail, reference, as_of, created_at
        FROM alerts WHERE NOT acknowledged {clause}
        ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC
        LIMIT ?
    """, params).df()


def acknowledge(con: duckdb.DuckDBPyConnection, ticker: str | None = None) -> int:
    """Mark alerts as seen."""
    if not _has_table(con, 'alerts'):
        return 0
    if ticker:
        con.execute('UPDATE alerts SET acknowledged = TRUE WHERE ticker = ?', [ticker.upper()])
    else:
        con.execute('UPDATE alerts SET acknowledged = TRUE')
    return int(con.execute('SELECT count(*) FROM alerts WHERE acknowledged').fetchone()[0])
