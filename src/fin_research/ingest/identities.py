"""Check that a filer's reported numbers add up, as a test of the concept mapping rather than of the filer.

The canonical metric map is 126 hand-written concept assignments carrying no tests in the project it came
from. Trusting it silently would mean every downstream number inherits an unverified assumption. Accounting
identities are the available check: the statements are required to articulate, so if revenue minus cost of
revenue does not equal reported gross profit, the mapping is wrong far more often than the filer is.

The result is three-valued on purpose. A check that quietly skips when an input is missing reports success it
did not earn, and in a system whose whole claim is mechanical verification that is worse than no check at all.
So a missing input yields `SKIPPED` naming what was absent, and callers are expected to treat 'everything was
skipped' as failure. Two identities genuinely do not apply to some filers -- a bank reports no cost of revenue
at all -- and distinguishing 'does not apply' from 'does not balance' is exactly the point.
"""

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from fin_research.ingest.facts import (
    CONTINUING_OPERATIONS_SUFFIX,
    DISCONTINUED_CASH_FLOW,
    FX_EFFECT_ON_CASH,
    MEZZANINE_EQUITY,
    NET_CASH_CHANGE_EXCLUDING_FX,
    NET_CASH_CHANGE_INCLUDING_FX,
    Fact,
    by_period,
    find_all_tags,
    find_tag,
)

log = logging.getLogger(__name__)

# Filings report exact dollars but round their components, so an exact match is the wrong bar. The floor is
# deliberately small: it only needs to absorb rounding, and a generous one would let a small filer's real
# discrepancy pass unnoticed -- a percentage of a micro-cap's balance sheet is a rounding error on a bank's.
RELATIVE_TOLERANCE = 0.005
ABSOLUTE_TOLERANCE = 5_000.0


class Status(StrEnum):
    """Outcome of one identity check."""

    PASS = 'pass'
    FAIL = 'fail'
    SKIPPED = 'skipped'


@dataclass(frozen=True)
class IdentityResult:
    """What one accounting identity said about one period."""

    name: str
    status: Status
    period_end: date
    detail: str
    missing: tuple[str, ...] = ()
    left: float | None = None
    right: float | None = None

    @property
    def difference(self) -> float | None:
        """Absolute gap between the two sides, or None when the check could not run."""
        if self.left is None or self.right is None:
            return None
        return self.left - self.right


def within_tolerance(left: float, right: float) -> bool:
    """True if two reported sums agree to within filing rounding."""
    return abs(left - right) <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * max(abs(left), abs(right)))


def _result(name: str, period_end: date, left: float, right: float, expression: str) -> IdentityResult:
    """Compare the two sides of an identity and describe the outcome."""
    ok = within_tolerance(left, right)
    gap = left - right
    return IdentityResult(
        name=name, status=Status.PASS if ok else Status.FAIL, period_end=period_end,
        detail=f'{expression}: {left:,.0f} vs {right:,.0f} (difference {gap:,.0f})',
        left=left, right=right)


def _best_match(name: str, period_end: date, left: float, candidates: list[float], expression: str) -> IdentityResult:
    """Compare one computed sum against several reported totals, accepting any that agrees.

    Filers disagree on which concept carries the bottom line of the cash flow statement. One health insurer
    reports its true total under the discontinued-operations variant while a retailer tags that same concept
    as zero and puts the total elsewhere -- so there is no single correct concept to compare against. Since
    this check exists to validate the concept mapping rather than to choose a canonical total, reconciling to
    any reported total is sufficient evidence that the sections are mapped correctly.
    """
    closest = min(candidates, key=lambda value: abs(left - value))
    return _result(name, period_end, left, closest, expression)


def _skipped(name: str, period_end: date, missing: list[str]) -> IdentityResult:
    """Record that an identity could not be evaluated, and exactly what was absent."""
    return IdentityResult(name=name, status=Status.SKIPPED, period_end=period_end,
                          detail=f'not reported: {", ".join(missing)}', missing=tuple(missing))


def check_balance_sheet(facts: list[Fact], period_end: date, period_type: str = 'A') -> IdentityResult:
    """Total assets must equal total liabilities plus equity.

    Requires `Total Liabilities` to be tagged in its own right. Some filers tag only
    `LiabilitiesAndStockholdersEquity`, which is assets by definition and would make this check pass without
    testing anything -- the anchor table does not map that concept, so those filers land in SKIPPED.

    Redeemable interests are added where present. They sit in the mezzanine, belonging to neither side, so a
    filer carrying them balances only when they are counted separately -- for one large insurer that is a
    1.6bn discrepancy that looks exactly like a mapping error and is not.
    """
    period = by_period(facts, period_end, period_type)
    needed = ('Total Assets', 'Total Liabilities', 'Stockholders Equity')
    missing = [name for name in needed if name not in period]
    if missing:
        return _skipped('balance_sheet', period_end, missing)

    mezzanine = find_tag(facts, MEZZANINE_EQUITY, period_end, period_type)
    claims = period['Total Liabilities'].value + period['Stockholders Equity'].value
    expression = 'assets vs liabilities + equity'
    if mezzanine is not None:
        claims += mezzanine.value
        expression = 'assets vs liabilities + redeemable interests + equity'

    return _result('balance_sheet', period_end, period['Total Assets'].value, claims, expression)


