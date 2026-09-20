"""Tests for model routing, parsing and the single call path.

No test reaches a provider. That is the concrete payoff of routing everything through one client: the whole
pipeline is exercisable without a key and without spending anything.
"""

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from fin_research.config import Config
from fin_research.core.state import BudgetExceeded, State
from fin_research.llm.client import ModelCallError, ModelClient, ModelRequest, response_payload
from fin_research.llm.parsing import ParseError, ParseMode, coerce
from fin_research.llm.router import estimate_tokens, resolve

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'


class Answer(BaseModel):
    """A minimal artifact schema."""

    verdict: str
    score: int = Field(ge=0, le=10)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_root=tmp_path)


@pytest.fixture
def state(tmp_path) -> State:
    with State(tmp_path / 'state.sqlite') as st:
        yield st


@pytest.fixture
def run(state) -> str:
    return state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=1.0)


@pytest.fixture
def client(cfg, state, run) -> ModelClient:
    return ModelClient(cfg=cfg, state=state, run_id=run, api_key='test-key')


def _request(**kwargs) -> ModelRequest:
    base = dict(agent='extraction', system='You extract evidence.', user='Extract from this filing.')
    return ModelRequest(**(base | kwargs))


# --- routing -----------------------------------------------------------------
def test_agents_are_routed_by_tier_not_by_name(cfg):
    assert resolve(cfg, 'extraction').tier == 'cheap'
    assert resolve(cfg, 'synthesis').tier == 'expensive'
    assert resolve(cfg, 'extraction', tier='expensive').model == resolve(cfg, 'synthesis').model


def test_an_unknown_agent_falls_back_to_the_cheap_tier(cfg):
    assert resolve(cfg, 'nobody').tier == 'cheap'


def test_tiering_is_what_makes_cheap_models_worth_using(cfg):
    """Reading a whole filing costs several times more on the expensive tier. That gap is the design.

    The multiple is not asserted tightly because provider prices move; what must hold is that the gap stays
    large enough to be worth routing around.
    """
    tokens = estimate_tokens('x' * 224_000)
    cheap = resolve(cfg, 'extraction').cost(tokens, 2000)
    expensive = resolve(cfg, 'synthesis').cost(tokens, 2000)
    assert expensive > 4 * cheap


def test_each_tier_carries_a_real_context_limit(cfg):
    """Truncation follows the model. The middle tier's window is a quarter of the cheap tier's."""
    assert resolve(cfg, 'financials').max_source_tokens < resolve(cfg, 'extraction').max_source_tokens
    assert all(resolve(cfg, agent).max_source_tokens > 0
               for agent in ('extraction', 'financials', 'synthesis'))


# --- parsing ------------------------------------------------------------------
@pytest.mark.parametrize(('reply', 'expected'), [
    ('{"verdict": "ok", "score": 7}', ParseMode.STRICT),
    ('Here is the JSON:\n```json\n{"verdict": "ok", "score": 7}\n```', ParseMode.FENCED),
    ('Sure! {"verdict": "ok", "score": 7} Hope that helps.', ParseMode.EMBEDDED),
    ('{"verdict": "ok", "score": 7,}', ParseMode.STRICT),
])
def test_the_ladder_recovers_the_usual_cheap_model_malformations(reply, expected):
    parsed, mode = coerce(reply, Answer)
    assert parsed.verdict == 'ok'
    assert mode is expected


def test_braces_inside_strings_do_not_confuse_the_extractor():
    parsed, _ = coerce('Note: {"verdict": "a {brace} inside", "score": 3}', Answer)
    assert parsed.verdict == 'a {brace} inside'


def test_a_reply_that_cannot_be_parsed_raises_with_the_reason():
    with pytest.raises(ParseError, match='Answer'):
        coerce('I would rather not answer that.', Answer)


def test_schema_violations_are_rejected_not_coerced():
    """score is bounded; a model exceeding it must trigger a re-prompt, not pass silently."""
    with pytest.raises(ParseError):
        coerce('{"verdict": "ok", "score": 99}', Answer)


# --- the call path ------------------------------------------------------------
@respx.mock
async def test_a_call_is_parsed_priced_and_recorded(client, state, run):
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(
        200, json=response_payload('{"verdict": "ok", "score": 7}', prompt_tokens=1000,
                                   completion_tokens=100, cost=0.0012)))

    async with client:
        result = await client.call(_request(schema=Answer))

    assert result.parsed.verdict == 'ok'
    assert result.cost_usd == 0.0012
    assert state.run(run).spend_usd == 0.0012
    assert state.query('SELECT count(*) FROM llm_calls')[0][0] == 1


@respx.mock
async def test_an_identical_prompt_is_served_from_cache_at_no_cost(client, state, run):
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(
        200, json=response_payload('{"verdict": "ok", "score": 7}', cost=0.5)))

    async with client:
        first = await client.call(_request(schema=Answer))
        second = await client.call(_request(schema=Answer))

    assert route.call_count == 1
    assert not first.cache_hit and second.cache_hit
    assert second.cost_usd == 0.0
    assert second.parsed.verdict == 'ok'
    # Both are recorded: the trace should show every step, not only the paid ones.
    assert state.query('SELECT count(*) FROM llm_calls')[0][0] == 2
    assert state.run(run).spend_usd == 0.5


