"""Company-shaped reads: the endpoints a research workbench needs, as distinct from a run ledger.

The rest of the API is organised around runs, because a run is what the agents produce. Nobody researches a
run. The questions asked here are asked about a company -- what does it do, what is it worth, what have I
concluded, what did the filings say -- and a run is one episode in that history.

Everything here is read-only apart from recording a verdict, and nothing here calls a model. The analytical
data lives in DuckDB with the market database attached read-only, and the run ledger lives in SQLite; a
company is assembled from both, in Python, because the two stores cannot be joined in one query and pretending
otherwise would mean copying one into the other.
"""

import logging
from datetime import date, timedelta

import duckdb

from fin_research.config import Config
from fin_research.core.state import State
from fin_research.ingest import store as store_module
from fin_research.ingest.news import ITEM_CODES
from fin_research.ingest.store import MARKET_ALIAS

log = logging.getLogger(__name__)

# Runs the system makes about itself rather than about a company.
DIAGNOSTIC_RUN = 'schema-check'

# The longest symbol a US listing uses. Anything longer is not a ticker.
MAX_TICKER_CHARS = 5


def is_ticker(name: str) -> bool:
    """Whether a run's subject names a company rather than something else the system did.

    A run row's ticker is whatever it was started with. A funnel run carries 'screen:quality_value', and a
    run started by typing a company's name rather than its symbol carries 'NVIDIA'. Both are real runs and
    belong in the ledger; neither is a company, and listing them as one puts rows in the watchlist that can
    never have a price, a filing or a verdict.
    """
    return bool(name) and name.isalnum() and len(name) <= MAX_TICKER_CHARS

# How far back a price series goes when the caller does not say.
DEFAULT_PRICE_YEARS = 5

# Concepts that share a metric name but mean different things. Net income for VSAT's 2026 year is a
# loss of $34.1 million attributable to the company and a profit of $3.9 million including minority
# interests; both are 'Net Income', and a chart saying one beside a note saying the other looks like an error
# unless it says which it is.
CONCEPT_MEANING = {
    'NetIncomeLoss': 'attributable to the company',
    'ProfitLoss': 'including minority interests',
    'IncomeLossFromContinuingOperationsAttributableToParent': 'continuing operations, attributable',
    'IncomeLossFromContinuingOperations': 'continuing operations',
    'LongTermDebtNoncurrent': 'excluding the current portion',
    'LongTermDebt': 'including the current portion',
    'LongTermDebtAndCapitalLeaseObligations': 'including lease obligations',
    'WeightedAverageNumberOfDilutedSharesOutstanding': 'weighted average',
}

# The lines a reader looks at first, in the order a statement presents them. The facts table holds
# 42 canonical metrics; showing all of them as charts would bury the four that carry the argument.
HEADLINE_METRICS = (
    'Revenue', 'Gross Profit', 'Operating Income', 'Net Income',
    'Operating Cash Flow', 'CapEx', 'Cash', 'Long Term Debt', 'Stockholders Equity', 'Total Assets',
)


def _attached(con: duckdb.DuckDBPyConnection) -> bool:
    """Whether the market database is already visible to this connection."""
    rows = con.execute('SELECT database_name FROM duckdb_databases()').fetchall()
    return any(row[0] == MARKET_ALIAS for row in rows)


def open_market(con: duckdb.DuckDBPyConnection, cfg: Config) -> bool:
    """Attach the market database if it is configured and reachable.

    Returns whether it worked rather than raising, because everything it provides -- prices, sector, the
    company's name -- is context around research that stands without it. A missing market file should cost
    the price chart, not the note.

    The second check is not redundant. Attaching in DuckDB is a property of the database rather than of one
    connection, so two requests arriving together both find it absent, both attach, and the one that loses
    the race is told the name is taken. Opening a company fires five requests at once, so the loser was
    routinely a blank header and a chart claiming the market database was unavailable while it was open.
    """
    if cfg.investalyze_db is None:
        return False
    if _attached(con):
        return True
    try:
        con.execute(f"ATTACH '{cfg.investalyze_db}' AS {MARKET_ALIAS} (READ_ONLY)")
        return True
    except duckdb.Error as error:
        # Losing the race is not a failure, and must not be reported as one: the database is open,
        # this connection simply was not the one that opened it. Anything else is a real problem and says so.
        if _attached(con):
            log.debug(f'market database was attached by another request: {error}')
            return True
        log.warning(f'could not attach {cfg.investalyze_db}: {error}; continuing without prices')
        return False


