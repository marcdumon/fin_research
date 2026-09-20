"""Command line for research runs: `python -m fin_research.agents <command>`.

Requires `OPENROUTER_API_KEY` in the environment, read here at the edge and nowhere else. Everything the run
reads must already be in the store, so `python -m fin_research.ingest ingest --ticker X` comes first -- the
separation is deliberate, since fetching filings is slow, cacheable and free, while reasoning about them is
none of those things.
"""

import argparse
import asyncio
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from fin_research import paths
from fin_research.agents.base import AGENT_DESCRIPTIONS, RunContext
from fin_research.config import read
from fin_research.core.state import State
from fin_research.core.supervisor import prepare, run_deep_dive
from fin_research.llm.client import ModelClient
from fin_research.logging import configure_logging
from fin_research.report.render import render

log = logging.getLogger('fin_research.agents')


async def _run(args: argparse.Namespace) -> int:
    """Take one company through the full route and write the note."""
    cfg = read(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    if args.gate:
        # Gating means the run waits for a click in the dashboard, so it is opt-in: an unattended job that
        # blocked on a human nobody had told about would just stall.
        cfg = replace(cfg, llm=replace(cfg.llm, gate_tiers=tuple(args.gate)))
        print(f'Gated on {", ".join(args.gate)}. Approve at http://127.0.0.1:8000 '
              f'within {cfg.llm.gate_timeout_seconds:.0f}s per step.')

    with State(cfg.state_path) as state:
        prepared = prepare(cfg, state, args.ticker, as_of, args.budget)
        if prepared.sources.is_empty:
            print(f'nothing stored for {args.ticker}. Run: '
                  f'python -m fin_research.ingest ingest --ticker {args.ticker} --statements --news')
            state.finish_run(prepared.run_id, 'failed', 'no sources')
            return 1

        log.info(f'{args.ticker}: run {prepared.run_id}, budget ${args.budget:.2f}, '
                 f'{len(prepared.sources.sections)} section(s)'
                 + (', statements' if prepared.sources.statements else '')
                 + (', press release' if prepared.sources.events else ''))

        async with ModelClient(cfg=cfg, state=state, run_id=prepared.run_id) as client:
            ctx = RunContext(cfg=cfg, state=state, client=client, run_id=prepared.run_id,
                             ticker=args.ticker, as_of=as_of, facts=prepared.facts)
            outcome = await run_deep_dive(ctx, prepared.sources, prepared.price)

        summary = state.run(prepared.run_id)
        if outcome.report is None:
            print(f'{args.ticker}: run ended {outcome.status} with no report. '
                  f'Spent ${summary.spend_usd:.4f} of ${summary.budget_usd:.2f}.')
            return 1

        markdown = render(outcome.report, ctx.sources, groundedness=outcome.groundedness,
                          cost_usd=summary.spend_usd,
                          match_kinds={k: v for result in outcome.results
                                       for k, v in result.verification.match_kinds().items()},
                          numeric={k: v for result in outcome.results
                                   for k, v in result.verification.numeric_outcomes().items()},
                          valuation=outcome.valuation)

        destination = args.out or paths.PROJECT_ROOT / 'reports' / f'{args.ticker}_{as_of}.md'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(markdown)

        print(f'{args.ticker}: {destination}')
        print(f'  cost ${summary.spend_usd:.4f} over {summary.calls} call(s), status {outcome.status}')
        print(f'  groundedness {outcome.groundedness:.0%}' if outcome.groundedness is not None
              else '  groundedness not measured')
    return 0


async def _cost(args: argparse.Namespace) -> int:
    """Show what a run spent, by agent."""
    cfg = read(args.config)
    with State(cfg.state_path) as state:
        runs = state.query('SELECT run_id, ticker, status, spend_usd, budget_usd, started_at '
                           'FROM runs ORDER BY started_at DESC LIMIT ?', (args.limit,))
        if not runs:
            print('no runs recorded')
            return 1
        for row in runs:
            print(f"{row['started_at']}  {row['ticker']:6} {row['status']:16} "
                  f"${row['spend_usd']:.4f} / ${row['budget_usd']:.2f}  {row['run_id']}")
        print()
        for row in state.cost_by_agent(runs[0]['run_id']):
            print(f"  {row['agent']:16} {row['tier']:9} {row['calls']:>3} call(s) "
                  f"${row['cost_usd'] or 0:.4f}  cached {row['cache_hits'] or 0}")
    return 0


async def _roster(args: argparse.Namespace) -> int:
    """List the agents and the model tier each runs on."""
    cfg = read(args.config)
    from fin_research.agents import AGENTS
    for name, agent in sorted(AGENTS.items(), key=lambda kv: kv[1].stage):
        print(f'  stage {agent.stage}  {name:16} {cfg.llm.tier_for(name):10} '
              f'{cfg.llm.model_for(name):28} {AGENT_DESCRIPTIONS[name]}')
    return 0


COMMANDS = {'run': _run, 'cost': _cost, 'roster': _roster}

COMMAND_DESCRIPTIONS = {
    'run': 'take one company through the full route and write the note',
    'cost': 'show what recent runs spent, by agent',
    'roster': 'list the agents and which model tier each runs on',
}


def main() -> int:
    """Parse arguments and run the requested command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', type=Path, default=paths.PROJECT_ROOT / 'fin_research.toml')
    common.add_argument('--log-level', default='INFO')

    parser = argparse.ArgumentParser(prog='python -m fin_research.agents', description=__doc__, parents=[common])
    subparsers = parser.add_subparsers(dest='command', required=True)

    for name, description in COMMAND_DESCRIPTIONS.items():
        sub = subparsers.add_parser(name, help=description, parents=[common])
        if name == 'run':
            sub.add_argument('--ticker', required=True)
            sub.add_argument('--budget', type=float, default=2.0, help='hard ceiling in dollars')
            sub.add_argument('--as-of', help='only read filings published on or before this date')
            sub.add_argument('--out', type=Path)
            sub.add_argument('--gate', nargs='*', choices=['cheap', 'mid', 'expensive'], default=None,
                             metavar='TIER',
                             help='ask before running agents on these tiers; needs the dashboard open')
        if name == 'cost':
            sub.add_argument('--limit', type=int, default=10)

    args = parser.parse_args()
    configure_logging(args.log_level)
    # Resolved from the project root rather than searched for: `find_dotenv` walks up from the
    # calling frame's file, which silently finds nothing when the package is installed elsewhere
    # or invoked from another directory.
    load_dotenv(paths.PROJECT_ROOT / '.env')
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == '__main__':
    raise SystemExit(main())
