"""The funnel: numbers narrow thousands to tens, a cheap read narrows tens to a handful.

Each stage is cheaper per company than the one after it and removes most of what reaches it, which is the
only reason deep research on a broad universe is affordable at all. Screening is free and reads no filings.
Triage costs a fraction of a cent per company and reads one section. The deep dive, which costs real money,
then runs on the few names that survived both.

Companies are read concurrently, bounded by the SEC's rate limit rather than by the model's: fetching a
filing is the slow part, and the shared throttle in the EDGAR client is what keeps that within the ten
requests a second the SEC allows.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from fin_research.agents import AGENTS
from fin_research.agents.base import RunContext
from fin_research.core.schemas import Triage
from fin_research.core.state import BudgetExceeded
from fin_research.ingest.documents import fetch_primary
from fin_research.ingest.edgar import ORIGINAL_ANNUAL_FORMS, EdgarClient, NoFilingHistory, TickerNotFound
from fin_research.ingest.sections import split
from fin_research.screen.rules import Result

log = logging.getLogger(__name__)

# The section a triage read is based on: the company describing its own business. Risk factors would be the
# obvious addition, but they run four times longer and are mostly boilerplate, which is a poor trade at this
# stage -- the deep dive reads them properly.
TRIAGE_ITEM = '1'

# Factors quoted to the model alongside the filing, so its judgement is anchored to why the screen picked it.
QUOTED_FACTORS = ('market_cap', 'earnings_yield', 'fcf_yield', 'roe', 'gross_margin',
                  'operating_margin', 'revenue_growth', 'momentum', 'net_debt_to_equity')


@dataclass
class Candidate:
    """One company through the funnel, and where it stopped."""

    ticker: str
    name: str
    sector: str
    score: float
    verdict: Triage | None = None
    skipped: str | None = None
    cost_usd: float = 0.0

    @property
    def escalated(self) -> bool:
        """True if this company earned a deep dive."""
        return self.verdict is not None and self.verdict.is_escalated


@dataclass
class FunnelResult:
    """What the funnel produced at every stage."""

    screen_result: Result
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def escalated(self) -> list[Candidate]:
        """Companies worth the expensive reading."""
        return [c for c in self.candidates if c.escalated]

    @property
    def cost_usd(self) -> float:
        """What the triage stage cost."""
        return sum(c.cost_usd for c in self.candidates)

    def summary(self) -> str:
        """The funnel, stage by stage."""
        read = [c for c in self.candidates if c.verdict is not None]
        verdicts = pd.Series([c.verdict.verdict for c in read]).value_counts().to_dict() if read else {}
        skipped = [c for c in self.candidates if c.skipped]
        return '\n'.join([
            self.screen_result.summary(),
            f'  read {len(read)} of {len(self.candidates)} survivors'
            + (f' ({len(skipped)} could not be read)' if skipped else ''),
            '  ' + ', '.join(f'{count} {verdict}' for verdict, count in verdicts.items()),
            f'  = {len(self.escalated)} worth a deep dive, for ${self.cost_usd:.4f}',
        ])


def describe_numbers(row: pd.Series) -> str:
    """The screen's own figures, written out for the model."""
    lines = []
    for factor in QUOTED_FACTORS:
        value = row.get(factor)
        if pd.isna(value):
            continue
        shown = f'${value / 1e9:,.2f}bn' if factor == 'market_cap' else f'{value:,.3f}'
        lines.append(f'  {factor}: {shown}')
    return '\n'.join(lines) or '  none available'


async def _triage_one(candidate: Candidate, row: pd.Series, client: EdgarClient, ctx: RunContext,
                      semaphore: asyncio.Semaphore, as_of: date) -> Candidate:
    """Fetch one company's business description and ask whether it earns a deep dive."""
    async with semaphore:
        try:
            cik = await client.resolve_filer(candidate.ticker)
            # Originals only: an amendment restates just the corrected parts and frequently has no Item 1.
            annual = await client.filings(cik, forms=ORIGINAL_ANNUAL_FORMS, limit=1)
            if not annual:
                candidate.skipped = 'no annual report'
                return candidate
            if annual[0].filed > as_of:
                candidate.skipped = f'latest filing postdates {as_of}'
                return candidate

            document = await fetch_primary(client, annual[0])
            section = split(document, form=annual[0].form).by_item(TRIAGE_ITEM)
            if section is None or section.is_redirect:
                candidate.skipped = f'Item {TRIAGE_ITEM} unavailable'
                return candidate
        except (TickerNotFound, NoFilingHistory) as error:
            candidate.skipped = str(error).split('.')[0]
            return candidate
        except Exception as error:
            log.warning(f'{candidate.ticker}: {type(error).__name__}: {error}')
            candidate.skipped = type(error).__name__
            return candidate

        document_id = f'{candidate.ticker}-item{TRIAGE_ITEM}'
        # Each candidate gets its own context: the ticker differs, and sources must not leak between them or
        # a quote from one company could verify against another's filing.
        local = RunContext(cfg=ctx.cfg, state=ctx.state, client=ctx.client, run_id=ctx.run_id,
                           ticker=candidate.ticker, as_of=as_of)
        local.add_source(document_id, section.text(document.text))

        task_id = ctx.state.start_task(ctx.run_id, 'triage', 0, candidate.ticker)
        try:
            result = await AGENTS['triage'].run(
                local, task_id=task_id, document_id=document_id, text=section.text(document.text),
                numbers=describe_numbers(row), screen_reason=f'the {candidate.sector} screen')
        except BudgetExceeded:
            ctx.state.finish_task(task_id, 'skipped', 'budget exhausted')
            candidate.skipped = 'budget exhausted'
            raise
        except Exception as error:
            ctx.state.finish_task(task_id, 'failed', str(error))
            candidate.skipped = type(error).__name__
            return candidate

        ctx.state.finish_task(task_id, 'done')
        candidate.cost_usd = result.cost_usd
        if isinstance(result.artifact, Triage):
            candidate.verdict = result.artifact
            ctx.state.save_artifact(ctx.run_id, 'triage', 'Triage',
                                    result.artifact.model_dump(mode='json'), task_id)
        return candidate


async def run_funnel(ctx: RunContext, screen_result: Result, client: EdgarClient, as_of: date,
                     limit: int | None = None) -> FunnelResult:
    """Read every screened survivor and decide which ones earn a deep dive."""
    survivors = screen_result.survivors.head(limit) if limit else screen_result.survivors
    outcome = FunnelResult(screen_result=screen_result)

    candidates = [Candidate(ticker=row['Ticker'], name=str(row.get('name', '')),
                            sector=str(row.get('sector', '')), score=float(row.get('score', 0.0)))
                  for _, row in survivors.iterrows()]

    # Bounded by the SEC's limit rather than the model's: fetching the filing dominates the time here.
    semaphore = asyncio.Semaphore(min(ctx.cfg.llm.max_concurrency, 6))
    jobs = [_triage_one(candidate, row, client, ctx, semaphore, as_of)
            for candidate, (_, row) in zip(candidates, survivors.iterrows(), strict=True)]

    try:
        outcome.candidates = list(await asyncio.gather(*jobs))
    except BudgetExceeded:
        log.warning('triage stopped on budget')
        outcome.candidates = candidates

    outcome.candidates.sort(key=lambda c: (not c.escalated, -c.score))
    return outcome
