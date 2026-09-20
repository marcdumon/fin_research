"""Run every check over the watchlist and record what changed.

Companies are patrolled concurrently, bounded well below the SEC's limit because a patrol is a background
job and has no reason to compete with whatever else is running. The factor table is computed once for the
whole pass rather than per company: it covers the entire universe anyway, and recomputing it per ticker would
dominate the time.

A check that fails takes its company down with it and nothing else. A patrol that aborts because one filer
has an unreadable filing is a patrol that stops telling you about the other forty.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

from fin_research.config import Config
from fin_research.ingest.edgar import EdgarClient, NoFilingHistory, TickerNotFound
from fin_research.ingest.store import MARKET_ALIAS
from fin_research.monitor.checks import CHECKS, Watch
from fin_research.monitor.store import Alert, raise_alerts, watchlist
from fin_research.screen import factors, rules

log = logging.getLogger(__name__)

MAX_CONCURRENT = 4


@dataclass
class PatrolResult:
    """What one pass found."""

    as_of: date
    patrolled: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        """The pass in a few lines."""
        by_severity: dict[str, int] = {}
        for alert in self.alerts:
            by_severity[alert.severity.value] = by_severity.get(alert.severity.value, 0) + 1
        counts = ', '.join(f'{count} {level}' for level, count in by_severity.items()) or 'nothing new'
        lines = [f'{len(self.patrolled)} companies patrolled as of {self.as_of}: {counts}']
        if self.skipped:
            lines.append(f'  {len(self.skipped)} could not be checked: '
                         + ', '.join(f'{t} ({why})' for t, why in list(self.skipped.items())[:4]))
        return '\n'.join(lines)


def screen_members(con: duckdb.DuckDBPyConnection, as_of: date, screens_path: Path) -> dict[str, set[str]]:
    """Which companies currently pass each configured screen.

    Computed once per pass. Returns an empty mapping when the market data is unavailable, which turns the
    membership check into a no-op rather than failing the whole patrol.
    """
    try:
        table = factors.compute(con, as_of)
    except duckdb.Error as error:
        log.warning(f'no market data, skipping screen membership: {error}')
        return {}

    members = {}
    for name, screen in rules.load(screens_path).items():
        members[name] = set(rules.apply(screen, table).survivors['Ticker'])
    return members


async def _patrol_one(ticker: str, con: duckdb.DuckDBPyConnection, client: EdgarClient, as_of: date,
                      members: dict[str, set[str]], semaphore: asyncio.Semaphore,
                      only: tuple[str, ...] | None) -> tuple[str, list[Alert], str | None]:
    """Run every check for one company."""
    async with semaphore:
        try:
            cik = await client.resolve_filer(ticker)
        except (TickerNotFound, NoFilingHistory) as error:
            return ticker, [], str(error).split('.')[0]

        watch = Watch(con=con, client=client, ticker=ticker, as_of=as_of, cik=cik, screen_members=members)
        found: list[Alert] = []

        for name, check in CHECKS.items():
            if only and name not in only:
                continue
            try:
                result = check(watch)
                found.extend(await result if inspect.isawaitable(result) else result)
            except Exception as error:
                # One broken check must not cost the company its other four.
                log.warning(f'{ticker}: check {name} failed: {type(error).__name__}: {error}')

        return ticker, found, None


async def patrol(cfg: Config, con: duckdb.DuckDBPyConnection, as_of: date, tickers: list[str] | None = None,
                 screens_path: Path | None = None, only: tuple[str, ...] | None = None) -> PatrolResult:
    """Check every watched company and record what changed."""
    names = tickers or watchlist(con)
    outcome = PatrolResult(as_of=as_of)
    if not names:
        return outcome

    members = screen_members(con, as_of, screens_path) if screens_path else {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with EdgarClient(cfg) as client:
        results = await asyncio.gather(*(
            _patrol_one(ticker, con, client, as_of, members, semaphore, only) for ticker in names))

    for ticker, alerts, skipped in results:
        if skipped:
            outcome.skipped[ticker] = skipped
            continue
        outcome.patrolled.append(ticker)
        outcome.alerts.extend(alerts)

    raise_alerts(con, outcome.alerts, as_of)
    return outcome


def attach_market(con: duckdb.DuckDBPyConnection, cfg: Config) -> bool:
    """Attach the market data if it is available, since two checks need prices."""
    from fin_research.ingest.store import attach_market_data

    if not cfg.investalyze_db:
        return False
    already = con.execute('SELECT count(*) FROM duckdb_databases() WHERE database_name = ?',
                          [MARKET_ALIAS]).fetchone()[0]
    return True if already else attach_market_data(con, cfg.investalyze_db)
