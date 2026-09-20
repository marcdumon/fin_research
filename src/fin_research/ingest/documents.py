"""Fetch the documents that make up a filing, and read FilingSummary.xml to learn what is in it.

Filings are immutable once accepted: an accession number always names the same bytes. So unlike the
submissions index, which is re-fetched on a TTL because a company keeps filing, documents are cached forever.
That turns every research run after the first into local reads.

FilingSummary.xml is the shortcut worth knowing about. EDGAR's renderer writes one R*.htm page per statement
and per note, each labelled with a human name -- 'CONSOLIDATED BALANCE SHEETS', 'Income Taxes', 'Debt'. Those
give precisely-bounded sections with no regex and no guessing, which is the opposite of the situation in the
primary document, where section boundaries have to be recovered from prose.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from fin_research.ingest.edgar import EdgarClient, Filing
from fin_research.ingest.text import CleanedDocument, clean, clean_rendered_page

log = logging.getLogger(__name__)

# MenuCategory values that carry readable content. 'Details' and 'Tables' are XBRL tagging breakdowns of
# material already present in the notes, so including them would duplicate evidence at great token cost.
CONTENT_CATEGORIES = frozenset({'Cover', 'Statements', 'Notes'})


@dataclass(frozen=True)
class RenderedReport:
    """One R*.htm page from EDGAR's renderer, as FilingSummary.xml describes it."""

    html_file: str
    short_name: str
    category: str

    @property
    def is_content(self) -> bool:
        """True if this page holds readable narrative or statement content rather than tagging detail."""
        return self.category in CONTENT_CATEGORIES


def _document_cache(data_root: Path, filing: Filing, filename: str) -> Path:
    """Cache location for one document inside one filing."""
    return data_root / 'sec' / 'documents' / str(filing.cik) / filing.accession_nodash / filename


async def fetch_document(client: EdgarClient, filing: Filing, filename: str) -> str:
    """Fetch one named document from a filing's archive directory, cached permanently.

    A very large TTL rather than a separate no-expiry code path, because filings genuinely never change and a
    single cache policy is easier to reason about than two.
    """
    cache = _document_cache(client.cfg.data_root, filing, filename)
    if cache.exists():
        return cache.read_text()
    return await client.get_text(f'{filing.base_url}/{filename}', cache)


async def fetch_primary(client: EdgarClient, filing: Filing) -> CleanedDocument:
    """The filing's primary document, cleaned.

    Its text is the string every citation offset for this filing is measured against, and its link spans are
    what lets the section splitter tell a contents row from a heading.
    """
    return clean(await fetch_document(client, filing, filing.primary_document))


async def fetch_primary_text(client: EdgarClient, filing: Filing) -> str:
    """Canonical plain text of the filing's primary document, for callers that need no link spans."""
    return (await fetch_primary(client, filing)).text


async def rendered_reports(client: EdgarClient, filing: Filing, content_only: bool = True) -> list[RenderedReport]:
    """Statements and notes that EDGAR's renderer produced for this filing, in presentation order.

    Returns an empty list when FilingSummary.xml is absent, which is normal for filings predating inline XBRL;
    callers fall back to splitting the primary document. A missing summary is not an error.
    """
    try:
        raw = await fetch_document(client, filing, 'FilingSummary.xml')
    except Exception as error:
        log.debug(f'no FilingSummary.xml for {filing.accession}: {type(error).__name__}: {error}')
        return []

    try:
        root = etree.fromstring(raw.encode('utf-8', errors='replace'))
    except etree.XMLSyntaxError as error:
        log.warning(f'malformed FilingSummary.xml for {filing.accession}: {error}')
        return []

    reports = []
    for node in root.iterfind('.//Report'):
        html_file = node.findtext('HtmlFileName') or ''
        short_name = (node.findtext('ShortName') or '').strip()
        category = (node.findtext('MenuCategory') or '').strip()
        if not html_file or not short_name:
            continue
        report = RenderedReport(html_file=html_file, short_name=short_name, category=category)
        if content_only and not report.is_content:
            continue
        reports.append(report)
    return reports


async def fetch_report(client: EdgarClient, filing: Filing, report: RenderedReport) -> CleanedDocument:
    """One rendered statement or note page, cleaned.

    Each R page is a document in its own right with its own offsets, which is why evidence must be keyed by
    document rather than by accession -- two documents in one filing share an accession but not a coordinate
    system.
    """
    return clean_rendered_page(await fetch_document(client, filing, report.html_file))
