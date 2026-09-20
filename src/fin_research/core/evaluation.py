"""Measure whether the system is working, using checks that do not require a model to judge them.

'Great analysis' is unmeasurable as stated, so it is replaced here by properties that are not: did the
splitter find the sections an analyst reads, do the filer's own numbers reconcile, did the claims cite
sources that actually say what was claimed, and what did it cost. None of these establishes that a conclusion
is correct -- nothing mechanical could -- but together they rule out the specific ways this kind of system
fails quietly, which is a floor worth having and a regression test worth running.

Deliberately no model-as-judge. Scoring an unverifiable output with another unverifiable output produces a
number that moves for reasons nobody can trace, and it would sit in a report implying more than it knows.
Judging whether a note is any good stays a human job.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from fin_research.core.state import State
from fin_research.ingest.facts import Fact, as_of
from fin_research.ingest.identities import check_filer
from fin_research.ingest.sections import REQUIRED_ITEMS, SplitResult

log = logging.getLogger(__name__)

# Below this a section that is not a pointer is the contents row, not the item.
PLAUSIBLE_ITEM_CHARS = 5_000


@dataclass
class Metric:
    """One measured property, with enough context to act on a regression."""

    name: str
    value: float | None
    detail: str = ''

    def __str__(self) -> str:
        """Render for a terminal, distinguishing 'zero' from 'not measured'."""
        shown = 'n/a' if self.value is None else (f'{self.value:.1%}' if self.value <= 1 else f'{self.value:,.4f}')
        return f'{self.name:22} {shown:>10}  {self.detail}'


@dataclass
class Scorecard:
    """Every metric for one company."""

    ticker: str
    metrics: list[Metric] = field(default_factory=list)

    def add(self, name: str, value: float | None, detail: str = '') -> None:
        """Record one measurement."""
        self.metrics.append(Metric(name=name, value=value, detail=detail))

    def get(self, name: str) -> float | None:
        """One metric's value, or None if it was not measured."""
        return next((m.value for m in self.metrics if m.name == name), None)


def section_coverage(split: SplitResult) -> Metric:
    """Share of the items an analyst reads that came back as real content.

    The metric a naive rubric omits, and the one that catches a splitter regression before it reaches an
    agent. A section satisfied by a pointer counts as covered: the filer genuinely put the content elsewhere,
    which is a fact about the filing rather than a failure of the split.
    """
    found = 0
    missing = []
    for item in REQUIRED_ITEMS:
        section = split.by_item(item)
        if section is None:
            missing.append(item)
        elif section.is_redirect or section.n_chars >= PLAUSIBLE_ITEM_CHARS:
            found += 1
        else:
            missing.append(f'{item}(short)')

    return Metric(name='section_coverage', value=found / len(REQUIRED_ITEMS),
                  detail=f'missing {missing}' if missing else 'all required items present')


def identity_health(facts: list[Fact], periods: int = 5) -> list[Metric]:
    """How well a filer's reported statements reconcile, and how much could be checked at all.

    Both numbers are reported because they answer different questions. A high pass rate over two checks is
    weaker evidence than a high pass rate over thirty, and only reporting the rate would hide that.
    """
    report = check_filer(facts, limit=periods)
    ran = report.passed + len(report.failed)

    return [
        Metric(name='identity_pass_rate', value=(report.passed / ran) if ran else None,
               detail=f'{report.passed} passed, {len(report.failed)} failed of {ran} that ran'),
        Metric(name='identity_coverage', value=(ran / (ran + report.skipped)) if (ran + report.skipped) else None,
               detail=f'{report.skipped} skipped for want of an input'),
    ]


def run_metrics(state: State, run_id: str) -> list[Metric]:
    """What a completed research run achieved and what it cost."""
    summary = state.run(run_id)
    if summary is None:
        return [Metric(name='run', value=None, detail=f'no run {run_id}')]

    calls = state.query(
        'SELECT count(*) AS n, sum(cache_hit) AS cached, sum(prompt_tokens) AS prompt, '
        "sum(completion_tokens) AS completion, sum(CASE WHEN parse_mode = 'failed' THEN 1 ELSE 0 END) "
        'AS unparsed FROM llm_calls WHERE run_id = ?', (run_id,))[0]

    total = calls['n'] or 0
    return [
        Metric(name='cost_per_report', value=summary.spend_usd, detail=f'{total} call(s), status {summary.status}'),
        Metric(name='tokens', value=float((calls['prompt'] or 0) + (calls['completion'] or 0)),
               detail=f"{calls['prompt'] or 0:,} in, {calls['completion'] or 0:,} out"),
        Metric(name='cache_hit_rate', value=(calls['cached'] or 0) / total if total else None,
               detail='replayed at no cost'),
        Metric(name='parse_failure_rate', value=(calls['unparsed'] or 0) / total if total else None,
               detail='replies that never matched their schema'),
    ]


def score(ticker: str, split: SplitResult | None = None, facts: list[Fact] | None = None,
          state: State | None = None, run_id: str | None = None,
          cutoff: date | None = None) -> Scorecard:
    """Assemble every metric available for one company.

    Each input is optional so the pipeline can be measured in pieces: the splitter and the numbers are
    checkable with no model involved at all, which means a regression in either is caught before anything is
    spent.
    """
    card = Scorecard(ticker=ticker)

    if split is not None:
        card.metrics.append(section_coverage(split))
    if facts is not None:
        card.metrics.extend(identity_health(as_of(facts, cutoff)))
    if state is not None and run_id is not None:
        card.metrics.extend(run_metrics(state, run_id))

    return card
