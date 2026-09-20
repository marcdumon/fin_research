"""Turn SEC's XBRL company facts into normalised financial metrics with point-in-time discipline.

The companyfacts endpoint returns every number a company ever tagged, keyed by us-gaap concept. Three things
have to happen before an agent can reason about any of it.

First, concepts map to canonical metrics through the vendored `anchors` table, so 'CostOfGoodsAndServicesSold'
and 'CostOfRevenue' both become 'Cost of Revenue'.

Second, periods are classified by measured span rather than by the labels SEC supplies. The labels lie: Apple
has rows tagged `fp='FY', form='10-K'` that actually span ninety days. Span-based classification also discards
half-year and nine-month cumulatives, which otherwise masquerade as annual figures.

Third, signs are normalised so outflows are negative. As filed, `PaymentsToAcquirePropertyPlantAndEquipment`
is a positive magnitude -- capital expenditure of +8.2bn. Storing it that way makes the cash flow statement
fail to add up, so payment-type concepts are negated here, once, at the boundary. This is a deliberate choice
against the source project, which contradicts itself on exactly this point.

Every row keeps `accession` and `filed`. `accession` is a foreign key straight back to the filing the number
came from, so a citation to a number is as checkable as a citation to a sentence; `filed` is what makes it
possible to ask what was knowable on a date, rather than what is known now.
"""

import logging
from dataclasses import dataclass
from datetime import date

from fin_research.ingest.anchors import BLOCKLIST, METRIC_ANCHORS, METRIC_PRIORITY
from fin_research.ingest.edgar import EdgarClient

log = logging.getLogger(__name__)

COMPANY_FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'

# Concepts reported as positive magnitudes that are economically outflows. Negated on the way in so that the
# cash flow statement articulates: operating + investing + financing + currency effect = change in cash.
OUTFLOW_METRICS = frozenset({'CapEx', 'Acquisitions', 'Debt Repaid', 'Dividends Paid', 'Share Repurchases'})

# Concepts that are not canonical metrics but are needed to check that the statements add up. Carried
# through parsing with `metric=None` so the verification layer can find them by tag.
#
# Names were read off ten real filers rather than guessed, and the ordering matters as much as the membership.
# Two things are easy to get wrong here and both produce differences that look like mapping errors:
#
#   - The '...DisposalGroupIncludingDiscontinuedOperations' variant is the discontinued-operations *portion*
#     of the change, not the whole of it. One large retailer tags it as zero while reporting a billion-dollar
#     change, so it ranks last and is only ever a fallback.
#   - Filers mix bases freely: several report the change in 'cash, cash equivalents and restricted cash' while
#     reporting the currency effect on plain 'cash and cash equivalents'. So the currency effect is searched
#     across every known concept independently, not paired to whichever total was found.
NET_CASH_CHANGE_INCLUDING_FX = (
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect',
    'CashAndCashEquivalentsPeriodIncreaseDecrease',
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecrease'
    'IncludingExchangeRateEffectDisposalGroupIncludingDiscontinuedOperations',
)
NET_CASH_CHANGE_EXCLUDING_FX = (
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect',
    'CashAndCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect',
)
FX_EFFECT_ON_CASH = (
    'EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
    'EffectOfExchangeRateOnCashAndCashEquivalents',
    'EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations',
    'EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'
    'IncludingDisposalGroupAndDiscontinuedOperations',
)

# Where a filer reports its three sections as continuing operations only, the discontinued portion is tagged
# separately and must be added back before the statement reconciles. One telecoms spin-off year turns on this:
# 33bn of discontinued financing cash flow sits outside the continuing-operations totals.
DISCONTINUED_CASH_FLOW = {
    'Operating Cash Flow': 'CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations',
    'Investing Cash Flow': 'CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations',
    'Financing Cash Flow': 'CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations',
}

CONTINUING_OPERATIONS_SUFFIX = 'ContinuingOperations'

# Redeemable interests sit between liabilities and equity on the balance sheet -- the 'mezzanine'. A filer
# carrying them balances only when they are counted, which is why they are looked up rather than assumed zero.
MEZZANINE_EQUITY = (
    'RedeemableNoncontrollingInterestEquityCarryingAmount',
    'RedeemableNoncontrollingInterestEquityFairValue',
    'TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests',
    'TemporaryEquityCarryingAmountAttributableToParent',
)

VERIFICATION_TAGS = frozenset(
    NET_CASH_CHANGE_INCLUDING_FX + NET_CASH_CHANGE_EXCLUDING_FX + FX_EFFECT_ON_CASH + MEZZANINE_EQUITY
    + tuple(DISCONTINUED_CASH_FLOW.values()))

