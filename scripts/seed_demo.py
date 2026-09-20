"""Populate the run ledger with realistic traces, without calling a provider.

The dashboard has to be built against something, and until a real key is in place the honest substitute is
the offline harness: it drives the actual pipeline -- all nine agents, real verification, real cost
arithmetic -- with recorded replies, so the rows it writes have the same shape as rows from a paid run.

Safe to re-run. Delete `data/state.sqlite` to start clean.
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))

from conftest import filing_html
from test_end_to_end import _finding_set, _quote, _report, _router, _thesis, _valuation

from fin_research import paths
from fin_research.agents.base import RunContext
from fin_research.config import read
from fin_research.core.state import State
from fin_research.core.supervisor import Sources, run_deep_dive
from fin_research.ingest.sections import split
from fin_research.ingest.text import clean
from fin_research.llm.client import ModelClient
from fin_research.logging import configure_logging
from fin_research.report.render import render

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'

# Each demo run uses a different filing, so the dashboard shows genuinely different costs and section sizes.
COMPANIES = [('AAPL', 'aapl_tech', 5.0), ('JPM', 'jpm_bank', 5.0), ('KO', 'ko_consumer', 0.00002)]


def sources_for(fixture: str) -> tuple[Sources, dict]:
    """Real sections of a real filing, plus replies that quote them."""
    document = clean(filing_html(fixture))
    result = split(document)
    items = {i: (result.by_item(i).text(document.text) if result.by_item(i) else '') for i in ('1', '1A', '7')}
    present = {i: t for i, t in items.items() if len(t) > 5_000}

    replies = {
        'business': _finding_set('doc-1', [_quote(present.get('1', ''), 500)]) if '1' in present else '{}',
        'risk_factors': _finding_set('doc-1A', [_quote(present['1A'], 800)], invent=True) if '1A' in present else '{}',
        'management_view': _finding_set('doc-7', [_quote(present['7'], 1200)]) if '7' in present else '{}',
        'financials': _finding_set('doc-fin', [_quote(next(iter(present.values())), 2500)]),
        'bull': _thesis('bull'), 'bear': _thesis('bear'), 'valuation': _valuation(),
        'synthesis': _report('doc-1', _quote(next(iter(present.values())), 500)),
    }
    sources = Sources(sections={k: (f'doc-{k}', v) for k, v in present.items()},
                      statements=('doc-fin', next(iter(present.values()))[:5_000]))
    return sources, replies


async def main() -> None:
    """Drive three runs with different outcomes: two complete, one that hits its budget."""
    configure_logging('WARNING')
    cfg = read(paths.PROJECT_ROOT / 'fin_research.toml')
    cfg.data_root.mkdir(parents=True, exist_ok=True)

    with State(cfg.state_path) as state, respx.mock:
        respx.post(COMPLETIONS).mock(side_effect=lambda request: _router(current['replies'])(request))
        current: dict = {}

        for offset, (ticker, fixture, budget) in enumerate(COMPANIES):
            sources, replies = sources_for(fixture)
            current['replies'] = replies
            as_of = date.today() - timedelta(days=offset)

            run_id = state.start_run(ticker, None, as_of, budget_usd=budget, config={'demo': True})
            async with ModelClient(cfg=cfg, state=state, run_id=run_id, api_key='demo') as client:
                ctx = RunContext(cfg=cfg, state=state, client=client, run_id=run_id,
                                 ticker=ticker, as_of=as_of)
                outcome = await run_deep_dive(ctx, sources, price=220.0)

            summary = state.run(run_id)
            print(f'{ticker:5} {outcome.status:16} ${summary.spend_usd:.4f} over {summary.calls} call(s), '
                  f'groundedness '
                  + (f'{outcome.groundedness:.0%}' if outcome.groundedness is not None else 'n/a'))

            if outcome.report is not None:
                state.request_approval(run_id, 'spend', f'Approve the expensive synthesis step for {ticker}?')
                destination = paths.PROJECT_ROOT / 'reports' / f'{ticker}_{as_of}.md'
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(render(outcome.report, ctx.sources, outcome.groundedness,
                                              summary.spend_usd))

        print(f'\nledger: {cfg.state_path}')
        print(f'runs  : {len(state.query("SELECT 1 FROM runs", ()))}')


if __name__ == '__main__':
    asyncio.run(main())
