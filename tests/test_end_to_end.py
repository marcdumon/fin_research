"""The whole route, end to end, with no provider and no money.

This is the payoff of routing every model call through one client: the complete pipeline -- four readers in
parallel, the statements, a three-call debate, a valuation and the final note -- is exercisable offline.

The fake replies quote real text from a real filing, so the verification in the middle is doing genuine work
rather than being waved through. One reply deliberately invents a quotation, and the test asserts it is thrown
away: that is the property the whole design exists to guarantee, so it is checked at the level of the finished
report, not only in a unit test.
"""

import json
from datetime import date

import httpx
import pytest
import respx
from conftest import filing_html

from fin_research.agents.base import RunContext
from fin_research.config import Config
from fin_research.core.state import State
from fin_research.core.supervisor import Sources, run_deep_dive
from fin_research.ingest.sections import split
from fin_research.ingest.text import clean
from fin_research.llm.client import ModelClient, response_payload
from fin_research.report.render import render

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'
FABRICATED = 'The Company guarantees a forty percent increase in iPhone sales next fiscal year.'


@pytest.fixture(scope='module')
def filing() -> dict:
    """Real sections of a real annual report, keyed by item."""
    document = clean(filing_html('aapl_tech'))
    result = split(document)
    return {item: result.by_item(item).text(document.text) for item in ('1', '1A', '7')}


def _quote(text: str, start: int, length: int = 90) -> str:
    """A verbatim slice of real filing text, as an honest agent would cite it."""
    return ' '.join(text[start:start + length].split())


def _finding_set(document_id: str, quotes: list[str], invent: bool = False) -> str:
    """A FindingSet reply citing the given quotes, optionally with one fabricated citation added."""
    claims = [{
        'claim_id': '', 'claim_type': 'fact', 'text': f'The filing states: {q[:80]}',
        'subject': None, 'polarity': 'neutral', 'importance': 3,
        'numeric_value': None, 'numeric_unit': None, 'attacks': [],
        'evidence': [{'kind': 'span', 'document_id': document_id, 'section_id': None,
                      'quoted_text': q, 'fact_id': None}],
    } for q in quotes]

    if invent:
        claims.append({
            'claim_id': '', 'claim_type': 'fact', 'text': 'Management promised 40% iPhone growth',
            'subject': None, 'polarity': 'bull', 'importance': 5,
            'numeric_value': None, 'numeric_unit': None, 'attacks': [],
            'evidence': [{'kind': 'span', 'document_id': document_id, 'section_id': None,
                          'quoted_text': FABRICATED, 'fact_id': None}],
        })

    return json.dumps({'findings': [{'headline': 'What the source says', 'detail': 'Observations.',
                                     'claims': claims, 'tags': []}], 'unresolved': []})


def _thesis(stance: str, round_number: int = 1) -> str:
    return json.dumps({
        'stance': stance, 'round': round_number,
        'summary': f'The {stance} case rests on the verified findings.',
        'claims': [{'claim_id': '', 'claim_type': 'judgement', 'text': f'A {stance} view is warranted',
                    'subject': None, 'polarity': stance, 'importance': 4, 'numeric_value': None,
                    'numeric_unit': None, 'evidence': [], 'attacks': []}],
        'falsifiers': [f'Gross margin moves against the {stance} case for two consecutive quarters'],
    })


def _valuation() -> str:
    return json.dumps({
        'method': 'Discounted cash flow', 'current_price': 220.0,
        'assumptions': [{'name': 'Revenue growth', 'value': 0.05, 'unit': 'per year',
                         'rationale': 'In line with the reported trend.', 'evidence': []}],
        'scenarios': [{'name': 'bear', 'value_per_share': 150.0, 'probability': 0.25, 'drivers': []},
                      {'name': 'base', 'value_per_share': 230.0, 'probability': 0.5, 'drivers': []},
                      {'name': 'bull', 'value_per_share': 310.0, 'probability': 0.25, 'drivers': []}],
    })


