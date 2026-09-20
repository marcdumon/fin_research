"""Shared fixtures.

Filing fixtures are stored gzipped: the six raw 10-Ks total about 35 MB and compress to 2 MB, which is the
difference between a repo you can clone and one you cannot.
"""

import gzip
import json

import pytest

from fin_research import paths
from fin_research.ingest.facts import parse_facts
from fin_research.ingest.text import clean

# Chosen to span filer conventions, not to be a nice portfolio. The last two pin structural eras: a filing
# whose headings sit in table cells, and one whose heading titles wrap to the following line. Each broke an
# earlier version of the splitter in a different way.
FILINGS = ('aapl_tech', 'acls_smallcap', 'jpm_bank', 'ko_consumer', 'pld_reit', 'unh_health',
           'aapl2015_tablecell', 'aapl2019_wraptitle')


def filing_html(name: str) -> bytes:
    """Raw HTML of one stored 10-K fixture."""
    return gzip.decompress((paths.FIXTURES / f'10k_{name}.html.gz').read_bytes())


@pytest.fixture(scope='session')
def cleaned_filings() -> dict:
    """Every fixture filing, cleaned once and shared -- cleaning a large bank filing takes about a second."""
    return {name: clean(filing_html(name)) for name in FILINGS}


def company_facts(name: str) -> dict:
    """Trimmed companyfacts payload for one filer, as SEC returns it."""
    return json.loads(gzip.decompress((paths.FIXTURES / f'companyfacts_{name}.json.gz').read_bytes()))


@pytest.fixture(scope='session')
def aapl_facts() -> list:
    """Normalised facts for a filer whose statements reconcile cleanly."""
    return parse_facts(company_facts('aapl'), cik=320193)


@pytest.fixture(scope='session')
def unh_facts() -> list:
    """Normalised facts for a filer carrying mezzanine equity and discontinued operations."""
    return parse_facts(company_facts('unh'), cik=731766)
