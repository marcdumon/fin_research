"""Start a gated run that blocks on approval, so the dashboard's gate can be exercised without a key.

Drives the real supervisor with recorded replies. The run genuinely stops at every expensive step and waits
for a decision written by the dashboard, which is the same path a paid run takes.
"""

import asyncio
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from seed_support import replies_for, sources_for
from test_end_to_end import COMPLETIONS, _router

from fin_research import paths
from fin_research.agents.base import RunContext
from fin_research.config import read
from fin_research.core.state import State
from fin_research.core.supervisor import run_deep_dive
from fin_research.llm.client import ModelClient
from fin_research.logging import configure_logging


async def main() -> None:
    """Run one company with the expensive tier gated, and wait for a human."""
    configure_logging('INFO')
    cfg = read(paths.PROJECT_ROOT / 'fin_research.toml')
    cfg = replace(cfg, llm=replace(cfg.llm, gate_tiers=('expensive',), gate_timeout_seconds=600.0))

    sources = sources_for('aapl_tech')
    replies = replies_for(sources)

    with State(cfg.state_path) as state, respx.mock:
        respx.post(COMPLETIONS).mock(side_effect=_router(replies))
        run_id = state.start_run('NVDA', None, date.today(), budget_usd=5.0, config={'demo': True})
        print(f'run {run_id} started; it will stop at each expensive step')

        async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='demo') as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                             ticker='NVDA', as_of=date.today())
            outcome = await run_deep_dive(ctx, sources, price=220.0)

        print(f'finished {outcome.status}, spent ${state.run(run_id).spend_usd:.4f}')


if __name__ == '__main__':
    asyncio.run(main())