def profile(con: duckdb.DuckDBPyConnection, ticker: str, attached: bool) -> dict:
    """Name, sector and business description, from the market database."""
    if not attached:
        return {}
    row = con.execute(
        f'SELECT CompanyName, Sector, Industry, NrEmployees, BusinessSummary, Website, CIK '
        f'FROM {MARKET_ALIAS}.companies WHERE Ticker = ?', [ticker]).fetchone()
    if row is None:
        return {}
    return {'name': row[0], 'sector': row[1], 'industry': row[2], 'employees': row[3],
            'summary': row[4], 'website': row[5], 'cik': row[6]}


def quote(con: duckdb.DuckDBPyConnection, ticker: str, attached: bool) -> dict:
    """The last close and the move since the close before it."""
    if not attached:
        return {}
    rows = con.execute(
        f'SELECT Date, C FROM {MARKET_ALIAS}.prices WHERE Ticker = ? ORDER BY Date DESC LIMIT 2',
        [ticker]).fetchall()
    if not rows:
        return {}
    price = float(rows[0][1])
    previous = float(rows[1][1]) if len(rows) > 1 else None
    change = (price - previous) / previous if previous else None
    return {'price': price, 'price_date': str(rows[0][0]), 'change_pct': change}


def shares_outstanding(con: duckdb.DuckDBPyConnection, ticker: str) -> float | None:
    """The most recently filed diluted share count, for a market capitalisation."""
    row = con.execute(
        "SELECT value FROM facts WHERE ticker = ? AND metric IN ('Shares Diluted', 'Shares Basic') "
        'ORDER BY period_end DESC, filed DESC LIMIT 1', [ticker]).fetchone()
    return float(row[0]) if row else None


def research_state(store: State, tickers: list[str] | None = None) -> dict[str, dict]:
    """The latest run per company, and what was concluded about it.

    Keyed by ticker. A company with five runs has one entry, which is the point: the rail used to list the
    same company once per attempt, so a week of debugging one filing looked like five companies.
    """
    rows = store.query(
        'SELECT run_id, ticker, status, as_of, spend_usd, started_at, finished_at FROM runs '
        'WHERE ticker NOT IN (?) ORDER BY started_at DESC', (DIAGNOSTIC_RUN,))

    latest: dict[str, dict] = {}
    for row in rows:
        ticker = row['ticker'].upper()
        if not is_ticker(ticker) or (tickers is not None and ticker not in tickers):
            continue
        entry = latest.setdefault(ticker, {
            'ticker': ticker, 'runs': 0, 'latest_run_id': None, 'latest_status': None,
            'researched_at': None, 'spend_usd': 0.0,
        })
        entry['runs'] += 1
        entry['spend_usd'] += float(row['spend_usd'] or 0)
        # Rows arrive newest first, so the newest completed run wins and an unfinished one only
        # stands in until a completed one turns up. A failed attempt is still history, but it is not what
        # the company's research currently says.
        first = entry['latest_run_id'] is None
        better = entry['latest_status'] != 'done' and row['status'] == 'done'
        if first or better:
            entry['latest_run_id'] = row['run_id']
            entry['latest_status'] = row['status']
            entry['researched_at'] = row['as_of']

    recorded = store.verdicts()
    for ticker, entry in latest.items():
        verdict = recorded.get(ticker)
        entry['verdict'] = verdict['verdict'] if verdict else None
        entry['conviction'] = verdict['conviction'] if verdict else None
        entry['reviewed_at'] = verdict['reviewed_at'] if verdict else None
    return latest


# What a company looks like before anything has been done about it.
UNRESEARCHED = {'runs': 0, 'latest_run_id': None, 'latest_status': None, 'researched_at': None,
                'spend_usd': 0.0, 'verdict': None, 'conviction': None, 'reviewed_at': None}


def watch_state(con: duckdb.DuckDBPyConnection) -> tuple[set[str], dict[str, int]]:
    """Which companies are watched, and how many unread alerts each has."""
    from fin_research.monitor.store import open_alerts, watchlist

    try:
        watched = set(watchlist(con))
    except duckdb.Error:
        watched = set()
    try:
        raised = open_alerts(con, limit=500)
        alerts = {str(k): int(v) for k, v in raised['ticker'].value_counts().items()} if len(raised) else {}
    except duckdb.Error:
        alerts = {}
    return watched, alerts


