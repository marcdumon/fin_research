"""Tests for material-event ingestion.

The substance of an earnings announcement is in Exhibit 99.1, not in the 8-K body, so the exhibit parser is
what matters here -- in particular that it reads the filer's declared exhibit *type* rather than guessing from
filenames, which companies compose freely.
"""

from datetime import date

import httpx
import pytest
import respx

from fin_research.config import Config, EdgarConfig
from fin_research.ingest.edgar import EdgarClient, Filing
from fin_research.ingest.news import ITEM_CODES, MATERIAL_ITEMS, Event, Exhibit, exhibits, parse_items, press_release

# The header is served as an HTML page with its SGML tags entity-escaped -- exactly as SEC returns it.
HEADERS_PAGE = """<html><body><pre>
ACCESSION NUMBER:\t0000320193-26-000018
&lt;DOCUMENT&gt;
&lt;TYPE&gt;8-K
&lt;SEQUENCE&gt;1
&lt;FILENAME&gt;aapl-20260730.htm
&lt;DESCRIPTION&gt;8-K
&lt;TEXT&gt;
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.1
&lt;SEQUENCE&gt;2
&lt;FILENAME&gt;a8-kex991q3.htm
&lt;DESCRIPTION&gt;EX-99.1
&lt;TEXT&gt;
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.2
&lt;SEQUENCE&gt;3
&lt;FILENAME&gt;supplement.htm
&lt;TEXT&gt;
&lt;/DOCUMENT&gt;
</pre></body></html>"""

FILING = Filing(cik=320193, accession='0000320193-26-000018', form='8-K', filed=date(2026, 7, 30),
                report_date=date(2026, 7, 30), primary_document='aapl-20260730.htm', items='2.02,9.01')


@pytest.fixture
def cfg(tmp_path):
    return Config(data_root=tmp_path, edgar=EdgarConfig(rate_limit_seconds=0.0))


# --- item codes --------------------------------------------------------------
@pytest.mark.parametrize(('raw', 'expected'), [
    ('2.02,9.01', ('2.02', '9.01')),
    ('5.02', ('5.02',)),
    ('', ()),
    ('2.02, 9.01 Financial Statements', ('2.02', '9.01')),
    ('2.02,2.02', ('2.02',)),
])
def test_item_codes_are_extracted_not_split_blindly(raw, expected):
    assert parse_items(raw) == expected


def test_event_classification_uses_the_filers_own_assertion():
    """The item number is what the company legally asserts the event to be -- better than inferring from prose."""
    earnings = Event(filing=FILING, items=('2.02', '9.01'))
    assert earnings.is_earnings and earnings.is_material
    assert earnings.descriptions[0] == 'Results of operations and financial condition'

    vote = Event(filing=FILING, items=('5.07', '9.01'))
    assert not vote.is_material, 'a shareholder vote does not move a valuation'


def test_exhibit_only_items_are_not_material():
    """9.01 says only that an exhibit is attached, so alone it carries no signal."""
    assert not Event(filing=FILING, items=('9.01',)).is_material


def test_unknown_item_codes_do_not_break_descriptions():
    assert Event(filing=FILING, items=('2.02', '9.99')).descriptions == (ITEM_CODES['2.02'],)


def test_material_items_are_all_real_codes():
    assert set(ITEM_CODES) >= MATERIAL_ITEMS


# --- exhibits ----------------------------------------------------------------
@respx.mock
async def test_exhibits_are_read_from_the_escaped_sgml_header(cfg):
    respx.get(f'{FILING.base_url}/{FILING.accession}-index-headers.html').mock(
        return_value=httpx.Response(200, text=HEADERS_PAGE))

    async with EdgarClient(cfg) as client:
        found = await exhibits(client, FILING)

    assert [(e.type, e.sequence, e.filename) for e in found] == [
        ('8-K', 1, 'aapl-20260730.htm'),
        ('EX-99.1', 2, 'a8-kex991q3.htm'),
        ('EX-99.2', 3, 'supplement.htm'),
    ]


@respx.mock
async def test_press_release_prefers_the_primary_exhibit(cfg):
    """A filer using both EX-99.1 and EX-99.2 puts the earnings release in the first."""
    respx.get(f'{FILING.base_url}/{FILING.accession}-index-headers.html').mock(
        return_value=httpx.Response(200, text=HEADERS_PAGE))
    respx.get(f'{FILING.base_url}/a8-kex991q3.htm').mock(
        return_value=httpx.Response(200, text='<p>Apple reports third quarter results</p>'))

    async with EdgarClient(cfg) as client:
        release = await press_release(client, FILING)

    assert release is not None
    assert 'third quarter results' in release.text


@respx.mock
async def test_filing_without_a_press_release_returns_none(cfg):
    page = HEADERS_PAGE.replace('EX-99.1', 'EX-10.1').replace('EX-99.2', 'EX-10.2')
    respx.get(f'{FILING.base_url}/{FILING.accession}-index-headers.html').mock(
        return_value=httpx.Response(200, text=page))

    async with EdgarClient(cfg) as client:
        assert await press_release(client, FILING) is None


@respx.mock
async def test_missing_header_file_is_survivable(cfg):
    """One unreadable filing must not abort a research run."""
    respx.get(f'{FILING.base_url}/{FILING.accession}-index-headers.html').mock(
        return_value=httpx.Response(404))

    async with EdgarClient(cfg) as client:
        assert await exhibits(client, FILING) == []


def test_press_release_detection_is_by_type_not_filename():
    assert Exhibit(type='EX-99.1', filename='whatever.htm').is_press_release
    assert not Exhibit(type='EX-10.1', filename='ex991_looks_like_one.htm').is_press_release
