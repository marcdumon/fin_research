"""Command line for the ingestion layer: `python -m fin_research.ingest <command>`.

No language model is involved in anything here. That is deliberate -- the filings, their sections and their
normalised numbers are useful on their own, and being able to build and inspect them without spending a cent
is what makes the rest of the system debuggable.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from fin_research import paths
from fin_research.config import read
from fin_research.core.evaluation import score
from fin_research.ingest import store as store_module
from fin_research.ingest.documents import fetch_primary
from fin_research.ingest.edgar import ANNUAL_FORMS, EdgarClient
from fin_research.ingest.facts import as_of, fetch_facts, periods
from fin_research.ingest.identities import Status, check_filer
from fin_research.ingest.pipeline import ingest_company
from fin_research.ingest.sections import split
from fin_research.logging import configure_logging

log = logging.getLogger('fin_research.ingest')


async def _init(args: argparse.Namespace) -> int:
    """Create the store and report whether the market database could be attached."""
    cfg = read(args.config)
    with store_module.connect(cfg) as con:
        attached = store_module.attach_market_data(con, cfg.investalyze_db) if cfg.investalyze_db else False
        print(f'store        : {cfg.db_path}')
        for table in ('filings', 'documents', 'sections', 'facts'):
            print(f'  {table:<11}: {store_module.count_rows(con, table):>8,} rows')
        if attached:
            tickers = store_module.scalar(con, f'SELECT count(*) FROM {store_module.MARKET_ALIAS}.companies')
            print(f'market data  : attached read-only, {tickers:,} companies')
        else:
            print('market data  : not attached')
    return 0


async def _ingest(args: argparse.Namespace) -> int:
    """Fetch, split and normalise everything needed to analyse one company."""
    cfg = read(args.config)
    counts = await ingest_company(cfg, args.ticker, years=args.years,
                                  statements=args.statements, news=args.news)
    print(f'{args.ticker}: stored ' + ', '.join(f'{count:,} {table}' for table, count in counts.items()))
    return 0


async def _verify(args: argparse.Namespace) -> int:
    """Check that a company's reported statements add up, which is how the metric mapping is tested."""
    cfg = read(args.config)
    async with EdgarClient(cfg) as client:
        cik = await client.resolve_filer(args.ticker)
        facts = as_of(await fetch_facts(client, cik))

    report = check_filer(facts, limit=args.periods)
    for result in report.results:
        mark = {Status.PASS: 'ok  ', Status.FAIL: 'FAIL', Status.SKIPPED: 'skip'}[result.status]
        print(f'  {mark} {result.period_end} {result.name:14} {result.detail}')
    print(f'\n{args.ticker}: {report.passed} passed, {len(report.failed)} failed, {report.skipped} skipped '
          f'across {len(periods(facts))} annual periods')
    print('verdict: the metric mapping is '
          + ('confirmed for this filer' if report.trustworthy else 'NOT confirmed -- treat its numbers as unverified'))
    return 0 if report.trustworthy else 1


async def _show(args: argparse.Namespace) -> int:
    """Summarise what is stored for one company."""
    cfg = read(args.config)
    with store_module.connect(cfg, read_only=True) as con:
        rows = con.execute("""
            SELECT f.form, f.filed, count(s.section_id) AS sections,
                   max(s.confidence) AS confidence, sum(s.n_chars) AS chars
            FROM filings f LEFT JOIN sections s USING (accession)
            WHERE f.ticker = ? GROUP BY f.form, f.filed ORDER BY f.filed DESC
        """, [args.ticker]).fetchall()
        if not rows:
            print(f'nothing stored for {args.ticker}')
            return 1
        print(f'{"form":10} {"filed":12} {"sections":>8} {"confidence":>10} {"chars":>12}')
        for form, filed, sections, confidence, chars in rows:
            print(f'{form:10} {filed!s:12} {sections:>8} '
                  f'{(f"{confidence:.2f}" if confidence is not None else "-"):>10} {(chars or 0):>12,}')
    return 0


async def _score(args: argparse.Namespace) -> int:
    """Measure the parts of the pipeline that need no model, across several companies."""
    cfg = read(args.config)
    failures = 0

    async with EdgarClient(cfg) as client:
        for ticker in args.tickers:
            try:
                cik = await client.resolve_filer(ticker)
                annual = await client.filings(cik, forms=ANNUAL_FORMS, limit=1)
                card = score(ticker, split=split(await fetch_primary(client, annual[0]), form=annual[0].form),
                             facts=await fetch_facts(client, cik))
            except Exception as error:
                print(f'{ticker:6} could not be scored: {type(error).__name__}: {error}')
                failures += 1
                continue

            print(f'{ticker}')
            for metric in card.metrics:
                print(f'   {metric}')
            coverage = card.get('section_coverage')
            passes = card.get('identity_pass_rate')
            if (coverage or 0) < 1.0 or (passes is not None and passes < 1.0):
                failures += 1

    clean = len(args.tickers) - failures
    print(f'\n{clean} of {len(args.tickers)} scored clean')
    return 0 if failures == 0 else 1


COMMANDS = {'init': _init, 'ingest': _ingest, 'verify': _verify, 'show': _show, 'score': _score}

COMMAND_DESCRIPTIONS = {
    'init': 'create the store and check the market database can be attached',
    'ingest': 'fetch filings, split them into sections, and normalise reported metrics',
    'verify': 'check that a filer\'s statements add up, testing the metric mapping',
    'show': 'summarise what is stored for one company',
    'score': 'measure section coverage and statement consistency across companies, without a model',
}


def main() -> int:
    """Parse arguments and run the requested command."""
    # Shared as a parent so the global flags are accepted on either side of the command name, which is where
    # anyone actually types them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', type=Path, default=paths.SETTINGS_FILE)
    common.add_argument('--log-level', default='INFO')

    parser = argparse.ArgumentParser(prog='python -m fin_research.ingest', description=__doc__,
                                     parents=[common])
    subparsers = parser.add_subparsers(dest='command', required=True)

    for name, description in COMMAND_DESCRIPTIONS.items():
        sub = subparsers.add_parser(name, help=description, parents=[common])
        if name in ('ingest', 'verify', 'show'):
            sub.add_argument('--ticker', required=True)
        if name == 'ingest':
            sub.add_argument('--years', type=int, default=5, help='how far back to read annual reports')
            sub.add_argument('--statements', action='store_true', help='also store rendered statement pages')
            sub.add_argument('--news', action='store_true', help='also store material events and press releases')
        if name == 'verify':
            sub.add_argument('--periods', type=int, default=5)
        if name == 'score':
            sub.add_argument('--tickers', nargs='+', required=True,
                             help='the golden set: filers chosen to stress the pipeline, not a portfolio')

    args = parser.parse_args()
    configure_logging(args.log_level)
    # Resolved from the project root rather than searched for: `find_dotenv` walks up from the
    # calling frame's file, which silently finds nothing when the package is installed elsewhere
    # or invoked from another directory.
    load_dotenv(paths.APP_DIR / '.env')
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == '__main__':
    raise SystemExit(main())