def _report(document_id: str, quote: str) -> str:
    return json.dumps({
        'ticker': 'AAPL', 'as_of': '2026-09-20',
        'thesis': 'The business is durable and the evidence supports a constructive view.',
        'direction': 'constructive', 'confidence': 'medium',
        'numbers': [], 'bear_case': [], 'disagreements': [
            {'question': 'Is the services margin sustainable?',
             'bull_position': 'Yes, on the disclosed mix.', 'bear_position': 'Unproven at this scale.',
             'resolution': None, 'resolved': False}],
        'bull_case': [{'claim_id': '', 'claim_type': 'fact', 'text': 'The filing describes the business',
                       'subject': None, 'polarity': 'bull', 'importance': 4, 'numeric_value': None,
                       'numeric_unit': None, 'attacks': [],
                       'evidence': [{'kind': 'span', 'document_id': document_id, 'section_id': None,
                                     'quoted_text': quote, 'fact_id': None}]}],
        'falsifiers': ['Services revenue declines year over year'],
        'valuation': json.loads(_valuation()), 'generated_at': None,
    })


@pytest.fixture
def replies(filing) -> dict:
    """A canned reply per agent, citing real filing text."""
    return {
        'business': _finding_set('doc-1', [_quote(filing['1'], 500), _quote(filing['1'], 3000)]),
        'risk_factors': _finding_set('doc-1A', [_quote(filing['1A'], 800)], invent=True),
        'management_view': _finding_set('doc-7', [_quote(filing['7'], 1200)]),
        'financials': _finding_set('doc-fin', [_quote(filing['7'], 2500)]),
        'bull': _thesis('bull'),
        'bear': _thesis('bear'),
        'valuation': _valuation(),
        'synthesis': _report('doc-1', _quote(filing['1'], 500)),
    }


def _router(replies: dict):
    """Answer each call according to which agent's system prompt it carries."""
    order = ('risk_factors', 'management_view', 'business', 'financials', 'valuation', 'synthesis',
             'bear', 'bull')

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body['messages'][0]['content'].lower()
        user = body['messages'][1]['content'].lower()

        for agent in order:
            marker = {'risk_factors': 'risk factors of a company',
                      'management_view': "management's discussion of results",
                      'business': 'reading a company filing',
                      'financials': 'reading reported financial statements',
                      'valuation': 'valuing a company',
                      'synthesis': 'writing the final research note',
                      'bear': 'sceptical case',
                      'bull': 'constructive case'}[agent]
            if marker in system:
                reply = replies[agent]
                if agent == 'bull' and 'rebuttal' not in user and 'answers it' in user:
                    reply = _thesis('bull', 2)
                return httpx.Response(200, json=response_payload(reply, prompt_tokens=800,
                                                                completion_tokens=200, cost=0.002))
        raise AssertionError(f'unexpected agent prompt: {system[:80]}')

    return respond


@respx.mock
async def test_a_full_run_produces_a_cited_report(tmp_path, filing, replies):
    respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)

    sources = Sources(
        sections={'1': ('doc-1', filing['1']), '1A': ('doc-1A', filing['1A']), '7': ('doc-7', filing['7'])},
        statements=('doc-fin', filing['7'][:5000]))

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await run_deep_dive(ctx, sources, price=220.0)

        assert outcome.status == 'done'
        assert outcome.report is not None
        assert outcome.findings, 'the readers produced nothing'

        # The fabricated citation must not have survived into the findings.
        every_quote = [request.quoted_text for finding in outcome.findings
                       for claim in finding.claims for request in claim.evidence]
        assert FABRICATED not in every_quote, 'an invented quotation reached the report'
        assert outcome.groundedness is not None and outcome.groundedness < 1.0

        # Spend is metered, and every agent ran as a tracked task.
        summary = state.run(run_id)
        assert summary.spend_usd > 0
        agents_run = {row['agent'] for row in state.cost_by_agent(run_id)}
        assert {'business', 'risk_factors', 'management_view', 'financials',
                'bull', 'bear', 'valuation', 'synthesis'} <= agents_run

        markdown = render(outcome.report, ctx.sources, groundedness=outcome.groundedness,
                          cost_usd=summary.spend_usd)

    assert '# AAPL — research note' in markdown
    assert 'Unresolved disagreements' in markdown
    assert 'Is the services margin sustainable?' in markdown
    assert '[1]' in markdown, 'no inline citation was rendered'
    assert FABRICATED not in markdown
    assert 'Groundedness' in markdown


@respx.mock
async def test_a_run_that_hits_its_budget_stops_cleanly(tmp_path, filing, replies):
    """A ceiling reached is a normal outcome: close the run, keep what was verified, do not raise."""
    respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)
    sources = Sources(sections={'1': ('doc-1', filing['1']), '1A': ('doc-1A', filing['1A'])})

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=0.00001)
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await run_deep_dive(ctx, sources, price=None)

        assert outcome.status == 'budget_exceeded'
        assert outcome.report is None
        assert state.run(run_id).status == 'budget_exceeded'


