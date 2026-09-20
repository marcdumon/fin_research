"""Tests for citation verification.

Two properties matter above all. A fabricated quote must never resolve -- that is the whole point. And a
resolved quote's offsets must slice the canonical text back to exactly what was matched, because every stored
citation is an offset and a drift there corrupts the record silently.
"""


from dataclasses import dataclass

import pytest
from conftest import filing_html

from fin_research.core.schemas import Claim, ClaimType, EvidenceKind, EvidenceRequest, Finding, MatchKind
from fin_research.core.verification import (
    MIN_QUOTE_CHARS,
    FactIndex,
    NumericVerdict,
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


# --- figures against the filer's own statements -------------------------------
@dataclass
class _Reported:
    """Stands in for a normalised fact."""

    metric: str
    value: float


@pytest.fixture
def reported() -> FactIndex:
    """What a company actually filed, across two years."""
    return FactIndex.from_facts([
        _Reported('Revenue', 416_161_000_000), _Reported('Revenue', 391_035_000_000),
        _Reported('CapEx', -8_165_000_000), _Reported('Total Assets', 359_241_000_000),
    ])


@pytest.fixture
def quoted() -> tuple[dict, EvidenceRequest]:
    """A source and a quote from it that genuinely exists."""
    text = 'Total net sales increased 6% to $416.2 billion during 2025, driven by iPhone and Services.'
    return {'d1': SourceText(text)}, EvidenceRequest(document_id='d1', quoted_text='Total net sales increased 6%')


def _numeric(value: float, metric: str | None, evidence, unit: str = 'USD billions') -> Claim:
    return Claim(claim_type=ClaimType.NUMERIC, text='a figure', numeric_value=value,
                 numeric_unit=unit, metric=metric, evidence=[evidence])


def test_a_real_quote_with_a_wrong_number_is_rejected(reported, quoted):
    """The case this check exists for: span verification alone passes it, because the sentence is real."""
    sources, evidence = quoted
    verdict = verify_claim(_numeric(500.0, 'Revenue', evidence), sources, reported)

    assert not verdict.grounded
    assert verdict.numeric is NumericVerdict.CONTRADICTED
    assert '416,161,000,000' in verdict.reason

    without_facts = verify_claim(_numeric(500.0, 'Revenue', evidence), sources)
    assert without_facts.grounded, 'span checking alone cannot catch this, which is the point'


def test_a_correct_figure_is_confirmed(reported, quoted):
    sources, evidence = quoted
    verdict = verify_claim(_numeric(416.2, 'Revenue', evidence), sources, reported)
    assert verdict.grounded and verdict.numeric is NumericVerdict.CONFIRMED


def test_a_prior_year_figure_is_accepted(reported, quoted):
    """A model quoting last year's revenue is being accurate, not wrong."""
    sources, evidence = quoted
    assert verify_claim(_numeric(391.0, 'Revenue', evidence), sources, reported).grounded


def test_an_outflow_quoted_without_its_sign_still_matches(reported, quoted):
    """Statements store outflows negative; prose says 'capital expenditure of 8.2 billion'."""
    sources, evidence = quoted
    verdict = verify_claim(_numeric(8.165, 'CapEx', evidence), sources, reported)
    assert verdict.grounded and verdict.numeric is NumericVerdict.CONFIRMED


def test_a_metric_the_company_never_reported_is_not_checked(reported, quoted):
    """Not every number is a line in the accounts. Rejecting those would discard correct claims."""
    sources, evidence = quoted
    verdict = verify_claim(_numeric(12.0, 'Retail stores', evidence, unit='count'), sources, reported)
    assert verdict.grounded and verdict.numeric is NumericVerdict.UNCHECKED


def test_a_figure_with_no_metric_named_is_not_checked(reported, quoted):
    sources, evidence = quoted
    verdict = verify_claim(_numeric(12.0, None, evidence), sources, reported)
    assert verdict.numeric is NumericVerdict.UNCHECKED


def test_a_wrong_number_with_a_fabricated_quote_fails_on_the_quote_first(reported, quoted):
    """Citation failure is reported ahead of arithmetic, because it is the more fundamental one."""
    sources, _ = quoted
    invented = EvidenceRequest(document_id='d1', quoted_text='Revenue reached five hundred billion dollars')
    verdict = verify_claim(_numeric(500.0, 'Revenue', invented), sources, reported)

    assert not verdict.grounded
    assert 'not found in the source' in verdict.reason


def test_the_report_counts_figures_separately_from_citations(reported, quoted):
    """Whether a source says what was claimed and whether the number is right are different questions."""
    sources, evidence = quoted
    report = verify_claims([_numeric(416.2, 'Revenue', evidence), _numeric(500.0, 'Revenue', evidence)],
                           sources, reported)

    assert report.numeric_outcomes() == {'confirmed': 1, 'contradicted': 1}
    assert report.match_kinds() == {'exact': 2}, 'both quotes were real; only the numbers differed'


def test_an_empty_index_checks_nothing(quoted):
    _, evidence = quoted
    assert FactIndex.from_facts([]).check(_numeric(416.2, 'Revenue', evidence))[0] is NumericVerdict.UNCHECKED