def check_cash_flow(facts: list[Fact], period_end: date, period_type: str = 'A') -> IdentityResult:
    """The three cash flow sections, plus currency effects where they belong, must equal the reported change in cash.

    This is the identity that proves the sign convention. Outflows are stored negative, so the sections add
    rather than subtract; if capital expenditure were stored as filed -- a positive magnitude -- investing
    cash flow would not reconcile, and the error would surface here rather than inside an agent's reasoning.

    Whether the currency effect belongs in the sum depends on which total the filer reported, so that is
    resolved first. Which concept carries the effect is a separate question: filers mix bases, reporting the
    change in cash including restricted cash while reporting the effect on plain cash, so every known concept
    is searched rather than only the one matching the total.
    """
    period = by_period(facts, period_end, period_type)
    sections = ('Operating Cash Flow', 'Investing Cash Flow', 'Financing Cash Flow')
    missing = [name for name in sections if name not in period]

    including = find_tag(facts, NET_CASH_CHANGE_INCLUDING_FX, period_end, period_type)
    excluding = find_tag(facts, NET_CASH_CHANGE_EXCLUDING_FX, period_end, period_type)
    if including is None and excluding is None:
        missing.append('reported change in cash')
    if missing:
        return _skipped('cash_flow', period_end, missing)

    flows = 0.0
    for name in sections:
        fact = period[name]
        flows += fact.value
        # A section reported as continuing operations only leaves the discontinued portion tagged elsewhere.
        if fact.tag.endswith(CONTINUING_OPERATIONS_SUFFIX):
            discontinued = find_tag(facts, (DISCONTINUED_CASH_FLOW[name],), period_end, period_type)
            if discontinued is not None:
                flows += discontinued.value

    if including is not None:
        effect = find_tag(facts, FX_EFFECT_ON_CASH, period_end, period_type)
        # An untagged currency effect means the filer reported none, which is materially zero.
        flows += effect.value if effect else 0.0
        reported = [f.value for f in find_all_tags(facts, NET_CASH_CHANGE_INCLUDING_FX, period_end, period_type)]
        expression = 'operating + investing + financing + currency effect vs change in cash'
    else:
        reported = [excluding.value]
        expression = 'operating + investing + financing vs change in cash'

    return _best_match('cash_flow', period_end, flows, reported, expression)


def check_gross_profit(facts: list[Fact], period_end: date, period_type: str = 'A') -> IdentityResult:
    """Revenue less cost of revenue must equal reported gross profit.

    Banks and insurers report no cost of revenue at all, so for them this is SKIPPED rather than FAIL. That
    distinction is why the result type has three values.
    """
    period = by_period(facts, period_end, period_type)
    needed = ('Revenue', 'Cost of Revenue', 'Gross Profit')
    missing = [name for name in needed if name not in period]
    if missing:
        return _skipped('gross_profit', period_end, missing)

    return _result('gross_profit', period_end,
                   period['Revenue'].value - period['Cost of Revenue'].value,
                   period['Gross Profit'].value, 'revenue - cost of revenue vs gross profit')


CHECKS = {
    'balance_sheet': check_balance_sheet,
    'cash_flow': check_cash_flow,
    'gross_profit': check_gross_profit,
}

CHECK_DESCRIPTIONS = {
    'balance_sheet': 'assets equal liabilities plus equity',
    'cash_flow': 'cash flow sections plus currency effects equal the change in cash',
    'gross_profit': 'revenue less cost of revenue equals gross profit',
}


def check_period(facts: list[Fact], period_end: date, period_type: str = 'A') -> list[IdentityResult]:
    """Run every identity against one period."""
    return [check(facts, period_end, period_type) for check in CHECKS.values()]


@dataclass(frozen=True)
class IdentityReport:
    """Every identity result for a filer, with the verdict that callers should act on."""

    results: tuple[IdentityResult, ...]

    @property
    def passed(self) -> int:
        """Number of checks that ran and agreed."""
        return sum(1 for r in self.results if r.status is Status.PASS)

    @property
    def failed(self) -> tuple[IdentityResult, ...]:
        """Checks that ran and disagreed -- each one evidence of a mapping error."""
        return tuple(r for r in self.results if r.status is Status.FAIL)

    @property
    def skipped(self) -> int:
        """Number of checks that could not run for want of an input."""
        return sum(1 for r in self.results if r.status is Status.SKIPPED)

    @property
    def trustworthy(self) -> bool:
        """True only if something was actually verified and nothing contradicted.

        Requiring at least one PASS is the point: a filer whose every check was skipped has demonstrated
        nothing, and treating that as a clean bill of health is the failure this class exists to prevent.
        """
        return self.passed > 0 and not self.failed


def check_filer(facts: list[Fact], period_type: str = 'A', limit: int | None = None) -> IdentityReport:
    """Run every identity across a filer's reported periods, newest first."""
    ends = sorted({f.period_end for f in facts if f.period_type == period_type}, reverse=True)
    results = [r for end in ends[:limit] for r in check_period(facts, end, period_type)]

    report = IdentityReport(results=tuple(results))
    for failure in report.failed:
        log.warning(f'{failure.name} does not balance for {failure.period_end}: {failure.detail}')
    if not report.trustworthy:
        reason = (f'{len(report.failed)} identity check(s) failed' if report.failed
                  else f'nothing could be checked ({report.skipped} skipped for want of an input)')
        log.warning(f'{reason} -- treat this filer\'s numbers as unconfirmed')
    return report