_ANNUAL_FORMS = frozenset({'10-K', '10-KT', '20-F', '40-F'})
_QUARTER_FORMS = frozenset({'10-Q', '10-QT'})

# Span windows that reject half-year and nine-month cumulative rows, which carry no marker of their own.
_MIN_QUARTER_DAYS, _MAX_QUARTER_DAYS = 60, 105
_MIN_ANNUAL_DAYS, _MAX_ANNUAL_DAYS = 340, 380

# A number filed more than this long after the period it describes is a restatement, not an original report.
_MAX_FILING_LAG_DAYS = 380


@dataclass(frozen=True)
class Fact:
    """One reported number, mapped to a canonical metric and tied to the filing that carried it.

    `anchor_rank` is the concept's position in its metric's priority list. It matters because filers commonly
    tag several anchors for the same metric -- a REIT reports both parent-only equity and equity including
    noncontrolling interests -- and only the higher-priority one makes the balance sheet balance.
    """

    cik: int
    accession: str
    taxonomy: str
    tag: str
    metric: str | None
    statement: str | None
    unit: str
    value: float
    period_start: date | None
    period_end: date
    period_type: str
    form: str
    filed: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    anchor_rank: int = 0

    @property
    def is_instant(self) -> bool:
        """True for balance-sheet facts, which describe a moment rather than a span."""
        return self.period_start is None


def _build_tag_index() -> dict[str, tuple[str, str, int]]:
    """Map every anchor concept to its canonical metric and statement.

    A few concepts legitimately serve two metrics -- depreciation appears on both the income statement and the
    cash flow statement -- so ties are broken by `METRIC_PRIORITY`, the ordering the source project worked out
    against real filings.
    """
    rank = {metric: position for position, metric in enumerate(METRIC_PRIORITY)}
    index: dict[str, tuple[str, str, int]] = {}

    for metric, (statement, tags) in METRIC_ANCHORS.items():
        for position, tag in enumerate(tags):
            if tag in BLOCKLIST:
                continue
            incumbent = index.get(tag)
            if incumbent is None or rank.get(metric, len(rank)) < rank.get(incumbent[0], len(rank)):
                index[tag] = (metric, statement, position)
    return index


TAG_INDEX = _build_tag_index()


def classify_period(start: date | None, end: date, form: str, filed: date | None = None) -> str | None:
    """Classify a fact's period as annual ('A') or quarterly ('Q'), or None to discard it.

    Vendored from xbrl_normaliser, where it is the one part of the normalisation core carrying tests. Duration
    facts are judged purely on measured span, because the form and fiscal-period labels SEC returns are
    unreliable; instant facts have no span, so the form is all there is.
    """
    if filed is not None and (filed - end).days > _MAX_FILING_LAG_DAYS:
        return None

    if start is None:
        base = form.split('/')[0]
        if base in _ANNUAL_FORMS:
            return 'A'
        if base in _QUARTER_FORMS:
            return 'Q'
        return None

    span = (end - start).days
    if _MIN_QUARTER_DAYS <= span <= _MAX_QUARTER_DAYS:
        return 'Q'
    if _MIN_ANNUAL_DAYS <= span <= _MAX_ANNUAL_DAYS:
        return 'A'
    return None


def normalise_sign(metric: str | None, value: float) -> float:
    """Flip positive-magnitude outflows to negative, leaving everything else as filed."""
    return -abs(value) if metric in OUTFLOW_METRICS else value


def _parse_date(text: str | None) -> date | None:
    """Parse an ISO date, or None on empty or malformed input."""
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_facts(raw: dict, cik: int, *, mapped_only: bool = True) -> list[Fact]:
    """Flatten a companyfacts payload into `Fact` rows.

    Unmapped concepts are dropped by default: a company tags hundreds of them and carrying the long tail would
    bury the forty-two that the analysis actually speaks in. The exception is `VERIFICATION_TAGS`, kept because
    the statements cannot be checked for internal consistency without them. Pass `mapped_only=False` when
    exploring what a filer actually reports.
    """
    facts: list[Fact] = []

    for taxonomy, concepts in raw.get('facts', {}).items():
        for tag, concept in concepts.items():
            mapped = TAG_INDEX.get(tag)
            if mapped is None and mapped_only and tag not in VERIFICATION_TAGS:
                continue
            metric, statement, anchor_rank = mapped if mapped else (None, None, 0)

            for unit, rows in concept.get('units', {}).items():
                for row in rows:
                    end = _parse_date(row.get('end'))
                    filed = _parse_date(row.get('filed'))
                    if end is None or filed is None or row.get('val') is None:
                        continue

                    start = _parse_date(row.get('start'))
                    period_type = classify_period(start, end, row.get('form', ''), filed)
                    if period_type is None:
                        continue

                    facts.append(Fact(
                        cik=cik, accession=row.get('accn', ''), taxonomy=taxonomy, tag=tag,
                        metric=metric, statement=statement, unit=unit,
                        value=normalise_sign(metric, float(row['val'])),
                        period_start=start, period_end=end, period_type=period_type,
                        form=row.get('form', ''), filed=filed,
                        fiscal_year=row.get('fy'), fiscal_period=row.get('fp'), anchor_rank=anchor_rank))

    return facts


