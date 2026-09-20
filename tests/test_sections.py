"""Tests for 10-K section splitting.

The regression these guard against is silent and expensive: if the splitter returns the table of contents,
every downstream citation is technically valid and analytically worthless. So the assertions are about
plausibility of content, not just presence of keys -- a section that exists but is 27 characters long is the
exact failure this module was built to prevent.

The six fixtures were chosen to stress different filer conventions: a hyperlinked contents block (AAPL), a
contents block that is a plain table with no links at all (UNH), items satisfied by pointers to a separate
section (ACLS, PLD) or to page ranges (JPM), and all-caps headings (KO).
"""

import itertools

import pytest
from conftest import FILINGS

from fin_research.ingest.sections import (
    ITEM_HEADING,
    REQUIRED_ITEMS,
    Section,
    _Candidate,
    find_candidates,
    longest_increasing_run,
    split,
)
from fin_research.ingest.text import CleanedDocument, clean

# Below this, a non-redirect section is a contents row rather than the item itself.
PLAUSIBLE_ITEM_CHARS = 5_000


# --- the heading regex -------------------------------------------------------
@pytest.mark.parametrize(('line', 'expected'), [
    ('Item 1A. Risk Factors', ('1', 'A')),
    ('ITEM 7. MD&A', ('7', None)),
    ('Item 7A Quantitative and Qualitative Disclosures', ('7', 'A')),
    (' | Item 8. Financial Statements', ('8', None)),
])
def test_headings_are_recognised(line, expected):
    match = ITEM_HEADING.search(line)
    assert match is not None
    assert (match.group('num'), match.group('suffix')) == expected


def test_mid_sentence_cross_reference_is_not_a_heading():
    """'see Item 1A. Risk Factors' is the single most common impostor in a filing."""
    assert ITEM_HEADING.search('as described in Item 1A. Risk Factors above') is None


def test_marker_alone_in_a_table_cell_is_a_contents_row():
    """A heading carries its title inline; a contents row puts the title in the next cell."""
    doc = clean('<table><tr><td>Item 1.</td><td>Business</td></tr></table>'
                '<p>Item 1. Business</p><p>We make things.</p>')
    assert [c.position for c in find_candidates(doc)] == [doc.text.rindex('Item 1.')]


# --- the subsequence pass ----------------------------------------------------
def test_longest_increasing_run_enforces_item_order():
    """Real headings ascend through the document exactly once; stray references do not fit that ordering."""
    candidates = [
        _Candidate(position=10, item_key='1A', title='Risk Factors', weight=1.0),   # forward reference
        _Candidate(position=20, item_key='1', title='Business', weight=1.5),
        _Candidate(position=30, item_key='1A', title='Risk Factors', weight=1.5),
        _Candidate(position=40, item_key='7', title="Management's discussion", weight=1.5),
    ]
    assert [(c.item_key, c.position) for c in longest_increasing_run(candidates)] == [
        ('1', 20), ('1A', 30), ('7', 40)]


def test_empty_candidates_yield_empty_chain():
    assert longest_increasing_run([]) == []


def test_document_without_headings_falls_back_to_whole_document():
    """Reading too much is affordable; citing the contents page is not."""
    doc = CleanedDocument(text='A filing with no item headings at all.', link_spans=())
    result = split(doc)
    assert result.method == 'whole_doc'
    assert result.confidence == 0.0
    assert result.sections[0].char_end == len(doc.text)


# --- real filings ------------------------------------------------------------
@pytest.mark.parametrize('name', FILINGS)
def test_every_filing_yields_plausible_required_items(name, cleaned_filings):
    doc = cleaned_filings[name]
    result = split(doc)

    keys = [s.item_key for s in result.sections]
    assert len(keys) == len(set(keys)), f'{name}: an item was split twice'
    assert result.confidence >= 0.9, f'{name}: low confidence {result.confidence}'

    for key in REQUIRED_ITEMS:
        section = result.by_item(key)
        assert section is not None, f'{name}: Item {key} missing'
        if section.is_redirect:
            continue
        assert section.n_chars > PLAUSIBLE_ITEM_CHARS, (
            f'{name}: Item {key} is only {section.n_chars} chars -- this is the contents row, not the item')


@pytest.mark.parametrize('name', FILINGS)
def test_sections_are_ordered_and_non_overlapping(name, cleaned_filings):
    sections = split(cleaned_filings[name]).sections
    for earlier, later in itertools.pairwise(sections):
        assert earlier.char_end == later.char_start
        assert earlier.char_start < later.char_start


@pytest.mark.parametrize('name', FILINGS)
def test_section_text_round_trips_against_the_document(name, cleaned_filings):
    """Offsets must index the exact string they were measured against, or every citation drifts."""
    doc = cleaned_filings[name]
    section = split(doc).by_item('1A')
    assert section is not None
    assert section.text(doc.text) == doc.text[section.char_start:section.char_end]


def test_risk_factors_reads_as_risk_factors(cleaned_filings):
    """A cheap end-to-end sanity check that the span holds what its label claims."""
    doc = cleaned_filings['aapl_tech']
    body = split(doc).by_item('1A').text(doc.text).lower()
    assert 'risk factors' in body[:200]
    assert 'adversely affect' in body


@pytest.mark.parametrize(('name', 'item', 'target'), [
    ('acls_smallcap', '8', '15'),   # 'submitted as a separate section ... following Item 15'
    ('pld_reit', '8', '15'),
])
def test_items_satisfied_by_reference_are_flagged_with_their_target(name, item, target, cleaned_filings):
    section = split(cleaned_filings[name]).by_item(item)
    assert section.is_redirect is True
    assert section.redirect_to == target


def test_pointer_to_a_page_range_is_flagged_even_without_a_target(cleaned_filings):
    """A bank points Item 7 at 'pages 46-160', naming no item. Still a pointer, still must not be read as MD&A."""
    section = split(cleaned_filings['jpm_bank']).by_item('7')
    assert section.is_redirect is True
    assert section.redirect_to is None
    assert section.n_chars < PLAUSIBLE_ITEM_CHARS


def test_section_helpers():
    section = Section(item_key='1A', title='Risk Factors', char_start=100, char_end=500, method='anchor_lis')
    assert section.n_chars == 400
    assert section.est_tokens == 100
    assert section.is_redirect is False


def test_quarterly_filings_are_refused_rather_than_mis_split(cleaned_filings):
    """A 10-Q numbers items separately per Part, so Item 1 occurs twice and ordering cannot disambiguate."""
    result = split(cleaned_filings['aapl_tech'], form='10-Q')
    assert result.method == 'whole_doc'
    assert result.confidence == 0.0


@pytest.mark.parametrize(('name', 'layout'), [
    ('unh_health', 'contents rows put the title in the cell beside the marker'),
    ('aapl2015_tablecell', 'real headings sit in table cells with the title on the next line'),
    ('aapl2019_wraptitle', 'real headings have an empty title on the marker line'),
])
def test_contents_rows_and_headings_are_told_apart_across_layouts(name, layout, cleaned_filings):
    """Three filings whose markers look alike on the surface; each broke an earlier rule. See `find_candidates`."""
    result = split(cleaned_filings[name])
    business = result.by_item('1')
    assert business is not None, f'{name}: {layout}'
    assert business.n_chars > PLAUSIBLE_ITEM_CHARS, (
        f'{name}: Item 1 is {business.n_chars} chars -- the contents row won. Layout: {layout}')
