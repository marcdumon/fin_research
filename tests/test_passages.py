"""Tests for numbered passage citation.

The property that matters is that a passage's recorded offsets slice the canonical text back to exactly that
passage. Citation by number is only exact if that holds; if offsets drift, every citation points somewhere
slightly wrong and nothing reports it.
"""

import itertools

import pytest
from conftest import filing_html

from fin_research.core.passages import MIN_PASSAGE_CHARS, PassageIndex, render, split_passages
from fin_research.core.schemas import Claim, ClaimType, EvidenceRequest, MatchKind
from fin_research.core.verification import SourceText, verify_claim, verify_evidence
from fin_research.ingest.sections import split
from fin_research.ingest.text import clean

TEXT = (
    'The Company designs, manufactures and markets smartphones and personal computers worldwide. '
    'The Company sells through its retail stores, online stores, and a direct sales force. '
    'Net sales increased 6% during 2025 compared to 2024, driven by higher iPhone unit sales.'
)


def test_offsets_slice_back_to_the_passage():
    """Citation by number is exact only if this holds."""
    for passage in split_passages(TEXT):
        assert TEXT[passage.char_start:passage.char_end] == passage.text


def test_passages_are_numbered_from_one_and_in_order():
    passages = split_passages(TEXT)
    assert [p.passage_id for p in passages] == list(range(1, len(passages) + 1))
    assert all(a.char_start < b.char_start for a, b in itertools.pairwise(passages))


def test_fragments_too_short_to_cite_are_not_numbered():
    """A table cell is not a citable statement, and numbering one wastes a number."""
    passages = split_passages('Yes. No. ' + 'A' * (MIN_PASSAGE_CHARS + 10) + '.')
    assert all(len(p.text.strip()) >= MIN_PASSAGE_CHARS for p in passages)


def test_abbreviations_do_not_split_a_passage():
    """Filings are full of 'U.S.' and 'Inc.'; splitting there produces fragments nobody can cite."""
    text = 'The Company operates in the U.S. and abroad through Apple Inc. and its subsidiaries worldwide.'
    assert len(split_passages(text)) == 1


def test_rendering_shows_the_number_the_model_must_cite():
    rendered = render(split_passages(TEXT))
    assert rendered.startswith('[1] ')
    assert '[2] ' in rendered


def test_a_very_long_passage_is_trimmed_for_display_but_not_for_offsets():
    """A wall of table data stays citable; only what the model is shown is shortened."""
    long_text = 'Revenue was reported as follows. ' + 'X' * 2000 + '.'
    passage = split_passages(long_text)[0]
    assert len(passage.shown()) < len(passage.text)
    assert long_text[passage.char_start:passage.char_end] == passage.text


# --- resolution ---------------------------------------------------------------
def _cite(passage_id: int | None, quote: str | None = None) -> EvidenceRequest:
    return EvidenceRequest(document_id='d1', passage_id=passage_id, quoted_text=quote)


@pytest.fixture
def index() -> dict:
    return {'d1': PassageIndex.build('d1', TEXT)}


def test_a_cited_number_resolves_to_exact_offsets(index):
    """A number alone locates the text. Nothing corroborates that it is the text the claim was about."""
    evidence = verify_evidence(_cite(2), {'d1': SourceText(TEXT)}, index)
    assert evidence.is_grounded
    assert evidence.match_kind is MatchKind.POSITIONAL
    assert TEXT[evidence.char_start:evidence.char_end] == evidence.matched_text
    assert 'retail stores' in evidence.matched_text


def test_an_anchor_inside_the_cited_passage_confirms_the_number(index):
    """What restores the check: the agent shows a few words of the passage it says it read."""
    evidence = verify_evidence(_cite(2, 'retail stores'), {'d1': SourceText(TEXT)}, index)
    assert evidence.match_kind is MatchKind.EXACT
    assert TEXT[evidence.char_start:evidence.char_end] == 'retail stores'


