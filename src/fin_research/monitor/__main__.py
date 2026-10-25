"""Command line for monitoring: `python -m fin_research.monitor <command>`.

No model is involved, so a patrol costs nothing and can run as often as you like. What it produces is a list
of places worth pointing the expensive reading at -- which is the whole division of labour here: cheap
detection, expensive interpretation, and never the other way round.

Scheduling is left to cron or a systemd timer. A long-lived daemon would be one more thing to supervise, and
a patrol is a short job that either ran or did not.
"""

import argparse
import asyncio
import logging
from datetime import date
from pathlib import Path

from fin_research import paths
from fin_research.config import read
from fin_research.ingest import store as store_module
from fin_research.logging import configure_logging
from fin_research.monitor import store as monitor_store
from fin_research.monitor.checks import CHECK_DESCRIPTIONS
from fin_research.monitor.patrol import attach_market, patrol

log = logging.getLogger('fin_research.monitor')


def _open(cfg):
    """Open the store for writing, with market data attached where available."""
    con = store_module.connect(cfg)
    monitor_store.ensure_schema(con)
    attach_market(con, cfg)
    return con


async def _watch(args: argparse.Namespace) -> int:
    """Add companies to the watchlist."""
    cfg = read(args.config)
    with _open(cfg) as con:
        monitor_store.watch(con, args.tickers, args.note or '')
        print(f'watching {len(monitor_store.watchlist(con))}: {", ".join(monitor_store.watchlist(con))}')
    return 0


async def _unwatch(args: argparse.Namespace) -> int:
    """Stop patrolling companies."""
    cfg = read(args.config)
    with _open(cfg) as con:
        monitor_store.unwatch(con, args.tickers)
        remaining = monitor_store.watchlist(con)
        print(f'watching {len(remaining)}: {", ".join(remaining) or "nothing"}')
    return 0


async def _patrol(args: argparse.Namespace) -> int:
    """Check every watched company and report what changed."""
    cfg = read(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    with _open(cfg) as con:
        if not monitor_store.watchlist(con) and not args.tickers:
            print('nothing is being watched. Add something: '
                  'python -m fin_research.monitor watch --tickers AAPL KO')
            return 1

        result = await patrol(cfg, con, as_of, tickers=args.tickers,
                              screens_path=args.screens if args.screens.exists() else None,
                              only=tuple(args.only) if args.only else None)
        print(result.summary())

        if result.alerts:
            print()
            for alert in sorted(result.alerts, key=lambda a: (a.severity.value, a.ticker)):
                print(f'  {alert.severity.value.upper():6} {alert.ticker:6} {alert.headline}')
                if alert.detail:
                    print(f'  {"":6} {"":6} {alert.detail[:100]}')
    return 0


async def _alerts(args: argparse.Namespace) -> int:
    """Show alerts nobody has acknowledged."""
    cfg = read(args.config)
    with _open(cfg) as con:
        frame = monitor_store.open_alerts(con, args.ticker, args.limit)
        if not len(frame):
            print('no open alerts')
            return 0
        for _, row in frame.iterrows():
            print(f"  {row['severity'].upper():6} {row['ticker']:6} {row['as_of']}  {row['headline']}")
            if row['detail']:
                print(f"  {'':6} {'':6}             {str(row['detail'])[:100]}")
    return 0


async def _ack(args: argparse.Namespace) -> int:
    """Mark alerts as seen."""
    cfg = read(args.config)
    with _open(cfg) as con:
        monitor_store.acknowledge(con, args.ticker)
        print(f'acknowledged{" for " + args.ticker if args.ticker else ""}')
    return 0


async def _checks(args: argparse.Namespace) -> int:
    """Describe what a patrol looks for."""
    for name, description in CHECK_DESCRIPTIONS.items():
        print(f'  {name:22} {description}')
    return 0


COMMANDS = {'watch': _watch, 'unwatch': _unwatch, 'patrol': _patrol, 'alerts': _alerts,
            'ack': _ack, 'checks': _checks}

COMMAND_DESCRIPTIONS = {
    'watch': 'add companies to the watchlist',
    'unwatch': 'stop patrolling companies',
    'patrol': 'check every watched company and report what changed',
    'alerts': 'show alerts nobody has acknowledged',
    'ack': 'mark alerts as seen',
    'checks': 'describe what a patrol looks for',
}


def main() -> int:
    """Parse arguments and run the requested command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', type=Path, default=paths.SETTINGS_FILE)
    common.add_argument('--log-level', default='WARNING')

    parser = argparse.ArgumentParser(prog='python -m fin_research.monitor', description=__doc__,
                                     parents=[common])
    subparsers = parser.add_subparsers(dest='command', required=True)

    for name, description in COMMAND_DESCRIPTIONS.items():
        sub = subparsers.add_parser(name, help=description, parents=[common])
        if name in ('watch', 'unwatch'):
            sub.add_argument('--tickers', nargs='+', required=True)
        if name == 'watch':
            sub.add_argument('--note', help='why this company is being watched')
        if name == 'patrol':
            sub.add_argument('--tickers', nargs='+', help='patrol these instead of the watchlist')
            sub.add_argument('--as-of', help='patrol as the world looked on this date')
            sub.add_argument('--screens', type=Path, default=paths.SCREENS_FILE)
            sub.add_argument('--only', nargs='+', choices=list(CHECK_DESCRIPTIONS),
                             help='run only these checks')
        if name in ('alerts', 'ack'):
            sub.add_argument('--ticker')
        if name == 'alerts':
            sub.add_argument('--limit', type=int, default=50)

    args = parser.parse_args()
    configure_logging(args.log_level)
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == '__main__':
    raise SystemExit(main())
