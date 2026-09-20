"""What counts as a change worth knowing about.

Every check here is mechanical. That is deliberate: a monitor runs over a whole watchlist on a schedule, so
anything that costs money per company per pass will either be switched off or quietly bankrupt you. A new
filing, an 8-K item code, a price move, a screen a company just fell out of, statements that stopped adding
up -- all of these are facts about data, and none of them needs a model to notice.

The expensive reading comes afterwards and only for what these raise. A monitor's job is to decide where to
point attention, not to supply it.

Each check compares what it sees now against a fingerprint of what it saw last time. First contact records
the state and raises nothing: a company added to the watchlist today has not *changed*, and alerting on its
entire filing history would bury the one thing that actually moves tomorrow.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import pandas as pd

from fin_research.ingest.edgar import EVENT_FORMS, PERIODIC_FORMS, EdgarClient
from fin_research.ingest.facts import as_of as facts_as_of
from fin_research.ingest.facts import fetch_facts
from fin_research.ingest.identities import check_filer
from fin_research.ingest.news import ITEM_CODES, MATERIAL_ITEMS, parse_items
from fin_research.ingest.store import digest
from fin_research.monitor.store import Alert, Severity, last_seen, record

log = logging.getLogger(__name__)

# A move this large over the lookback is worth a look whichever way it went.
PRICE_MOVE_THRESHOLD = 0.15
PRICE_LOOKBACK_DAYS = 30

# 8-K items that reliably mean something has gone wrong, as opposed to merely happened.
ADVERSE_ITEMS = frozenset({'1.03', '2.06', '3.01', '4.01', '4.02', '5.02'})


@dataclass
class Watch:
    """Everything a check needs to look at one company."""

    con: duckdb.DuckDBPyConnection
    client: EdgarClient
    ticker: str
    as_of: date
    cik: int | None = None
    # Shared across the whole pass, because recomputing it per company would dominate the run.
    factors: pd.DataFrame | None = None
    screen_members: dict[str, set[str]] = field(default_factory=dict)


async def new_filings(watch: Watch) -> list[Alert]:
    """A periodic report the company had not filed last time we looked."""
    if watch.cik is None:
        return []

    filings = await watch.client.filings(watch.cik, forms=PERIODIC_FORMS, limit=8)
    visible = [f for f in filings if f.filed <= watch.as_of]

    # An empty state is still a baseline. Returning early without recording one means the first filing that
    # does appear looks like first contact and is swallowed, which is the one alert this check exists for.
    fingerprint = digest(*(f.accession for f in visible))
    previous = last_seen(watch.con, watch.ticker, 'new_filings')
    record(watch.con, watch.ticker, 'new_filings', fingerprint,
           {'accessions': [f.accession for f in visible]})

    if previous is None or previous['fingerprint'] == fingerprint:
        return []

    known = set(previous['payload'].get('accessions', []))
    fresh = [f for f in visible if f.accession not in known]
    return [Alert(ticker=watch.ticker, check_name='new_filings',
                  severity=Severity.HIGH if f.form.startswith('10-K') else Severity.MEDIUM,
                  headline=f'{f.form} filed {f.filed}',
                  detail=f'period ending {f.report_date}' if f.report_date else '',
                  reference=f.primary_url)
            for f in fresh]


async def material_events(watch: Watch) -> list[Alert]:
    """An 8-K filed under an item number that changes what the company is worth."""
    if watch.cik is None:
        return []

    filings = await watch.client.filings(watch.cik, forms=EVENT_FORMS, limit=12)
    visible = [f for f in filings if f.filed <= watch.as_of]
    events = [(f, parse_items(f.items)) for f in visible]
    material = [(f, items) for f, items in events if set(items) & MATERIAL_ITEMS]

    fingerprint = digest(*(f.accession for f, _ in material))
    previous = last_seen(watch.con, watch.ticker, 'material_events')
    record(watch.con, watch.ticker, 'material_events', fingerprint,
           {'accessions': [f.accession for f, _ in material]})

    if previous is None or previous['fingerprint'] == fingerprint:
        return []

    known = set(previous['payload'].get('accessions', []))
    alerts = []
    for filing, items in material:
        if filing.accession in known:
            continue
        described = '; '.join(ITEM_CODES[i] for i in items if i in ITEM_CODES)
        adverse = bool(set(items) & ADVERSE_ITEMS)
        alerts.append(Alert(
            ticker=watch.ticker, check_name='material_events',
            severity=Severity.HIGH if adverse else Severity.MEDIUM,
            headline=f'8-K filed {filing.filed}: {described[:90]}',
            detail=f'items {", ".join(items)}', reference=filing.primary_url))
    return alerts


def price_move(watch: Watch) -> list[Alert]:
    """A large move over the lookback window, in either direction."""
    from fin_research.ingest.store import MARKET_ALIAS

    try:
        row = watch.con.execute(f"""
            WITH span AS (
                SELECT Date, AC FROM {MARKET_ALIAS}.prices
                WHERE Ticker = ? AND Date <= DATE '{watch.as_of}'
                  AND Date >= DATE '{watch.as_of}' - INTERVAL {PRICE_LOOKBACK_DAYS} DAY
            )
            SELECT last(AC ORDER BY Date) / nullif(first(AC ORDER BY Date), 0) - 1, count(*) FROM span
        """, [watch.ticker]).fetchone()
    except duckdb.Error:
        return []

    if row is None or row[0] is None or row[1] < 5:
        return []

    change = float(row[0])
    fingerprint = digest(watch.ticker, round(change, 2))
    previous = last_seen(watch.con, watch.ticker, 'price_move')
    record(watch.con, watch.ticker, 'price_move', fingerprint, {'change': change})

    if abs(change) < PRICE_MOVE_THRESHOLD or (previous and previous['fingerprint'] == fingerprint):
        return []

    direction = 'rose' if change > 0 else 'fell'
    return [Alert(ticker=watch.ticker, check_name='price_move', severity=Severity.MEDIUM,
                  headline=f'price {direction} {abs(change):.0%} over {PRICE_LOOKBACK_DAYS} days',
                  detail='a move this size usually has a reason worth finding')]


def screen_membership(watch: Watch) -> list[Alert]:
    """A company that has just entered or left one of the configured screens.

    Entering is the more interesting direction: it means the numbers crossed a threshold someone set
    deliberately, which is a better reason to look than a price move with no explanation.
    """
    if not watch.screen_members:
        return []

    current = {name for name, members in watch.screen_members.items() if watch.ticker in members}
    fingerprint = digest(*sorted(current))
    previous = last_seen(watch.con, watch.ticker, 'screen_membership')
    record(watch.con, watch.ticker, 'screen_membership', fingerprint, {'screens': sorted(current)})

    if previous is None or previous['fingerprint'] == fingerprint:
        return []

    was = set(previous['payload'].get('screens', []))
    alerts = []
    for name in sorted(current - was):
        alerts.append(Alert(ticker=watch.ticker, check_name='screen_membership', severity=Severity.MEDIUM,
                            headline=f'now passes the {name} screen',
                            detail='its numbers crossed a threshold that was set deliberately'))
    for name in sorted(was - current):
        alerts.append(Alert(ticker=watch.ticker, check_name='screen_membership', severity=Severity.LOW,
                            headline=f'no longer passes the {name} screen'))
    return alerts


async def statements_reconcile(watch: Watch) -> list[Alert]:
    """The company's own reported figures have stopped adding up.

    Usually this means a restatement, which is among the most informative things a filer ever does and among
    the least announced. It can also mean the concept mapping has met a filer it does not handle, which is
    worth knowing for a different reason -- either way the numbers should not be trusted until someone looks.
    """
    if watch.cik is None:
        return []

    facts = facts_as_of(await fetch_facts(watch.client, watch.cik), watch.as_of)
    report = check_filer(facts, limit=3)
    state = 'reconciles' if report.trustworthy else 'broken'
    fingerprint = digest(watch.ticker, state, len(report.failed))
    previous = last_seen(watch.con, watch.ticker, 'statements_reconcile')
    record(watch.con, watch.ticker, 'statements_reconcile', fingerprint,
           {'state': state, 'failed': len(report.failed), 'passed': report.passed})

    if previous is None or previous['payload'].get('state') != 'reconciles' or state == 'reconciles':
        return []

    failure = report.failed[0] if report.failed else None
    return [Alert(
        ticker=watch.ticker, check_name='statements_reconcile', severity=Severity.HIGH,
        headline='reported statements stopped reconciling',
        detail=failure.detail[:200] if failure else f'{report.skipped} checks could not run')]


CHECKS = {
    'new_filings': new_filings,
    'material_events': material_events,
    'price_move': price_move,
    'screen_membership': screen_membership,
    'statements_reconcile': statements_reconcile,
}

CHECK_DESCRIPTIONS = {
    'new_filings': 'a periodic report that was not there last time',
    'material_events': 'an 8-K filed under an item number that moves a valuation',
    'price_move': f'a move over {PRICE_MOVE_THRESHOLD:.0%} in {PRICE_LOOKBACK_DAYS} days, either way',
    'screen_membership': 'entered or left one of the configured screens',
    'statements_reconcile': 'the filer\'s own figures stopped adding up, usually a restatement',
}


def lookback_start(as_of: date, days: int = PRICE_LOOKBACK_DAYS) -> date:
    """Start of the price window, exposed so the command line can describe what it looked at."""
    return as_of - timedelta(days=days)
