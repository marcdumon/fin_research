"""The note is assembled from claims that were verified, not from claims verified again while assembling it.

Two ways of citing a source had to pass through the renderer and only one did: a quotation was looked up a
second time at render time and found, while a passage number was looked up without the passage table and lost.
The note then listed no sources at all, under a footer asserting that every citation had been checked. These
tests hold the renderer to the verdicts it is given, so the appendix describes the check that actually
governed what survived.
"""

from datetime import date

from fin_research.core.passages import PassageIndex
from fin_research.core.schemas import Claim, ClaimType, Disagreement, EvidenceKind, EvidenceRequest, MatchKind, Report
from fin_research.core.verification import ClaimVerdict, FactIndex, SourceText, verify_claim, verify_evidence
from fin_research.report.render import render

TEXT = ('The Company designs and manufactures smartphones and personal computers. '
        'Revenue for the year was 391,035 million dollars, an increase of two percent. '
        'Gross margin expanded on a favourable mix of services.')


def _claim(claim_id: str, text: str, evidence: list[EvidenceRequest],
           claim_type: ClaimType = ClaimType.FACT) -> Claim:
    """A claim carrying an identifier, as the pipeline hands one to the renderer."""
    return Claim(claim_id=claim_id, claim_type=claim_type, text=text, evidence=evidence)


def _verdict(claim: Claim, sources: dict[str, SourceText],
             passages: dict[str, PassageIndex] | None = None) -> ClaimVerdict:
    """Verify a claim the way an agent stage does, so the renderer receives a genuine verdict."""
    return verify_claim(claim, sources, None, passages)


def _report(**fields) -> Report:
    """A minimal report referring to claims by identifier."""
    return Report(ticker='AAPL', as_of=date(2026, 9, 20), thesis='A durable business.',
                  direction='constructive', confidence='medium', **fields)


def test_a_passage_citation_reaches_the_appendix():
    """The preferred citation form. It resolves through the passage table, which render time does not have."""
    index = PassageIndex.build('doc-1', TEXT)
    passage = index.get(1)
    claim = _claim('aaaaaaaa', 'The filing describes the business',
                   [EvidenceRequest(document_id='doc-1', passage_id=1)])
    verdict = _verdict(claim, {'doc-1': SourceText(TEXT)}, {'doc-1': index})
    assert verdict.grounded

    markdown = render(_report(bull_case=['aaaaaaaa']), {'aaaaaaaa': verdict})

    assert 'The filing describes the business[1]' in markdown
    assert '_No citations resolved._' not in markdown
    assert f'chars {passage.char_start}-{passage.char_end}' in markdown
    assert passage.text[:40] in markdown


def test_an_identifier_that_names_nothing_leaves_no_trace():
    """A reference the synthesiser invented must not become a heading with an empty citation under it."""
    markdown = render(_report(bull_case=['deadbeef'], numbers=['deadbeef']), {})

    assert 'deadbeef' not in markdown
    assert '_None supported by the evidence._' in markdown
    assert '_No financial findings survived verification._' in markdown


def test_the_same_span_cited_twice_is_one_numbered_source():
    """Two agents reading the same sentence is normal; an appendix listing it twice is not."""
    sources = {'doc-1': SourceText(TEXT)}
    quoted = 'designs and manufactures smartphones'
    first = _verdict(_claim('aaaaaaaa', 'It designs phones',
                            [EvidenceRequest(document_id='doc-1', quoted_text=quoted)]), sources)
    second = _verdict(_claim('bbbbbbbb', 'It also makes computers',
                             [EvidenceRequest(document_id='doc-1', quoted_text=quoted)]), sources)

    markdown = render(_report(bull_case=['aaaaaaaa', 'bbbbbbbb']),
                      {'aaaaaaaa': first, 'bbbbbbbb': second})

    assert 'It designs phones[1]' in markdown
    assert 'It also makes computers[1]' in markdown, 'the same span was numbered twice'
    assert '\n2. ' not in markdown


def test_two_different_passages_are_two_sources():
    """They once collided: an unmatched evidence id ignored the passage number, so both keyed the same."""
    index = PassageIndex.build('doc-1', TEXT)
    first = _verdict(_claim('aaaaaaaa', 'First point',
                            [EvidenceRequest(document_id='doc-1', passage_id=1)]), {}, {'doc-1': index})
    second = _verdict(_claim('bbbbbbbb', 'Second point',
                             [EvidenceRequest(document_id='doc-1', passage_id=2)]), {}, {'doc-1': index})

    markdown = render(_report(bull_case=['aaaaaaaa', 'bbbbbbbb']),
                      {'aaaaaaaa': first, 'bbbbbbbb': second})

    assert 'First point[1]' in markdown
    assert 'Second point[2]' in markdown


