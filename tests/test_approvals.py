"""Tests for the human-approval gate.

The gate only means something if a rejection genuinely stops work and an approval genuinely releases it, so
those are checked end to end through the supervisor rather than only at the primitive.
"""

import asyncio
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import respx
from test_end_to_end import COMPLETIONS, _router

from fin_research.agents.base import RunContext
from fin_research.config import Config
from fin_research.core.approvals import APPROVED, DOWNGRADED, EXPIRED, REJECTED, Rejected, decision, gate, wait_for_decision
from fin_research.core.state import State
from fin_research.core.supervisor import run_deep_dive
from fin_research.llm.client import ModelClient


@pytest.fixture
def state(tmp_path) -> State:
    with State(tmp_path / 'state.sqlite') as store:
        yield store


@pytest.fixture
def run(state) -> str:
    return state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)


async def _answer_after(state: State, approval_id: str, status: str, delay: float = 0.05) -> None:
    """Stand in for someone clicking in the dashboard, which writes from another process."""
    await asyncio.sleep(delay)
    state.decide_approval(approval_id, status)


# --- the primitive ------------------------------------------------------------
def test_a_pending_request_has_no_decision_yet(state, run):
    approval_id = state.request_approval(run, 'agent', 'Run synthesis?')
    assert decision(state, approval_id) is None


def test_an_unknown_request_has_no_decision(state):
    assert decision(state, 'nope') is None


async def test_waiting_returns_the_answer_once_it_arrives(state, run):
    approval_id = state.request_approval(run, 'agent', 'Run synthesis?')
    answering = asyncio.create_task(_answer_after(state, approval_id, APPROVED))

    assert await wait_for_decision(state, approval_id, timeout=5, poll=0.01) == APPROVED
    await answering


async def test_no_answer_in_time_is_recorded_rather_than_left_pending(state, run):
    """A request that appears to still be waiting on someone, forever, is worse than one marked expired."""
    approval_id = state.request_approval(run, 'agent', 'Run synthesis?')

    assert await wait_for_decision(state, approval_id, timeout=0.05, poll=0.01) == EXPIRED
    assert state.pending_approvals(run) == []


async def test_gate_raises_on_refusal(state, run):
    approval_id_holder: list[str] = []

    async def refuse():
        await asyncio.sleep(0.02)
        pending = state.pending_approvals(run)
        approval_id_holder.append(pending[0]['approval_id'])
        state.decide_approval(pending[0]['approval_id'], REJECTED)

    refusing = asyncio.create_task(refuse())
    with pytest.raises(Rejected):
        await gate(state, run, subject='agent', prompt='Run synthesis?', timeout=5)
    await refusing

    assert approval_id_holder, 'the gate never registered a request'


# --- through the supervisor ---------------------------------------------------
@respx.mock
async def test_a_refused_step_is_skipped_and_never_calls_a_model(tmp_path, filing_sources, replies):
    """The point of the gate: refusing must stop the spend, not merely annotate it."""
    route = respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)
    cfg = replace(cfg, llm=replace(cfg.llm, gate_tiers=('expensive',), gate_timeout_seconds=5.0))

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)

        async def refuse_everything():
            for _ in range(200):
                for pending in state.pending_approvals(run_id):
                    state.decide_approval(pending['approval_id'], REJECTED)
                await asyncio.sleep(0.01)

        refuser = asyncio.create_task(refuse_everything())
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await run_deep_dive(ctx, filing_sources, price=None)
        refuser.cancel()

        # The expensive agents were gated; the cheap and mid ones ran normally.
        called = {call.request.content.decode() for call in route.calls}
        assert not any('valuing a company' in body.lower() for body in called)
        assert not any('writing the final research note' in body.lower() for body in called)

        skipped = {row['agent'] for row in state.query(
            "SELECT agent FROM tasks WHERE run_id = ? AND status = 'skipped'", (run_id,))}
        assert {'valuation', 'synthesis'} <= skipped

    assert outcome.report is None
    assert outcome.findings, 'the ungated agents should still have run'


