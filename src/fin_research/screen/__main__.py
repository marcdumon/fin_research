"""Command line for screening: `python -m fin_research.screen <command>`.

No model is involved. This is the cheap first pass that turns thousands of companies into tens, using only
figures that were public on the screening date, so it costs nothing and can be re-run for any past date to
see what the screen would have said at the time.
"""

import argparse
import asyncio
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from fin_research import paths
from fin_research.config import read
from fin_research.ingest import store as store_module
from fin_research.logging import configure_logging
from fin_research.screen import factors, rules

log = logging.getLogger('fin_research.screen')

DISPLAY = ['Ticker', 'name', 'sector', 'market_cap', 'earnings_yield', 'fcf_yield', 'roe',
           'gross_margin', 'revenue_growth', 'momentum', 'score']


def _connect(cfg):
    """Open the store with the market data attached, or explain why it could not be."""
    con = store_module.connect(cfg, read_only=True)
    if not cfg.investalyze_db or not store_module.attach_market_data(con, cfg.investalyze_db):
        con.close()
        raise SystemExit('screening needs the market database; check investalyze.db_path in fin_research.toml')
    return con


def _render(frame: pd.DataFrame) -> str:
    """Format results for a terminal."""
    columns = [c for c in DISPLAY if c in frame.columns]
    shown = frame[columns].copy()
    if 'market_cap' in shown:
        shown['market_cap'] = (shown['market_cap'] / 1e9).round(2)
        shown = shown.rename(columns={'market_cap': 'mcap_bn'})
    if 'name' in shown:
        shown['name'] = shown['name'].astype(str).str.slice(0, 26)
    return shown.to_string(index=False, float_format=lambda v: f'{v:,.3f}')


async def _run(args: argparse.Namespace) -> int:
    """Run one screen and print the survivors."""
    cfg = read(args.config)
    screens = rules.load(args.screens)
    if args.screen not in screens:
        print(f'unknown screen {args.screen!r}. Available: {", ".join(screens)}')
        return 1

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    screen = screens[args.screen]

    con = _connect(cfg)
    try:
        table = factors.compute(con, as_of)
    finally:
        con.close()

    result = rules.apply(screen, table)
    print(screen.explain())
    if screen.notes:
        print(f'  {screen.notes.strip()}')
    print()
    print(result.summary())
    print()
    print(_render(result.survivors) if len(result.survivors) else 'nothing survived')

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        result.survivors.to_csv(args.out, index=False)
        print(f'\n{args.out}')
    return 0


async def _list(args: argparse.Namespace) -> int:
    """Show the screens that are defined, and the factors they can use."""
    for screen in rules.load(args.screens).values():
        print(screen.explain())
        print()
    print('factors available:')
    for name, description in factors.FACTOR_DESCRIPTIONS.items():
        print(f'  {name:20} {description}')
    return 0


async def _funnel(args: argparse.Namespace) -> int:
    """Screen, then read each survivor and decide which earn a deep dive. This one costs money."""
    from dotenv import load_dotenv

    from fin_research.agents.base import RunContext
    from fin_research.core.state import State
    from fin_research.ingest.edgar import EdgarClient
    from fin_research.llm.client import ModelClient
    from fin_research.screen.funnel import run_funnel

    # Resolved from the project root rather than searched for: `find_dotenv` walks up from the
    # calling frame's file, which silently finds nothing when the package is installed elsewhere
    # or invoked from another directory.
    load_dotenv(paths.APP_DIR / '.env')
    cfg = read(args.config)
    screens = rules.load(args.screens)
    if args.screen not in screens:
        print(f'unknown screen {args.screen!r}. Available: {", ".join(screens)}')
        return 1

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    screen = screens[args.screen]

    con = _connect(cfg)
    try:
        table = factors.compute(con, as_of)
    finally:
        con.close()

    result = rules.apply(screen, table)
    print(screen.explain())
    print()
    print(result.summary())
    if not len(result.survivors):
        return 1

    print(f'\nreading up to {args.limit or len(result.survivors)} survivors, '
          f'budget ${args.budget:.2f}...\n')

    with State(cfg.state_path) as state:
        run_id = state.start_run(f'screen:{args.screen}', None, as_of, args.budget,
                                 {'screen': args.screen})
        async with ModelClient(cfg=cfg, state=state, run_id=run_id) as model, EdgarClient(cfg) as edgar:
            ctx = RunContext(cfg=cfg, state=state, client=model, run_id=run_id,
                             ticker=args.screen, as_of=as_of)
            outcome = await run_funnel(ctx, result, edgar, as_of, limit=args.limit)
        state.finish_run(run_id, 'done')

    print(outcome.summary())
    print()
    for candidate in outcome.candidates:
        if candidate.skipped:
            print(f'  {candidate.ticker:6} skipped: {candidate.skipped}')
            continue
        verdict = candidate.verdict
        mark = 'ESCALATE' if candidate.escalated else verdict.verdict
        print(f'  {candidate.ticker:6} {mark:9} {verdict.rationale[:96]}')
        for question in verdict.open_questions[:2]:
            print(f'         ? {question[:96]}')

    if outcome.escalated:
        tickers = " ".join(c.ticker for c in outcome.escalated)
        print(f'\nWorth a deep dive: {tickers}')
        print(f'  python -m fin_research.ingest ingest --ticker {outcome.escalated[0].ticker} '
              f'--statements --news')
        print(f'  python -m fin_research.agents run --ticker {outcome.escalated[0].ticker} --budget 2.00')
    return 0


COMMANDS = {'run': _run, 'list': _list, 'funnel': _funnel}

COMMAND_DESCRIPTIONS = {
    'run': 'run one screen and print the companies that survive it',
    'list': 'show the screens defined, and the factors they can use',
    'funnel': 'screen, then read each survivor and decide which earn a deep dive (costs money)',
}


def main() -> int:
    """Parse arguments and run the requested command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', type=Path, default=paths.SETTINGS_FILE)
    common.add_argument('--screens', type=Path, default=paths.SCREENS_FILE)
    common.add_argument('--log-level', default='WARNING')

    parser = argparse.ArgumentParser(prog='python -m fin_research.screen', description=__doc__, parents=[common])
    subparsers = parser.add_subparsers(dest='command', required=True)

    for name, description in COMMAND_DESCRIPTIONS.items():
        sub = subparsers.add_parser(name, help=description, parents=[common])
        if name in ('run', 'funnel'):
            sub.add_argument('--screen', required=True)
            sub.add_argument('--as-of', help='screen as the world looked on this date')
        if name == 'run':
            sub.add_argument('--out', type=Path, help='also write the survivors to a CSV')
        if name == 'funnel':
            sub.add_argument('--budget', type=float, default=1.0, help='hard ceiling in dollars')
            sub.add_argument('--limit', type=int, help='read only the top N survivors')

    args = parser.parse_args()
    configure_logging(args.log_level)
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == '__main__':
    raise SystemExit(main())
