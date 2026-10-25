"""What each agent is shown, and what it is therefore able to say.

Two stages were asked to cite sources they had never been given -- the debate, which sees summaries, and the
synthesiser, which sees the debate. Both did as they were told and invented document identifiers, and every
claim they made was discarded on the way out. Nothing in the pipeline failed; the verification worked exactly
as designed, on work that could never have passed it.

These tests hold each prompt to what its agent actually has in front of it.
"""

from datetime import date

import pytest

from fin_research.agents.base import RunContext, claim_pool, identify
from fin_research.agents.debate import bear, bull
from fin_research.agents.synthesis import synthesis
from fin_research.config import Config
from fin_research.core.schemas import Claim, ClaimType, EvidenceRequest, Finding, Thesis


@pytest.fixture
def findings() -> list[Finding]:
    """Two verified findings, identified as the pipeline identifies them after checking."""
    claims = [identify(Claim(claim_type=ClaimType.FACT, text='Revenue grew 3% in fiscal 2026',
                             evidence=[EvidenceRequest(document_id='doc-7', passage_id=4)])),
              identify(Claim(claim_type=ClaimType.FACT, text='Five customers are 19% of revenue',
                             evidence=[EvidenceRequest(document_id='doc-1A', passage_id=9)]))]
    return [Finding(headline='What the filing says', detail='Observations.', claims=claims)]


@pytest.fixture
def ctx(tmp_path) -> RunContext:
    """A context with no client: these tests build prompts, they do not send them."""
    return RunContext(cfg=Config(data_root=tmp_path), state=None, client=None, run_id='r1',  # type: ignore[arg-type]
                      ticker='VSAT', as_of=date(2026, 9, 21))


def test_a_claim_identifier_is_stable_and_derived_from_the_text():
    first = identify(Claim(claim_type=ClaimType.FACT, text='Revenue grew 3%'))
    second = identify(Claim(claim_type=ClaimType.JUDGEMENT, text='Revenue grew 3%'))
    assert first.claim_id == second.claim_id
    assert len(first.claim_id) == 8


def test_an_identifier_the_model_supplied_is_overwritten():
    """Structured output makes every field required, so a model fills this one with 'c1' and means nothing."""
    claim = identify(Claim(claim_id='c1', claim_type=ClaimType.FACT, text='Revenue grew 3%'))
    assert claim.claim_id != 'c1'


def test_the_debate_is_given_the_claims_it_must_argue_from(ctx, findings):
    identifiers = [c.claim_id for c in findings[0].claims]
    prompt = bull.build_prompt(ctx, 8000, findings=findings)

    for identifier in identifiers:
        assert identifier in prompt
    assert 'Revenue grew 3% in fiscal 2026' in prompt


def test_the_debate_is_not_asked_to_quote_sources_it_cannot_see(ctx, findings):
    """It is shown claim summaries. An instruction to quote the filing can only produce invented citations."""
    for agent in (bull, bear):
        prompt = agent.build_prompt(ctx, 8000, findings=findings, opposing='The bear case.')
        assert 'quoting the source verbatim' not in prompt
        assert 'you cannot quote them' in prompt


def test_the_synthesiser_is_given_the_claims_and_told_to_refer_to_them(ctx, findings):
    thesis = Thesis(stance='bull', round=1, summary='Constructive.',
                    claims=[identify(Claim(claim_type=ClaimType.JUDGEMENT, text='The business is durable'))],
                    falsifiers=['Revenue declines'])
    prompt = synthesis.build_prompt(ctx, 8000, findings=findings, bull=thesis, bear=None, rebuttal=None)

    _, pool = claim_pool(findings, [thesis])
    for identifier in pool:
        assert identifier in prompt
    assert 'by their identifiers' in prompt
    assert 'not text' in prompt


def test_the_valuation_is_given_the_statements_rather_than_summaries_of_them(ctx, findings):
    """One run assumed a cash balance and a share count the balance sheet states outright, and priced off both."""
    from fin_research.agents.synthesis import valuation

    statements = 'Consolidated Balance Sheets - USD ($)\n $ in Thousands\nCash and cash equivalents | 1,746,835'
    prompt = valuation.build_prompt(ctx, 8000, findings=findings, price=74.31, statements=statements)

    assert '1,746,835' in prompt
    assert 'Do not estimate a figure the' in prompt
    assert 'Current share price: 74.31' in prompt


def test_the_valuation_says_so_when_it_has_no_statements(ctx, findings):
    from fin_research.agents.synthesis import valuation

    prompt = valuation.build_prompt(ctx, 8000, findings=findings, price=None, statements=None)
    assert '(not available)' in prompt
    assert 'Current share price: not available' in prompt


def test_the_claim_pool_carries_both_the_readers_and_the_debate(findings):
    thesis = Thesis(stance='bear', round=1, summary='Sceptical.',
                    claims=[identify(Claim(claim_type=ClaimType.JUDGEMENT, text='Leverage is a problem'))],
                    falsifiers=['Debt is repaid'])
    listing, pool = claim_pool(findings, [thesis, None])

    assert len(pool) == 3
    assert 'Leverage is a problem' in listing
    assert all(claim.claim_id in listing for claim in pool.values())


def test_a_string_past_its_limit_is_cut_rather_than_losing_the_artifact():
    """A run lost a whole valuation -- scenarios, assumptions, reasoning -- over a 130-character method."""
    from fin_research.core.schemas import Valuation

    valuation = Valuation.model_validate({'method': 'EV/EBITDA multiple valuation. ' * 10, 'current_price': 74.31,
                                          'assumptions': [], 'scenarios': []})
    assert len(valuation.method) == 120
    assert valuation.current_price == 74.31


def test_a_missing_field_still_fails():
    """Cutting a long string is a courtesy; inventing an absent one would not be."""
    from pydantic import ValidationError

    from fin_research.core.schemas import Valuation

    with pytest.raises(ValidationError):
        Valuation.model_validate({'current_price': 74.31, 'assumptions': [], 'scenarios': []})