def test_a_cited_figure_shows_what_was_claimed_rather_than_an_empty_quote():
    """A reported figure has no character range, but it must still say what it was."""
    request = EvidenceRequest(kind=EvidenceKind.FACT, fact_id='Revenue',
                              quoted_text='Revenue for the year was 391,035 million dollars')
    evidence = verify_evidence(request, {}, None, FactIndex(by_metric={'Revenue': (391_035_000_000.0,)}))
    assert evidence.is_grounded
    assert evidence.match_kind is MatchKind.EXACT

    claim = _claim('cccccccc', 'Revenue was 391 billion dollars', [request], ClaimType.NUMERIC)
    verdict = ClaimVerdict(claim=claim, evidence=(evidence,), grounded=True, reason='checked')

    markdown = render(_report(numbers=['cccccccc']), {'cccccccc': verdict})

    assert '`Revenue` (reported figure)' in markdown
    assert '391,035 million dollars' in markdown
    assert '— ""' not in markdown


def test_the_footer_reports_no_score_when_nothing_was_checkable():
    markdown = render(_report(), {})
    assert 'Groundedness: **not measured**' in markdown


def test_unresolved_disagreements_are_stated_as_unresolved():
    report = _report(disagreements=[Disagreement(question='Is the services margin sustainable?',
                                                 bull_position='Yes, on the disclosed mix.',
                                                 bear_position='Unproven at this scale.')])
    markdown = render(report, {})
    assert 'Is the services margin sustainable?' in markdown
    assert '_Unresolved on the available evidence._' in markdown


def test_an_unmatched_evidence_row_is_never_numbered():
    """Verification drops such a claim, but if one reaches the renderer it must not gain a citation."""
    claim = _claim('aaaaaaaa', 'Management promised forty percent growth',
                   [EvidenceRequest(document_id='doc-1', quoted_text='guarantees a forty percent increase')])
    verdict = _verdict(claim, {'doc-1': SourceText(TEXT)})
    assert not verdict.grounded

    markdown = render(_report(bull_case=['aaaaaaaa']), {'aaaaaaaa': verdict})

    assert 'Management promised forty percent growth' in markdown
    assert '[1]' not in markdown
    assert '_No citations resolved._' in markdown


def test_evidence_unused_by_any_rendered_claim_never_appears():
    """The appendix lists what the note cites, not everything the run verified."""
    sources = {'doc-1': SourceText(TEXT)}
    used = _verdict(_claim('aaaaaaaa', 'It designs phones',
                           [EvidenceRequest(document_id='doc-1',
                                            quoted_text='designs and manufactures smartphones')]), sources)
    unused = _verdict(_claim('bbbbbbbb', 'Margin expanded',
                             [EvidenceRequest(document_id='doc-1',
                                              quoted_text='Gross margin expanded')]), sources)

    markdown = render(_report(bull_case=['aaaaaaaa']), {'aaaaaaaa': used, 'bbbbbbbb': unused})

    assert 'Gross margin expanded' not in markdown
    assert 'Margin expanded' not in markdown


def _inference(claim_id: str, text: str) -> Claim:
    """A debater's claim: reasoning over checked claims, carrying no evidence of its own."""
    return Claim(claim_id=claim_id, claim_type=ClaimType.INFERENCE, text=text)


def test_a_reference_to_a_checked_claim_becomes_that_claims_citation():
    """The debate names the claims it reasons from. Left alone those names reach the reader as hexadecimal."""
    sources = {'doc-1': SourceText(TEXT)}
    grounded = _verdict(_claim('95988326', 'Revenue was 391,035 million dollars',
                               [EvidenceRequest(document_id='doc-1',
                                                quoted_text='Revenue for the year was 391,035')]), sources)
    argument = ClaimVerdict(claim=_inference('aaaaaaaa', 'Based on [95988326], the turnaround is real'),
                            evidence=(), grounded=True, reason='inference, exempt by design')

    markdown = render(_report(bull_case=['aaaaaaaa']), {'95988326': grounded, 'aaaaaaaa': argument})

    assert 'Based on [1], the turnaround is real' in markdown
    assert '95988326' not in markdown
    assert '391,035' in markdown, 'the cited passage never reached the appendix'


def test_several_references_in_one_bracket_each_get_a_number():
    sources = {'doc-1': SourceText(TEXT)}
    first = _verdict(_claim('95988326', 'It designs phones',
                            [EvidenceRequest(document_id='doc-1',
                                             quoted_text='designs and manufactures smartphones')]), sources)
    second = _verdict(_claim('6f95ac32', 'Margin expanded',
                             [EvidenceRequest(document_id='doc-1',
                                              quoted_text='Gross margin expanded')]), sources)
    argument = ClaimVerdict(claim=_inference('aaaaaaaa', 'On [95988326, 6f95ac32] the case rests'),
                            evidence=(), grounded=True, reason='inference, exempt by design')

    markdown = render(_report(bull_case=['aaaaaaaa']),
                      {'95988326': first, '6f95ac32': second, 'aaaaaaaa': argument})

    assert 'On [1][2] the case rests' in markdown


def test_a_reference_to_nothing_leaves_no_gap():
    argument = ClaimVerdict(claim=_inference('aaaaaaaa', 'The turnaround [deadbeef] is real'),
                            evidence=(), grounded=True, reason='inference, exempt by design')

    markdown = render(_report(bull_case=['aaaaaaaa']), {'aaaaaaaa': argument})

    assert 'The turnaround is real' in markdown
    assert 'deadbeef' not in markdown


