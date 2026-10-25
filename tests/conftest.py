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


# Tables a test would touch if it ever wrote through the real configuration. `Config()` defaults to the
# real data directory, and a mocked session that used it on 2026-09-20 left eight fake runs, fake spend and
# thirty-two fixture replies in the live prompt cache -- where a real run would have been handed them as
# answers, and where the dashboard presented two of them as research notes. Nothing noticed for two days.
LEDGER_TABLES = ('runs', 'llm_calls', 'artifacts', 'prompt_cache')


def _ledger_counts() -> tuple[int, ...] | None:
    """Row counts of the real run ledger, read without opening it for writing."""
    import sqlite3

    real = paths.DATA_ROOT / 'state.sqlite'
    if not real.exists():
        return None
    con = sqlite3.connect(f'file:{real}?mode=ro', uri=True)
    try:
        return tuple(con.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in LEDGER_TABLES)
    except sqlite3.Error:
        return None
    finally:
        con.close()


@pytest.fixture(scope='session', autouse=True)
def the_real_ledger_is_left_alone():
    """Fail the session if any test wrote to the real run ledger.

    Counted rather than timestamped: the dashboard may be running and touching the file while tests run, but
    it does not add runs, calls or cached replies unless someone starts one.
    """
    before = _ledger_counts()
    yield
    after = _ledger_counts()
    if before is None or after is None:
        return
    assert before == after, (
        f'the real ledger changed during the tests: {dict(zip(LEDGER_TABLES, before, strict=True))} -> '
        f'{dict(zip(LEDGER_TABLES, after, strict=True))}. A test is writing through the real configuration, '
        f'or a run was started from the dashboard while the tests ran.')


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
    from test_end_to_end import _claim_id, _finding_set, _quote, _report, _thesis, _valuation

    text = {key: value for key, (_, value) in filing_sources.sections.items()}
    return {
        'business': _finding_set('doc-1', [_quote(text['1'], 500)]),
        'risk_factors': _finding_set('doc-1A', [_quote(text['1A'], 800)]),
        'management_view': _finding_set('doc-7', [_quote(text['7'], 1200)]),
        'financials': _finding_set('doc-fin', [_quote(text['7'], 2500)]),
        'bull': _thesis('bull'), 'bear': _thesis('bear'), 'valuation': _valuation(),
        'synthesis': _report(numbers=[], bull=[_claim_id(f"The filing states: {_quote(text['1'], 500)[:80]}")],
                             bear=[_claim_id('A bear view is warranted')]),
    }


@pytest.fixture(autouse=True)
def sec_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every SEC request of a test carries a test address, never the one in the user's .env."""
    monkeypatch.setenv('SEC_CONTACT_EMAIL', 'tests@example.com')
