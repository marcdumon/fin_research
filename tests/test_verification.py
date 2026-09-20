"""Tests for citation verification.

Two properties matter above all. A fabricated quote must never resolve -- that is the whole point. And a
resolved quote's offsets must slice the canonical text back to exactly what was matched, because every stored
citation is an offset and a drift there corrupts the record silently.
"""


import pytest
from conftest import filing_html

from fin_research.core.schemas import Claim, ClaimType, EvidenceKind, EvidenceRequest, Finding, MatchKind
from fin_research.core.verification import (
    MIN_QUOTE_CHARS,
    SourceText,
    fold,
    parse_scale,
    surviving_findings,
    verify_claim,
    verify_claims,
    verify_evidence,
    verify_numeric,
)
from fin_research.ingest.text import clean

RSQUO = chr(0x2019)
NBSP = chr(0x00a0)
ZWSP = chr(0x200b)


@pytest.fixture(scope='module')
def aapl_source() -> SourceText:
    """A real filing's canonical text."""
    return SourceText(clean(filing_html('aapl_tech')).text)


def _claim(**kwargs) -> Claim:
    base = dict(claim_type=ClaimType.FACT, text='something asserted')
    return Claim(**(base | kwargs))


# --- folding keeps offsets honest --------------------------------------------
def test_fold_index_map_points_back_at_the_original():
    text = f'a{NBSP}{NBSP}b'
    folded, index_map = fold(text, 1)
    assert folded == 'a b'
    assert len(index_map) == len(folded)
    assert text[index_map[-1]] == 'b'


def test_fold_drops_zero_width_without_losing_the_map():
    folded, index_map = fold(f'Risk{ZWSP}Factors', 2)
    assert folded == 'RiskFactors'
    assert len(index_map) == len(folded)


def test_level_zero_is_the_identity():
    folded, index_map = fold('Hello', 0)
    assert folded == 'Hello'
    assert index_map == [0, 1, 2, 3, 4]


# --- the ladder ---------------------------------------------------------------
@pytest.mark.parametrize(('quote', 'expected'), [
    ('The Company designs and manufactures', MatchKind.EXACT),
    ('The  Company   designs and manufactures', MatchKind.WHITESPACE),
    ('the company designs and manufactures', MatchKind.CASE),
])
def test_ladder_records_how_hard_it_had_to_look(quote, expected):
    source = SourceText('The Company designs and manufactures smartphones.')
    match = source.locate(quote)
    assert match is not None
    assert match.match_kind is expected


def test_typographic_punctuation_resolves_to_the_same_offsets():
    """A model retypes a quote in ASCII; the filing uses a typographic apostrophe. Same text, same place."""
    source = SourceText(f'The Company{RSQUO}s results improved materially this year.')
    typed = source.locate("The Company's results improved materially")
    assert typed is not None
    assert typed.match_kind is MatchKind.UNICODE
    assert source.text[typed.char_start:typed.char_end] == typed.matched_text


def test_a_fabricated_quote_never_resolves():
    """This is the property the entire design exists to guarantee."""
    source = SourceText('The Company designs and manufactures smartphones.')
    assert source.locate('The Company guarantees a 40% increase in sales next year.') is None


def test_a_quote_too_short_to_mean_anything_is_refused():
    source = SourceText('Revenue increased during the period.')
    assert source.locate('Revenue') is None
    assert len('Revenue') < MIN_QUOTE_CHARS


def test_offsets_round_trip_against_a_real_filing(aapl_source):
    quote = aapl_source.text[40_000:40_180]
    match = aapl_source.locate(quote)
    assert match is not None
    assert aapl_source.text[match.char_start:match.char_end] == match.matched_text


def test_a_real_quote_retyped_in_ascii_still_resolves(aapl_source):
    position = aapl_source.text.find(RSQUO)
    original = aapl_source.text[position - 60:position + 60]
    match = aapl_source.locate(original.replace(RSQUO, "'"))
    assert match is not None
    assert match.match_kind is not MatchKind.NONE


