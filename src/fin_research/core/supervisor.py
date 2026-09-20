"""Orchestration: a fixed route through the agents, with verification between the stages.

The route is static. For a single-company deep dive the sequence is known in advance, and letting a model
decide what to do next buys nothing while introducing exactly the drift the rest of this design works to
avoid. The `tasks` table already models dynamic dispatch, so nothing here prevents it later; it is simply not
needed now.

Stage one fans out, because four agents reading four different documents have nothing to say to each other and
waiting for them in sequence wastes minutes. The debate does not fan out: each side must read the other, which
is the entire point of it.

Budget failure is a normal outcome, not a crash. A run that hits its ceiling is closed cleanly with whatever
it produced, because a partial set of verified findings is worth more than an exception, and the prompt cache
means resuming after raising the budget costs almost nothing.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

import duckdb

from fin_research.agents import AGENTS
from fin_research.agents.base import AgentResult, RunContext
from fin_research.agents.debate import thesis_digest
from fin_research.core.approvals import Rejected, gate
from fin_research.core.schemas import Finding, Report, Thesis, Valuation
from fin_research.core.state import BudgetExceeded, State
from fin_research.core.verification import FactIndex
from fin_research.ingest import store as store_module
from fin_research.ingest.store import MARKET_ALIAS
from fin_research.llm.router import resolve

log = logging.getLogger(__name__)

# Sections each reading agent is pointed at.
SECTION_FOR_AGENT = {'business': '1', 'risk_factors': '1A', 'management_view': '7'}

STAGE_ONE = ('business', 'risk_factors', 'management_view', 'events')


@dataclass
class Sources:
    """The documents a run will read, already loaded and registered for citation."""

    sections: dict[str, tuple[str, str]] = field(default_factory=dict)
    statements: tuple[str, str] | None = None
    events: tuple[str, str] | None = None

    @property
    def is_empty(self) -> bool:
        """True if nothing was found to read."""
        return not self.sections and self.statements is None and self.events is None


def load_sources(con: duckdb.DuckDBPyConnection, ticker: str, as_of: date) -> Sources:
    """Gather the most recent readable material for a company, as of a date.

    Everything is filtered on the filing date rather than the period it describes. A run dated 2023 must not
    read a filing published in 2025, or the research it produces is judging the past with the future's
    knowledge.
    """
    sources = Sources()

    latest = con.execute("""
        SELECT accession FROM filings
        WHERE ticker = ? AND form IN ('10-K', '10-K/A') AND filed <= ?
        ORDER BY filed DESC LIMIT 1
    """, [ticker, as_of]).fetchone()

    if latest:
        accession = latest[0]
        for item_key in set(SECTION_FOR_AGENT.values()):
            row = con.execute("""
                SELECT s.document_id, substr(d.text, s.char_start + 1, s.char_end - s.char_start)
                FROM sections s JOIN documents d USING (document_id)
                WHERE s.accession = ? AND s.item_key = ? AND NOT s.is_redirect
            """, [accession, item_key]).fetchone()
            if row:
                sources.sections[item_key] = (row[0], row[1])

        statements = con.execute("""
            SELECT document_id, text FROM documents
            WHERE accession = ? AND doc_role = 'statements' ORDER BY filename
        """, [accession]).fetchall()
        if statements:
            # One combined source, so a claim can cite across the statements it reconciles.
            sources.statements = (statements[0][0], '\n\n'.join(text for _, text in statements))

    press = con.execute("""
        SELECT d.document_id, d.text FROM documents d JOIN filings f USING (accession)
        WHERE f.ticker = ? AND d.doc_role = 'press_release' AND d.filed <= ?
        ORDER BY d.filed DESC LIMIT 1
    """, [ticker, as_of]).fetchone()
    if press:
        sources.events = (press[0], press[1])

    return sources


def load_facts(con: duckdb.DuckDBPyConnection, ticker: str, as_of: date) -> FactIndex:
    """Every metric the company had reported by `as_of`, indexed for checking agents' figures.

    Both annual and quarterly periods are loaded. An earlier version took only annual figures, and rejected a
    press release correctly quoting quarterly revenue because it did not match any full-year number -- a
    false rejection, and the worse kind of error here, since it discards work that was right.

    Filtered on the filing date like everything else: a run dated 2023 must not verify a claim against a
    figure published in 2025, which would quietly turn a look-ahead error into a passing check.
    """
    rows = con.execute("""
        SELECT metric, value FROM facts
        WHERE ticker = ? AND filed <= ? AND metric IS NOT NULL
    """, [ticker, as_of]).fetchall()

    from dataclasses import make_dataclass
    row = make_dataclass('FactRow', ['metric', 'value'])
    return FactIndex.from_facts([row(metric, value) for metric, value in rows])


def latest_price(con: duckdb.DuckDBPyConnection, ticker: str, as_of: date) -> float | None:
    """Most recent close on or before `as_of`, from the attached market data, or None."""
    try:
        row = con.execute(f"""
            SELECT C FROM {MARKET_ALIAS}.prices WHERE Ticker = ? AND Date <= ?
            ORDER BY Date DESC LIMIT 1
        """, [ticker, as_of]).fetchone()
    except duckdb.Error as error:
        log.debug(f'no price for {ticker}: {error}')
        return None
    return float(row[0]) if row else None


@dataclass
class DeepDive:
    """What a run produced, whether or not it reached the end."""

    run_id: str
    ticker: str
    status: str
    findings: list[Finding] = field(default_factory=list)
    bull: Thesis | None = None
    bear: Thesis | None = None
    rebuttal: Thesis | None = None
    valuation: Valuation | None = None
    report: Report | None = None
    results: list[AgentResult] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        """What the agents spent."""
        return sum(r.cost_usd for r in self.results)

    @property
    def groundedness(self) -> float | None:
        """Share of checkable claims across the whole run whose citations resolved."""
        checkable = [v for r in self.results for v in r.verification.checkable]
        if not checkable:
            return None
        return sum(1 for v in checkable if v.grounded) / len(checkable)


def _gate_prompt(ctx: RunContext, name: str) -> str:
    """What the dashboard shows when asking whether to run an agent."""
    choice = resolve(ctx.cfg, name)
    summary = ctx.state.run(ctx.run_id)
    spent = f'${summary.spend_usd:.4f} of ${summary.budget_usd:.2f}' if summary else 'unknown'
    return (f'Run {name} for {ctx.ticker} on {choice.model} ({choice.tier})? '
            f'Spent so far: {spent}.')


async def _run_agent(name: str, ctx: RunContext, stage: int, semaphore: asyncio.Semaphore,
                     **inputs: object) -> AgentResult | None:
    """Run one agent as a tracked task, returning None if it failed for a reason worth continuing past."""
    async with semaphore:
        task_id = ctx.state.start_task(ctx.run_id, name, stage)

        if ctx.cfg.llm.needs_approval(name):
            try:
                await gate(ctx.state, ctx.run_id, subject='agent', prompt=_gate_prompt(ctx, name),
                           task_id=task_id, timeout=ctx.cfg.llm.gate_timeout_seconds)
            except Rejected as refusal:
                log.info(f'{name} not run: {refusal}')
                ctx.state.finish_task(task_id, 'skipped', str(refusal))
                return None

        try:
            result = await AGENTS[name].run(ctx, task_id=task_id, **inputs)
        except BudgetExceeded:
            ctx.state.finish_task(task_id, 'skipped', 'budget exhausted')
            raise
        except Exception as error:
            log.warning(f'{name} failed: {type(error).__name__}: {error}')
            ctx.state.finish_task(task_id, 'failed', str(error))
            return None

        ctx.state.finish_task(task_id, 'done')
        ctx.state.save_artifact(ctx.run_id, name, AGENTS[name].output_type,
                                result.artifact.model_dump(mode='json'), task_id)
        return result


async def run_deep_dive(ctx: RunContext, sources: Sources, price: float | None = None) -> DeepDive:
    """Take one company through the full route and return whatever it produced.

    Ordered so the cheap, checkable work happens first: by the time an expensive model is called, everything
    it reads has already survived verification.
    """
    outcome = DeepDive(run_id=ctx.run_id, ticker=ctx.ticker, status='running')
    semaphore = asyncio.Semaphore(ctx.cfg.llm.max_concurrency)

    for document_id, text in list(sources.sections.values()) + [
            s for s in (sources.statements, sources.events) if s is not None]:
        ctx.add_source(document_id, text)

    try:
        # Stage one: independent readers, in parallel.
        jobs = []
        for name in STAGE_ONE:
            if name == 'events' and sources.events:
                document_id, text = sources.events
                jobs.append(_run_agent(name, ctx, 1, semaphore, document_id=document_id, text=text,
                                       label='a material event announcement'))
            elif name in SECTION_FOR_AGENT and SECTION_FOR_AGENT[name] in sources.sections:
                document_id, text = sources.sections[SECTION_FOR_AGENT[name]]
                jobs.append(_run_agent(name, ctx, 1, semaphore, document_id=document_id, text=text,
                                       label=f'Item {SECTION_FOR_AGENT[name]} of the annual report'))

        for result in await asyncio.gather(*jobs):
            if result is not None:
                outcome.results.append(result)
                outcome.findings.extend(getattr(result.artifact, 'findings', []))

        # Stage two: the reported statements, which arrive already reconciled.
        if sources.statements is not None:
            document_id, text = sources.statements
            result = await _run_agent('financials', ctx, 2, semaphore, document_id=document_id,
                                      statements=text, identities='checked and consistent')
            if result is not None:
                outcome.results.append(result)
                outcome.findings.extend(getattr(result.artifact, 'findings', []))

        # Stages three to five: the debate, fixed at three calls, each side reading the other.
        bull = await _run_agent('bull', ctx, 3, semaphore, findings=outcome.findings)
        outcome.bull = bull.artifact if bull else None
        if bull:
            outcome.results.append(bull)

        bear = await _run_agent('bear', ctx, 4, semaphore, findings=outcome.findings,
                                opposing=thesis_digest(outcome.bull))
        outcome.bear = bear.artifact if bear else None
        if bear:
            outcome.results.append(bear)

        rebuttal = await _run_agent('bull', ctx, 5, semaphore, findings=outcome.findings,
                                    opposing=thesis_digest(outcome.bear))
        outcome.rebuttal = rebuttal.artifact if rebuttal else None
        if rebuttal:
            outcome.results.append(rebuttal)

        valued = await _run_agent('valuation', ctx, 5, semaphore, findings=outcome.findings, price=price)
        outcome.valuation = valued.artifact if valued else None
        if valued:
            outcome.results.append(valued)

        # Stage six: the note a human reads.
        written = await _run_agent('synthesis', ctx, 6, semaphore, findings=outcome.findings,
                                   bull=outcome.bull, bear=outcome.bear, rebuttal=outcome.rebuttal,
                                   valuation=outcome.valuation)
        if written is not None:
            outcome.results.append(written)
            outcome.report = written.artifact

        outcome.status = 'done' if outcome.report is not None else 'incomplete'

    except BudgetExceeded as error:
        log.warning(f'run stopped on budget: {error}')
        outcome.status = 'budget_exceeded'

    ctx.state.finish_run(ctx.run_id, outcome.status)
    return outcome


@dataclass(frozen=True)
class Preparation:
    """Everything a run needs that can be gathered without calling a model."""

    run_id: str
    sources: Sources
    price: float | None
    facts: FactIndex | None = None


def prepare(cfg, state: State, ticker: str, as_of: date, budget_usd: float,
            run_id: str | None = None) -> Preparation:
    """Load everything a run will read, without calling a model.

    Opens a run when not given one. A caller that has to fetch filings first opens it beforehand instead, so
    the run is visible while that happens -- otherwise a browser waiting on the run id sees nothing for a
    minute and concludes the request failed.

    Returns the pieces rather than a half-built context. An earlier version handed back a `RunContext` with
    no client attached and relied on the caller to fill it in, which is a shape that type checks only because
    of a suppression and fails at the first model call.
    """
    with store_module.connect(cfg, read_only=True) as con:
        sources = load_sources(con, ticker, as_of)
        facts = load_facts(con, ticker, as_of)
        price = None
        if cfg.investalyze_db and store_module.attach_market_data(con, cfg.investalyze_db):
            price = latest_price(con, ticker, as_of)

    if run_id is None:
        run_id = state.start_run(ticker, None, as_of, budget_usd, {'agents': cfg.llm.agents})
    return Preparation(run_id=run_id, sources=sources, price=price, facts=facts)