@respx.mock
async def test_the_cli_path_runs_end_to_end_from_the_store(tmp_path, filing, replies):
    """Exercises prepare() -> client -> run_deep_dive, the seam the direct-construction test skips."""
    from fin_research.core.supervisor import prepare
    from fin_research.ingest import store as store_module
    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.text import clean as clean_html

    respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)

    annual = Filing(cik=320193, accession='acc-1', form='10-K', filed=date(2026, 1, 31),
                    report_date=date(2025, 12, 31), primary_document='aapl.htm')
    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'AAPL')
        document = clean_html(filing_html('aapl_tech'))
        document_id = store_module.store_document(con, annual, 'aapl.htm', 'primary', document)
        store_module.store_sections(con, document_id, annual.accession, split(document))

    with State(tmp_path / 'state.sqlite') as state:
        prepared = prepare(cfg, state, 'AAPL', date(2026, 9, 20), budget_usd=5.0)
        assert not prepared.sources.is_empty, 'sections did not load back out of the store'

        async with ModelClient(cfg=cfg, state=state, run_id=prepared.run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=prepared.run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await run_deep_dive(ctx, prepared.sources, prepared.price)

        assert outcome.report is not None
        assert state.run(prepared.run_id).status == 'done'


@respx.mock
async def test_a_run_dated_before_a_filing_cannot_read_it(tmp_path, filing, replies):
    """Point-in-time discipline: a 2019 run must not see a filing published in 2026."""
    from fin_research.core.supervisor import prepare
    from fin_research.ingest import store as store_module
    from fin_research.ingest.edgar import Filing
    from fin_research.ingest.text import clean as clean_html

    cfg = Config(data_root=tmp_path)
    annual = Filing(cik=320193, accession='acc-1', form='10-K', filed=date(2026, 1, 31),
                    report_date=date(2025, 12, 31), primary_document='aapl.htm')
    with store_module.connect(cfg) as con:
        store_module.store_filings(con, [annual], 'AAPL')
        document = clean_html(filing_html('aapl_tech'))
        document_id = store_module.store_document(con, annual, 'aapl.htm', 'primary', document)
        store_module.store_sections(con, document_id, annual.accession, split(document))

    with State(tmp_path / 'state.sqlite') as state:
        prepared = prepare(cfg, state, 'AAPL', date(2019, 1, 1), budget_usd=5.0)

    assert prepared.sources.is_empty, 'a run read a filing published seven years after its as-of date'


@respx.mock
async def test_a_wrong_figure_never_reaches_the_report(tmp_path, filing, replies):
    """A model can quote a real sentence and state the wrong number. Only the fact check catches that.

    The reply here cites a genuine quotation from the filing and attaches a revenue figure the company never
    reported. Span verification passes it, because the sentence is real; the claim must still be dropped.
    """
    from dataclasses import dataclass

    from fin_research.core.verification import FactIndex

    @dataclass
    class Reported:
        metric: str
        value: float

    honest = _quote(filing['1'], 500)
    wrong = json.dumps({'findings': [{'headline': 'Revenue', 'detail': 'From the filing.', 'tags': [],
                                      'claims': [{
                                          'claim_id': '', 'claim_type': 'numeric',
                                          'text': 'Revenue reached $900 billion', 'subject': None,
                                          'polarity': 'bull', 'importance': 5,
                                          'numeric_value': 900.0, 'numeric_unit': 'USD billions',
                                          'metric': 'Revenue', 'attacks': [],
                                          'evidence': [{'kind': 'span', 'document_id': 'doc-1',
                                                        'section_id': None, 'quoted_text': honest,
                                                        'fact_id': None}]}]}],
                        'unresolved': []})

    respx.post(COMPLETIONS).mock(side_effect=_router({**replies, 'business': wrong}))
    cfg = Config(data_root=tmp_path)
    sources = Sources(sections={'1': ('doc-1', filing['1']), '1A': ('doc-1A', filing['1A'])})
    facts = FactIndex.from_facts([Reported('Revenue', 416_161_000_000)])

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20), facts=facts)
            outcome = await run_deep_dive(ctx, sources, price=None)

    stated = [claim.text for finding in outcome.findings for claim in finding.claims]
    assert 'Revenue reached $900 billion' not in stated, 'a figure the company never reported survived'

    outcomes = {k: v for result in outcome.results for k, v in result.verification.numeric_outcomes().items()}
    assert outcomes.get('contradicted', 0) >= 1, 'the contradiction should be recorded, not merely dropped'
