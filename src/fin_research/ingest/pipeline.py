"""Fetch and prepare everything a research run needs for one company.

Separated from the command line so a run can prepare its own inputs. Requiring a person to run an ingest
step first is reasonable at a terminal and unreasonable from a browser, where the only sensible answer to
"research this company" is that the system fetches what it needs.

Nothing here calls a model. Filings are cached permanently and the submissions index on a multi-day TTL, so
preparing a company a second time costs one request and a few seconds.
"""

import logging
from datetime import date, timedelta

from fin_research.config import Config
from fin_research.ingest import store as store_module
from fin_research.ingest.documents import fetch_primary, fetch_report, rendered_reports
from fin_research.ingest.edgar import ANNUAL_FORMS, EdgarClient
from fin_research.ingest.facts import fetch_facts
from fin_research.ingest.news import events, press_release
from fin_research.ingest.sections import REQUIRED_ITEMS, split

log = logging.getLogger(__name__)

DEFAULT_YEARS = 5


async def ingest_company(cfg: Config, ticker: str, years: int = DEFAULT_YEARS, statements: bool = True,
                         news: bool = True) -> dict[str, int]:
    """Fetch a company's filings, split them, and normalise its reported metrics.

    Returns row counts rather than nothing, so a caller can tell an empty company from a failed fetch.
    """
    since = date.today() - timedelta(days=365 * years)

    async with EdgarClient(cfg) as client:
        cik = await client.resolve_filer(ticker)
        annual = await client.filings(cik, forms=ANNUAL_FORMS, since=since)
        log.info(f'{ticker}: CIK {cik}, {len(annual)} annual report(s) since {since}')

        with store_module.connect(cfg) as con:
            store_module.store_filings(con, annual, ticker)

            for filing in annual:
                document = await fetch_primary(client, filing)
                document_id = store_module.store_document(con, filing, filing.primary_document,
                                                          'primary', document)
                result = split(document, form=filing.form)
                store_module.store_sections(con, document_id, filing.accession, result)
                missing = [item for item in REQUIRED_ITEMS if result.by_item(item) is None]
                log.info(f'  {filing.form} {filing.filed}: {len(result.sections)} sections, '
                         f'confidence {result.confidence:.2f}' + (f', missing {missing}' if missing else ''))

                if statements:
                    for report in await rendered_reports(client, filing):
                        page = await fetch_report(client, filing, report)
                        store_module.store_document(con, filing, report.html_file,
                                                    report.category.lower(), page)

            if news:
                recent = await events(client, cik, since=since, material_only=True)
                store_module.store_filings(con, [e.filing for e in recent], ticker)
                for event in recent:
                    release = await press_release(client, event.filing)
                    if release is not None:
                        store_module.store_document(con, event.filing, 'press-release',
                                                    'press_release', release)
                log.info(f'  {len(recent)} material event(s)')

            store_module.store_facts(con, await fetch_facts(client, cik), ticker)

            return {table: store_module.count_rows(con, table)
                    for table in ('filings', 'documents', 'sections', 'facts')}


def is_prepared(cfg: Config, ticker: str) -> bool:
    """Whether a company already has readable sections stored."""
    with store_module.connect(cfg, read_only=True) as con:
        row = con.execute("""
            SELECT count(*) FROM sections s JOIN filings f USING (accession)
            WHERE f.ticker = ? AND NOT s.is_redirect
        """, [ticker]).fetchone()
    return bool(row and row[0])