def company_row(con: duckdb.DuckDBPyConnection, ticker: str, attached: bool, research: dict,
                watched: set[str], alerts: dict[str, int]) -> dict:
    """Everything the watchlist and the company header show about one company.

    One function for both. The header used to be built separately and without the filing date, so a company
    page read "last filed" from a field its own endpoint never sent.
    """
    quoted = quote(con, ticker, attached)
    shares = shares_outstanding(con, ticker)
    row = con.execute('SELECT max(filed) FROM filings WHERE ticker = ?', [ticker]).fetchone()
    return {
        'ticker': ticker,
        **UNRESEARCHED,
        **research,
        **profile(con, ticker, attached),
        **quoted,
        'shares': shares,
        'market_cap': quoted['price'] * shares if quoted.get('price') and shares else None,
        'watched': ticker in watched,
        'open_alerts': alerts.get(ticker, 0),
        'last_filed': str(row[0]) if row and row[0] else None,
    }


def filing_history(con: duckdb.DuckDBPyConnection, ticker: str, limit: int = 60) -> list[dict]:
    """What has been ingested for a company, newest first, with the sections that were located."""
    rows = con.execute(
        'SELECT f.accession, f.form, f.filed, f.report_date, f.primary_document, f.base_url, f.items, '
        '(SELECT count(*) FROM sections s WHERE s.accession = f.accession AND NOT s.is_redirect) AS sections '
        'FROM filings f WHERE f.ticker = ? ORDER BY f.filed DESC LIMIT ?', [ticker, limit]).fetchall()
    # An 8-K lists the rule items it reports under, as codes. '1.01,5.02,7.01,9.01' is a new material
    # agreement and a change of officers; the codes say that only to someone who knows the rulebook, so they
    # are read out here. 9.01 is left out because it says only that an exhibit is attached.
    def described(items: str | None) -> list[str]:
        codes = [code.strip() for code in (items or '').split(',') if code.strip() and code.strip() != '9.01']
        return [ITEM_CODES.get(code, f'Item {code}') for code in codes]

    return [{'accession': r[0], 'form': r[1], 'filed': str(r[2]), 'report_date': str(r[3]) if r[3] else None,
             'url': f'{r[5]}/{r[4]}' if r[5] and r[4] else None, 'items': described(r[6]), 'sections': r[7]}
            for r in rows]


def price_series(con: duckdb.DuckDBPyConnection, ticker: str, years: int, attached: bool) -> dict:
    """Daily bars plus a marker for every filing, so the chart shows when the company last spoke.

    The markers are the reason this is worth charting at all: a price move is a question, and the filing
    nearest it is usually the answer.
    """
    if not attached:
        return {'bars': [], 'markers': [], 'available': False}

    since = date.today() - timedelta(days=int(years * 365.25))
    bars = con.execute(
        f'SELECT Date, O, H, L, C, V FROM {MARKET_ALIAS}.prices '
        f'WHERE Ticker = ? AND Date >= ? ORDER BY Date', [ticker, since]).fetchall()

    markers = con.execute(
        'SELECT filed, form, accession FROM filings WHERE ticker = ? AND filed >= ? ORDER BY filed',
        [ticker, since]).fetchall()

    return {
        'available': True,
        'bars': [{'time': str(b[0]), 'open': float(b[1]), 'high': float(b[2]), 'low': float(b[3]),
                  'close': float(b[4]), 'volume': int(b[5] or 0)} for b in bars],
        'markers': [{'time': str(m[0]), 'form': m[1], 'accession': m[2]} for m in markers],
    }


def fundamentals(con: duckdb.DuckDBPyConnection, ticker: str) -> dict:
    """The reported figures as annual series, one value per metric per period.

    Point-in-time throughout: a period restated later is shown as most recently filed, and `filed` travels
    with every point so a chart can say when the company said it. Restatements are why this cannot simply
    select the rows -- one period carries several, and taking an arbitrary one makes growth rates wrong.
    """
    # The concept wins before the filing date does. A company reports several figures that all map
    # to one metric -- NetIncomeLoss is profit attributable to the parent, ProfitLoss includes minority
    # interests -- and `anchor_rank` records which one the mapping considers canonical. Ordering by date
    # first let a proxy statement filed three months after the annual report override the annual report's
    # own figure, which is not what point-in-time means. Date still decides between filings of the same
    # concept, which is where restatements live.
    rows = con.execute(
        """
        SELECT metric, period_end, value, filed, form, tag FROM (
            SELECT metric, period_end, value, filed, form, tag,
                   row_number() OVER (PARTITION BY metric, period_end ORDER BY anchor_rank, filed DESC) AS pick
            FROM facts
            WHERE ticker = ? AND metric IS NOT NULL AND period_type = 'A'
        ) WHERE pick = 1 ORDER BY metric, period_end
        """, [ticker]).fetchall()

    series: dict[str, list[dict]] = {}
    for metric, period_end, value, filed, form, tag in rows:
        series.setdefault(metric, []).append(
            {'period_end': str(period_end), 'value': float(value), 'filed': str(filed), 'form': form,
             'tag': tag, 'meaning': CONCEPT_MEANING.get(tag)})

    ordered = {m: series[m] for m in HEADLINE_METRICS if m in series}
    ordered.update({m: v for m, v in sorted(series.items()) if m not in ordered})
    return {'headline': [m for m in HEADLINE_METRICS if m in series], 'series': ordered}