def test_each_section_of_one_filing_is_its_own_source(tmp_path):
    """Items 1, 1A and 7 come from one 10-K and shared its document_id.

    Three agents then registered three different texts under one key, so whichever registered last decided
    what all three were checked against. Passage numbers resolved against the wrong section and were recorded
    as exact matches, because a number cannot be compared to the text it was meant to point at.
    """
    from datetime import date as _date

    from conftest import filing_html

    from fin_research.config import Config
    from fin_research.core.supervisor import load_sources
    from fin_research.ingest import store as store_module
    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.sections import split
    from fin_research.ingest.text import clean

    cfg = Config(data_root=tmp_path)
    annual = Filing(cik=320193, accession='acc-1', form='10-K', filed=_date(2026, 1, 31),
                    report_date=_date(2025, 12, 31), primary_document='aapl.htm')
    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'AAPL')
        document = clean(filing_html('aapl_tech'))
        document_id = store_module.store_document(con, annual, 'aapl.htm', 'primary', document)
        store_module.store_sections(con, document_id, annual.accession, split(document))

        sources = load_sources(con, 'AAPL', _date(2026, 9, 20))

    identifiers = [identifier for identifier, _ in sources.sections.values()]
    assert len(identifiers) == len(set(identifiers)), 'two sections share one citation key'
    assert document_id not in identifiers, 'a section is cited as the whole document'


# --- the assembled note --------------------------------------------------------
def test_a_note_resolves_identifiers_and_numbers_each_source_once(tmp_path):
    """What the dashboard reads. The same span cited by two claims is one entry in the appendix."""
    from fin_research.core.state import State
    from fin_research.report.note import as_json, build_note

    sources = {'doc-1': SourceText(TEXT)}
    quoted = 'designs and manufactures smartphones'
    first = _verdict(_claim('aaaaaaaa', 'It designs phones',
                            [EvidenceRequest(document_id='doc-1', quoted_text=quoted)]), sources)
    second = _verdict(_claim('bbbbbbbb', 'On [aaaaaaaa] the case rests', [], ClaimType.INFERENCE), sources)

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
        state.save_artifact(run_id, 'synthesis', 'Report', _report(
            numbers=['aaaaaaaa'], bull_case=['bbbbbbbb', 'deadbeef']).model_dump(mode='json'))
        state.save_artifact(run_id, 'verification', 'Verdicts', {
            identifier: {'claim': verdict.claim.model_dump(mode='json'),
                         'evidence': [e.model_dump(mode='json') for e in verdict.evidence],
                         'grounded': verdict.grounded, 'reason': verdict.reason, 'numeric': None}
            for identifier, verdict in (('aaaaaaaa', first), ('bbbbbbbb', second))})

        body = as_json(build_note(state, run_id))

    assert [c['text'] for c in body['numbers']] == ['It designs phones']
    assert body['numbers'][0]['citations'] == [1]

    # The inference names a claim, not a source, and that name becomes the source the claim rested on.
    assert body['bull_case'][0]['text'] == 'On [1] the case rests'
    assert len(body['bull_case']) == 1, 'an identifier naming nothing became a claim'
    assert len(body['sources']) == 1, 'one span was numbered twice'


def test_a_run_with_no_report_has_no_note(tmp_path):
    from fin_research.core.state import State
    from fin_research.report.note import build_note

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
        assert build_note(state, run_id) is None


def test_a_report_stored_before_the_schema_settled_is_still_readable(tmp_path):
    """Reports on disk embed whole claims and a whole valuation; both fields have since moved or gone."""
    from fin_research.report.note import modernise

    older = {
        'ticker': 'AAPL', 'as_of': '2026-09-20', 'thesis': 'A durable business.',
        'direction': 'constructive', 'confidence': 'medium', 'numbers': [],
        'bull_case': [{'claim_id': '', 'claim_type': 'fact', 'text': 'The filing describes the business',
                       'subject': None, 'polarity': 'bull', 'importance': 4, 'numeric_value': None,
                       'numeric_unit': None, 'metric': None, 'attacks': [], 'evidence': []}],
        'bear_case': [], 'falsifiers': [], 'disagreements': [], 'generated_at': None,
        'valuation': {'method': 'Discounted cash flow', 'current_price': 220.0,
                      'assumptions': [], 'scenarios': []},
    }

    updated, lifted, valuation = modernise(older)

    assert 'valuation' not in updated, 'a field the schema dropped would fail extra=forbid'
    assert valuation is not None and valuation['method'] == 'Discounted cash flow'
    assert len(lifted) == 1
    assert updated['bull_case'] == [lifted[0].claim_id], 'the claim was not replaced by its identifier'
    assert Report.model_validate(updated).ticker == 'AAPL'


def test_a_current_report_passes_through_unchanged():
    from fin_research.report.note import modernise

    current = _report(bull_case=['aaaaaaaa'], numbers=['bbbbbbbb']).model_dump(mode='json')
    updated, lifted, valuation = modernise(current)

    assert updated == current
    assert lifted == []
    assert valuation is None