async def fetch_facts(client: EdgarClient, cik: int, *, mapped_only: bool = True) -> list[Fact]:
    """Fetch and normalise every reported metric for one company."""
    cache = client.cfg.data_root / 'sec' / 'facts' / f'CIK{cik:010d}.json'
    raw = await client.get_json(COMPANY_FACTS_URL.format(cik=cik), cache)
    facts = parse_facts(raw, cik, mapped_only=mapped_only)
    log.info(f'CIK {cik}: {len(facts)} normalised facts across {len({f.metric for f in facts})} metrics')
    return facts


def as_of(facts: list[Fact], cutoff: date | None = None) -> list[Fact]:
    """Keep only what had been filed by `cutoff`, and for each period the most recently filed version.

    Restatements arrive as a later filing carrying a different value for a period already reported, so
    'latest filed wins' is what makes a series reflect current knowledge. Applying the cutoff first is what
    keeps a backtest honest: a restatement published in 2025 must not inform a decision dated 2023.
    """
    visible = [f for f in facts if cutoff is None or f.filed <= cutoff]

    # Keyed by concept rather than by metric. Keying by metric would silently discard every anchor a filer
    # tagged but one, which is how the balance sheet stopped balancing for filers reporting both parent-only
    # and including-noncontrolling-interest equity. Choosing between anchors is `by_period`'s job.
    best: dict[tuple[str, str, date, str], Fact] = {}
    for fact in visible:
        key = (fact.tag, fact.period_type, fact.period_end, fact.unit)
        incumbent = best.get(key)
        if incumbent is None or fact.filed > incumbent.filed:
            best[key] = fact
    return sorted(best.values(), key=lambda f: (f.metric or '', f.period_end, f.anchor_rank))


def find_tag(facts: list[Fact], tags: tuple[str, ...], period_end: date, period_type: str = 'A') -> Fact | None:
    """First fact matching any of `tags` for a period, in the tags' own priority order.

    Used for the verification-only concepts, which have no canonical metric name to look up by.
    """
    for tag in tags:
        for fact in facts:
            if fact.tag == tag and fact.period_end == period_end and fact.period_type == period_type:
                return fact
    return None


def find_all_tags(facts: list[Fact], tags: tuple[str, ...], period_end: date,
                  period_type: str = 'A') -> list[Fact]:
    """Every fact matching any of `tags` for a period, in the tags' own priority order."""
    found = []
    for tag in tags:
        found.extend(f for f in facts if f.tag == tag and f.period_end == period_end
                     and f.period_type == period_type)
    return found


def series(facts: list[Fact], metric: str, period_type: str = 'A') -> list[Fact]:
    """Every reported value of one metric at one frequency, oldest first."""
    return sorted((f for f in facts if f.metric == metric and f.period_type == period_type),
                  key=lambda f: f.period_end)


def by_period(facts: list[Fact], period_end: date, period_type: str = 'A') -> dict[str, Fact]:
    """The metrics reported for one specific period, keyed by canonical metric name.

    Where a filer tagged several anchors for one metric, the highest-priority anchor wins -- that ordering is
    the whole content of the anchor table, and ignoring it produces numbers that do not reconcile.
    """
    chosen: dict[str, Fact] = {}
    for fact in facts:
        if not fact.metric or fact.period_end != period_end or fact.period_type != period_type:
            continue
        incumbent = chosen.get(fact.metric)
        if incumbent is None or fact.anchor_rank < incumbent.anchor_rank:
            chosen[fact.metric] = fact
    return chosen


def periods(facts: list[Fact], period_type: str = 'A') -> list[date]:
    """Distinct period ends present at one frequency, oldest first."""
    return sorted({f.period_end for f in facts if f.period_type == period_type})