def test_an_anchor_from_a_different_passage_moves_the_citation_to_where_it_belongs(index):
    """A quote is checkable and a number is not, so a mistyped number loses to the words beside it."""
    evidence = verify_evidence(_cite(2, 'designs, manufactures and markets smartphones'),
                               {'d1': SourceText(TEXT)}, index)
    assert evidence.is_grounded
    assert TEXT[evidence.char_start:evidence.char_end] == 'designs, manufactures and markets smartphones'


def test_an_anchor_found_nowhere_fails_even_with_a_real_number(index):
    """The number exists, so the old code called this exact. The words prove the passage was never read."""
    evidence = verify_evidence(_cite(2, 'guarantees a forty percent increase in sales'),
                               {'d1': SourceText(TEXT)}, index)
    assert not evidence.is_grounded


def test_a_number_that_does_not_exist_is_rejected(index):
    """An invented number must fail the same way an invented quote does."""
    assert not verify_evidence(_cite(999), {'d1': SourceText(TEXT)}, index).is_grounded


def test_quoting_still_works_for_an_agent_that_quotes(index):
    """Passage numbers are preferred, not mandatory; a quote is held to the old standard."""
    evidence = verify_evidence(_cite(None, 'designs, manufactures and markets smartphones'),
                               {'d1': SourceText(TEXT)}, index)
    assert evidence.is_grounded


def test_a_paraphrase_is_still_rejected_when_quoting(index):
    """The failure that motivated numbering: accurate wording that is not the source's wording."""
    evidence = verify_evidence(_cite(None, 'designs and sells smartphones and computers'),
                               {'d1': SourceText(TEXT)}, index)
    assert not evidence.is_grounded


def test_a_claim_citing_a_passage_survives_verification(index):
    claim = Claim(claim_type=ClaimType.FACT, text='The company sells through retail stores',
                  evidence=[_cite(2)])
    verdict = verify_claim(claim, {'d1': SourceText(TEXT)}, None, index)
    assert verdict.grounded


def test_a_claim_citing_a_missing_passage_is_dropped(index):
    claim = Claim(claim_type=ClaimType.FACT, text='Invented', evidence=[_cite(999)])
    assert not verify_claim(claim, {'d1': SourceText(TEXT)}, None, index).grounded


# --- against a real filing -----------------------------------------------------
def test_a_real_section_numbers_cleanly_with_stable_offsets():
    document = clean(filing_html('aapl_tech'))
    section = split(document).by_item('1')
    text = section.text(document.text)
    index = PassageIndex.build('d1', text)

    assert len(index) > 50, 'a business section should yield many citable passages'
    assert all(text[p.char_start:p.char_end] == p.text for p in index.passages)


def test_numbering_adds_little_to_the_prompt():
    """Numbering costs tokens on every call, so the overhead has to stay small."""
    document = clean(filing_html('aapl_tech'))
    text = split(document).by_item('1').text(document.text)
    rendered = PassageIndex.build('d1', text).render()
    assert len(rendered) < len(text) * 1.1


def test_a_figure_can_be_its_own_anchor(index):
    """The natural anchor for a number is the number. Nine characters, and it was thrown out for brevity."""
    text = 'Total revenues | $ 4,640,280 | $ 4,519,571 | $ 4,283,758 |'
    cited = {'d1': PassageIndex.build('d1', text)}
    evidence = verify_evidence(EvidenceRequest(document_id='d1', passage_id=1, quoted_text='4,519,571'),
                               {'d1': SourceText(text)}, cited)

    assert evidence.is_grounded
    assert evidence.match_kind is MatchKind.EXACT
    assert text[evidence.char_start:evidence.char_end] == '4,519,571'


def test_a_short_anchor_is_not_enough_on_its_own(index):
    """The length rule still holds where it was meant to: searching a whole document for a few characters."""
    assert not verify_evidence(_cite(None, '4,519,571'), {'d1': SourceText(TEXT)}, index).is_grounded