@respx.mock
async def test_a_different_prompt_is_not_a_cache_hit(client):
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(
        200, json=response_payload('{"verdict": "ok", "score": 7}')))

    async with client:
        await client.call(_request(schema=Answer))
        await client.call(_request(schema=Answer, user='A different filing.'))

    assert route.call_count == 2


@respx.mock
async def test_the_budget_refuses_a_call_before_it_is_sent(cfg, state):
    """Metering afterwards reports; refusing beforehand controls. This is the difference."""
    run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=0.0001)
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=response_payload('{}')))

    async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
        with pytest.raises(BudgetExceeded):
            await client.call(_request(user='x' * 40_000, schema=Answer))

    assert route.call_count == 0, 'no request should have left the process'


@respx.mock
async def test_a_malformed_reply_is_re_prompted_with_the_error(client):
    """Telling the model what was wrong works better than asking the same question twice."""
    route = respx.post(COMPLETIONS).mock(side_effect=[
        httpx.Response(200, json=response_payload('not json at all')),
        httpx.Response(200, json=response_payload('{"verdict": "ok", "score": 5}')),
    ])

    async with client:
        result = await client.call(_request(schema=Answer))

    assert route.call_count == 2
    assert result.retries == 1
    assert result.parsed.score == 5


@respx.mock
async def test_rate_limiting_is_retried(client):
    route = respx.post(COMPLETIONS).mock(side_effect=[
        httpx.Response(429),
        httpx.Response(200, json=response_payload('{"verdict": "ok", "score": 1}')),
    ])

    async with client:
        result = await client.call(_request(schema=Answer))

    assert route.call_count == 2
    assert result.parsed.score == 1


@respx.mock
async def test_a_rejected_key_is_not_retried(client):
    """Repeating a request the provider refused only wastes time."""
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(401, json={'error': 'bad key'}))

    async with client:
        with pytest.raises(ModelCallError, match='401'):
            await client.call(_request(schema=Answer))

    assert route.call_count == 1


@respx.mock
async def test_a_failed_call_is_still_recorded(client, state):
    """Money spent on calls that errored is still spent; a ledger omitting them understates the cost."""
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(400, json={'error': 'bad request'}))

    async with client:
        with pytest.raises(ModelCallError):
            await client.call(_request(schema=Answer))

    row = state.query('SELECT error, agent FROM llm_calls')[0]
    assert row['agent'] == 'extraction'
    assert '400' in row['error']


@respx.mock
async def test_an_unparseable_reply_is_recorded_as_such(client, state):
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(
        200, json=response_payload('still not json')))

    async with client:
        result = await client.call(_request(schema=Answer))

    assert result.parsed is None
    assert result.parse_mode == ParseMode.FAILED.value
    assert state.query('SELECT parse_mode FROM llm_calls ORDER BY created_at DESC')[0][0] == 'failed'


async def test_a_missing_api_key_fails_with_a_useful_message(cfg, state, run, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    async with ModelClient(cfg=cfg, state=state, run_id=run) as client:
        with pytest.raises(ModelCallError, match='OPENROUTER_API_KEY'):
            await client.call(_request())


def test_nothing_outside_the_client_talks_to_a_provider():
    """The whole design rests on one call path. A second one would quietly break metering and budgets.

    Looks for the machinery of a call -- the completions endpoint and the bearer header -- rather than any
    mention of the provider, so a module that merely documents which environment variable to set is not an
    offender.
    """
    calling = ('chat/completions', 'Bearer ')
    offenders = [
        str(path.relative_to('src')) for path in Path('src/fin_research').rglob('*.py')
        if path.name != 'client.py' and any(marker in path.read_text() for marker in calling)
    ]
    assert offenders == [], f'these modules bypass the single call path: {offenders}'


# --- runs whose process has gone ----------------------------------------------
def test_a_run_whose_process_died_is_closed(state):
    """Nothing else detects this: the ledger only records what a live process tells it."""
    run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
    # A process id that cannot be running: the highest the kernel will allocate, plus one.
    state._write('UPDATE runs SET pid = ? WHERE run_id = ?', (2 ** 22 + 1, run_id))

    assert state.reconcile() == 1
    assert state.run(run_id).status == 'interrupted'


def test_a_live_run_is_left_alone(state):
    """This process is running, so a run recording it must not be closed."""
    run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
    assert state.reconcile() == 0
    assert state.run(run_id).status == 'running'


def test_a_finished_run_is_not_touched(state):
    run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
    state.finish_run(run_id, 'done')
    state._write('UPDATE runs SET pid = ? WHERE run_id = ?', (2 ** 22 + 1, run_id))

    assert state.reconcile() == 0
    assert state.run(run_id).status == 'done'


def test_a_new_run_records_the_process_that_owns_it(state):
    import os
    run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=2.0)
    assert state.query('SELECT pid FROM runs WHERE run_id = ?', (run_id,))[0]['pid'] == os.getpid()