# What each kind of document is, in the words a reader uses rather than the role it was stored under.
DOCUMENT_ROLE = {
    'statements': 'Financial statements',
    'press_release': 'Press release',
    'notes': 'Notes to the statements',
    'cover': 'Cover page',
    'primary': 'Annual report',
}


def _filing(con: duckdb.DuckDBPyConnection, accession: str) -> tuple[str | None, str | None, str | None]:
    """The form, filing date and primary document address of a filing."""
    row = con.execute('SELECT form, filed, base_url, primary_document FROM filings WHERE accession = ?',
                      [accession]).fetchone()
    if row is None:
        return None, None, None
    url = f'{row[2]}/{row[3]}' if row[2] and row[3] else None
    return row[0], str(row[1]), url


def describe_source(con: duckdb.DuckDBPyConnection, source_id: str) -> dict | None:
    """What a cited source is and the exact text an agent read from it.

    Returns the text alongside the description because the two must come from the same place: a citation's
    offsets are positions in the text the agent was given, so showing it means rebuilding that text exactly.
    Three kinds of source exist -- a located section of the annual report, a filing's statements read as one,
    and a single whole document -- and each is rebuilt by the same code that produced it for the agent.
    """
    if source_id.startswith(store_module.STATEMENTS_PREFIX):
        accession = source_id.removeprefix(store_module.STATEMENTS_PREFIX)
        built = store_module.statements_source(con, accession)
        if built is None:
            return None
        form, filed, url = _filing(con, accession)
        return {'source_id': source_id, 'text': built[1], 'part': 'Financial statements',
                'form': form, 'filed': filed, 'url': url, 'accession': accession}

    section = con.execute(
        'SELECT s.accession, s.item_key, s.title FROM sections s WHERE s.section_id = ?', [source_id]).fetchone()
    if section is not None:
        form, filed, url = _filing(con, section[0])
        title = (section[2] or '').strip()
        part = f'Item {section[1]}' + (f' · {title.title()}' if title else '')
        return {'source_id': source_id, 'text': store_module.section_text(con, source_id) or '',
                'part': part, 'form': form, 'filed': filed, 'url': url, 'accession': section[0]}

    document = con.execute(
        'SELECT accession, doc_role, url, text FROM documents WHERE document_id = ?', [source_id]).fetchone()
    if document is None:
        return None

    accession, role, url, text = document
    # Runs before statements had an identifier of their own cited them under the first page's. That
    # identifier names one page, but the offsets were measured in the joined text, so it is read as the join.
    if role == 'statements' and store_module.first_statements_page(con, accession) == source_id:
        built = store_module.statements_source(con, accession)
        text = built[1] if built else text

    form, filed, _ = _filing(con, accession)
    return {'source_id': source_id, 'text': text, 'part': DOCUMENT_ROLE.get(role, role),
            'form': form, 'filed': filed, 'url': url, 'accession': accession}


def source_text(con: duckdb.DuckDBPyConnection, source_id: str, start: int | None,
                end: int | None, context: int) -> dict | None:
    """The filing text a citation points at, with the cited span located inside an excerpt of it.

    Offsets come back relative to the excerpt so the caller can highlight without recomputing anything. A span
    that falls outside the text is reported as such rather than clamped: a citation that does not fit the
    source it names is broken, and highlighting the nearest thing to it would hide that.
    """
    found = describe_source(con, source_id)
    if found is None:
        return None

    whole = found.pop('text')
    found['total_chars'] = len(whole)

    if start is None or end is None:
        return {**found, 'excerpt': whole[:4000], 'span_start': None, 'span_end': None, 'in_range': True}

    if start < 0 or end > len(whole) or start >= end:
        return {**found, 'excerpt': '', 'span_start': None, 'span_end': None, 'in_range': False}

    begin = max(0, start - context)
    finish = min(len(whole), end + context)
    return {**found, 'excerpt': whole[begin:finish], 'span_start': start - begin, 'span_end': end - begin,
            'truncated_before': begin > 0, 'truncated_after': finish < len(whole), 'in_range': True}
