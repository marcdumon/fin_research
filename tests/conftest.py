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


@pytest.fixture
def filing_sources():
    """Real sections of a real filing, shaped as the supervisor expects them."""
    from fin_research.core.supervisor import Sources
    from fin_research.ingest.sections import split

    document = clean(filing_html('aapl_tech'))
    result = split(document)
    items = {i: result.by_item(i).text(document.text) for i in ('1', '1A', '7')}
    return Sources(sections={k: (f'doc-{k}', v) for k, v in items.items()},
                   statements=('doc-fin', items['7'][:5_000]))


@pytest.fixture
def replies(filing_sources):
    """Canned agent replies that quote the fixture filing verbatim."""
    from test_end_to_end import _finding_set, _quote, _report, _thesis, _valuation

    text = {key: value for key, (_, value) in filing_sources.sections.items()}
    return {
        'business': _finding_set('doc-1', [_quote(text['1'], 500)]),
        'risk_factors': _finding_set('doc-1A', [_quote(text['1A'], 800)]),
        'management_view': _finding_set('doc-7', [_quote(text['7'], 1200)]),
        'financials': _finding_set('doc-fin', [_quote(text['7'], 2500)]),
        'bull': _thesis('bull'), 'bear': _thesis('bear'), 'valuation': _valuation(),
        'synthesis': _report('doc-1', _quote(text['1'], 500)),
    }