# --- numbers ------------------------------------------------------------------
@pytest.mark.parametrize(('unit', 'expected'), [
    ('USD billions', 1e9), ('$ in Millions', 1e6), ('thousands', 1e3), ('USD', 1.0), (None, 1.0),
])
def test_scale_words_are_understood(unit, expected):
    assert parse_scale(unit) == expected


def test_numeric_claims_are_compared_after_rescaling():
    """A model reporting 416.2 billion against a stored 416,161,000,000 is right, not wrong."""
    claim = _claim(claim_type=ClaimType.NUMERIC, numeric_value=416.2, numeric_unit='USD billions')
    assert verify_numeric(claim, 416_161_000_000)
    assert not verify_numeric(claim, 380_000_000_000)


def test_a_numeric_claim_without_a_number_cannot_pass():
    assert not verify_numeric(_claim(claim_type=ClaimType.NUMERIC), 100.0)


# --- claims -------------------------------------------------------------------
def test_judgement_is_exempt_but_facts_are_not():
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}

    judgement = verify_claim(_claim(claim_type=ClaimType.JUDGEMENT, text='A good business'), sources)
    assert judgement.grounded

    unsupported = verify_claim(_claim(claim_type=ClaimType.FACT), sources)
    assert not unsupported.grounded
    assert 'no evidence' in unsupported.reason


def test_a_claim_citing_an_unknown_document_fails():
    """An unresolvable citation is exactly as suspect as a wrong one."""
    claim = _claim(evidence=[EvidenceRequest(document_id='missing', quoted_text='The Company designs things')])
    verdict = verify_claim(claim, {})
    assert not verdict.grounded


def test_a_claim_whose_quote_is_present_survives():
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}
    claim = _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='designs and manufactures')])
    verdict = verify_claim(claim, sources)
    assert verdict.grounded
    assert verdict.evidence[0].char_start is not None
    assert verdict.weakest_match is MatchKind.EXACT


def test_fact_evidence_is_grounded_by_its_identifier():
    evidence = verify_evidence(EvidenceRequest(kind=EvidenceKind.FACT, fact_id='f1'), {})
    assert evidence.is_grounded


# --- reports ------------------------------------------------------------------
def test_groundedness_is_none_when_nothing_was_checkable():
    """A batch of pure judgement has demonstrated nothing; reporting 100% would be a vacuous pass."""
    report = verify_claims([_claim(claim_type=ClaimType.JUDGEMENT)], {})
    assert report.groundedness is None


def test_groundedness_counts_only_checkable_claims():
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}
    claims = [
        _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='designs and manufactures')]),
        _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='invented entirely by the model')]),
        _claim(claim_type=ClaimType.JUDGEMENT),
    ]
    report = verify_claims(claims, sources)
    assert report.groundedness == 0.5
    assert len(report.rejected) == 1


def test_ungrounded_claims_are_stripped_before_anything_downstream_sees_them():
    """An expensive model must never build on a claim that failed its citations."""
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}
    finding = Finding(headline='h', detail='d', claims=[
        _claim(text='real', evidence=[EvidenceRequest(document_id='d1', quoted_text='designs and manufactures')]),
        _claim(text='invented', evidence=[EvidenceRequest(document_id='d1', quoted_text='guarantees 40% growth ahead')]),
    ])

    kept, report = surviving_findings([finding], sources)
    assert [c.text for c in kept[0].claims] == ['real']
    assert len(report.rejected) == 1


def test_a_finding_losing_every_claim_is_dropped_entirely():
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}
    finding = Finding(headline='h', detail='d', claims=[
        _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='entirely fabricated statement here')])])
    kept, _ = surviving_findings([finding], sources)
    assert kept == []


def test_match_kind_counts_expose_drift_toward_weaker_matching():
    sources = {'d1': SourceText('The Company designs and manufactures smartphones.')}
    claims = [
        _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='designs and manufactures')]),
        _claim(evidence=[EvidenceRequest(document_id='d1', quoted_text='DESIGNS AND MANUFACTURES')]),
    ]
    counts = verify_claims(claims, sources).match_kinds()
    assert counts == {'exact': 1, 'case': 1}