@respx.mock
async def test_an_approved_step_proceeds(tmp_path, filing_sources, replies):
    respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)
    cfg = replace(cfg, llm=replace(cfg.llm, gate_tiers=('expensive',), gate_timeout_seconds=5.0))

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)

        async def approve_everything():
            for _ in range(200):
                for pending in state.pending_approvals(run_id):
                    state.decide_approval(pending['approval_id'], APPROVED)
                await asyncio.sleep(0.01)

        approver = asyncio.create_task(approve_everything())
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await run_deep_dive(ctx, filing_sources, price=None)
        approver.cancel()

    assert outcome.report is not None, 'approving every step should produce the note'


@respx.mock
async def test_gating_is_off_unless_asked_for(tmp_path, filing_sources, replies):
    """An unattended run must not block on a human nobody told about."""
    respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)
    assert cfg.llm.gate_tiers == ()

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            outcome = await asyncio.wait_for(run_deep_dive(ctx, filing_sources, price=None), timeout=10)

    assert outcome.report is not None


# --- running a step on something cheaper --------------------------------------
async def test_a_downgrade_lets_the_step_proceed(state, run):
    """Refusing an expensive step on the last stage discards everything before it. Downgrading does not."""
    async def answer():
        await asyncio.sleep(0.02)
        pending = state.pending_approvals(run)
        state.decide_approval(pending[0]['approval_id'], DOWNGRADED)

    answering = asyncio.create_task(answer())
    decision = await gate(state, run, subject='agent', prompt='Run synthesis?', timeout=5)
    await answering

    assert decision == DOWNGRADED


async def test_a_rejection_still_stops_the_step(state, run):
    async def answer():
        await asyncio.sleep(0.02)
        state.decide_approval(state.pending_approvals(run)[0]['approval_id'], REJECTED)

    answering = asyncio.create_task(answer())
    with pytest.raises(Rejected):
        await gate(state, run, subject='agent', prompt='Run synthesis?', timeout=5)
    await answering


def test_tiers_are_ordered_so_cheaper_is_well_defined():
    from fin_research.config import Config

    llm = Config().llm
    assert llm.cheaper_tier('expensive') == 'mid'
    assert llm.cheaper_tier('mid') == 'cheap'
    assert llm.cheaper_tier('cheap') is None
    assert llm.cheaper_tier('nonsense') is None


@respx.mock
async def test_a_downgraded_step_runs_on_the_cheaper_model(tmp_path, filing_sources, replies):
    """The decision has to change which model is called, not merely be recorded."""
    route = respx.post(COMPLETIONS).mock(side_effect=_router(replies))
    cfg = Config(data_root=tmp_path)
    cfg = replace(cfg, llm=replace(cfg.llm, gate_tiers=('expensive',), gate_timeout_seconds=5.0))

    with State(tmp_path / 'state.sqlite') as state:
        run_id = state.start_run('AAPL', 320193, date(2026, 9, 20), budget_usd=5.0)

        async def downgrade_everything():
            for _ in range(300):
                for pending in state.pending_approvals(run_id):
                    state.decide_approval(pending['approval_id'], DOWNGRADED)
                await asyncio.sleep(0.01)

        answering = asyncio.create_task(downgrade_everything())
        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='k') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='AAPL', as_of=date(2026, 9, 20))
            await run_deep_dive(ctx, filing_sources, price=None)
        answering.cancel()

        used = {row['agent']: row['model'] for row in state.query(
            'SELECT agent, model FROM llm_calls WHERE run_id = ?', (run_id,))}

    assert used.get('synthesis') == cfg.llm.tiers['mid'], 'synthesis should have run on the cheaper tier'
    assert used.get('valuation') == cfg.llm.tiers['mid']
    assert route.call_count > 0


def test_the_gate_prompt_names_the_cheaper_model():
    """The button can only offer an alternative if the request says what it is."""
    from fin_research.agents.base import RunContext
    from fin_research.config import Config
    from fin_research.core.supervisor import _gate_prompt

    cfg = Config()
    with State(Path('/tmp') / 'gate-prompt-test.sqlite') as store:
        run_id = store.start_run('AAPL', None, date(2026, 9, 20), 2.0)
        ctx = RunContext(cfg=cfg, state=store, client=None, run_id=run_id,
                         ticker='AAPL', as_of=date(2026, 9, 20))
        prompt = _gate_prompt(ctx, 'synthesis')

    assert cfg.llm.tiers['expensive'] in prompt
    assert cfg.llm.tiers['mid'] in prompt
    assert 'Cheaper:' in prompt
