"""Confirm that every quoted citation really appears in the source, and assign its offsets.

This is the mechanical backbone of the whole system. An agent asserts something and quotes the filing; this
module finds that quote in the canonical text and records where. A quote that cannot be found is rejected, so
a fabricated citation cannot enter the store at all -- the failure mode is a dropped claim, never an invented
source. That is what makes 'did the model make this up' a string operation instead of a judgement call.

Matching runs down a ladder, recording which rung succeeded. Exact matching alone would be the wrong bar: a
model retypes a quote in plain ASCII, while filings carry typographic apostrophes, non-breaking spaces and
thousands of zero-width characters. Rejecting those would measure Unicode rather than honesty. Every rung is
a pure character-class substitution with no semantic content, so containment after folding remains a fact
about strings. The rung is recorded because a drift toward the weaker ones is itself a signal.

Offsets always index the canonical text, never the folded form: a hit is mapped back through an index built
during folding, so `text[start:end]` returns exactly what was matched.
"""

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from fin_research.core.schemas import Claim, Evidence, EvidenceKind, EvidenceRequest, Finding, MatchKind

log = logging.getLogger(__name__)

# Typographic characters a model routinely replaces with their ASCII equivalents when quoting.
QUOTE_FOLD = {
    chr(0x2018): "'", chr(0x2019): "'", chr(0x201a): "'", chr(0x201b): "'",
    chr(0x201c): '"', chr(0x201d): '"', chr(0x201e): '"',
    chr(0x2013): '-', chr(0x2014): '-', chr(0x2015): '-', chr(0x2212): '-',
    chr(0x00a0): ' ', chr(0x2007): ' ', chr(0x202f): ' ',
}
ZERO_WIDTH = frozenset(map(chr, (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0x00ad)))

# Quotes shorter than this are not evidence -- 'revenue' appears everywhere and proves nothing.
MIN_QUOTE_CHARS = 12

# Scale words a model uses when restating a figure it read as a full number.
SCALES = {
    'trillion': 1e12, 'tn': 1e12,
    'billion': 1e9, 'bn': 1e9, 'b': 1e9,
    'million': 1e6, 'mn': 1e6, 'mm': 1e6, 'm': 1e6,
    'thousand': 1e3, 'k': 1e3,
}
NUMERIC_TOLERANCE = 0.005

_LADDER = ((0, MatchKind.EXACT), (1, MatchKind.WHITESPACE), (2, MatchKind.UNICODE), (3, MatchKind.CASE))


def fold(text: str, level: int) -> tuple[str, list[int]]:
    """Normalise `text` at one rung of the ladder, returning it with a map back to the original offsets.

    The map is what keeps offsets honest: folding changes lengths -- whitespace collapses, zero-width
    characters vanish, case folding can turn one character into two -- so a position in the folded string
    means nothing without it. `index_map[i]` is the index in `text` that produced folded character `i`.
    """
    if level == 0:
        return text, list(range(len(text)))

    out: list[str] = []
    index_map: list[int] = []
    previous_was_space = False

    for position, character in enumerate(text):
        if character.isspace():
            if not previous_was_space:
                out.append(' ')
                index_map.append(position)
                previous_was_space = True
            continue
        previous_was_space = False

        folded = character
        if level >= 2:
            if character in ZERO_WIDTH:
                continue
            folded = unicodedata.normalize('NFKC', QUOTE_FOLD.get(character, character))
        if level >= 3:
            folded = folded.casefold()

        for produced in folded:
            out.append(produced)
            index_map.append(position)

    return ''.join(out), index_map


@dataclass(frozen=True)
class Match:
    """Where a quote was found, and how hard it had to be looked for."""

    char_start: int
    char_end: int
    match_kind: MatchKind
    matched_text: str


class SourceText:
    """One document's canonical text, with its folded forms cached.

    Folding a large filing is linear but not free, and a research run looks up dozens of quotes against the
    same document, so each rung is computed once on demand rather than per lookup.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self._folds: dict[int, tuple[str, list[int]]] = {}

    def _folded(self, level: int) -> tuple[str, list[int]]:
        """The document folded to one level, computed once."""
        if level not in self._folds:
            self._folds[level] = fold(self.text, level)
        return self._folds[level]

    def locate(self, quote: str) -> Match | None:
        """Find `quote` in this document, trying progressively more forgiving comparisons.

        Returns None when the quote is absent at every rung, which is the signal that the model invented it.
        """
        if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
            return None

        for level, kind in _LADDER:
            haystack, index_map = self._folded(level)
            needle, _ = fold(quote, level)
            needle = needle.strip() if level >= 1 else needle
            if not needle:
                continue

            position = haystack.find(needle)
            if position < 0:
                continue

            start = index_map[position]
            end = index_map[position + len(needle) - 1] + 1
            return Match(char_start=start, char_end=end, match_kind=kind, matched_text=self.text[start:end])

        return None


def parse_scale(unit: str | None) -> float:
    """Multiplier implied by a unit string, defaulting to 1.

    A model that read '416,161' in a table captioned '$ in Millions' will often report 416.2 with a unit of
    'USD billions'. Comparing that to the stored figure without rescaling produces a false mismatch.
    """
    if not unit:
        return 1.0
    for word, multiplier in SCALES.items():
        if re.search(rf'\b{word}s?\b', unit, re.IGNORECASE):
            return multiplier
    return 1.0


def verify_numeric(claim: Claim, reported: float, tolerance: float = NUMERIC_TOLERANCE) -> bool:
    """True if a claim's number agrees with the reported figure once scale is accounted for."""
    if claim.numeric_value is None:
        return False
    stated = claim.numeric_value * parse_scale(claim.numeric_unit)
    if reported == 0:
        return abs(stated) <= tolerance
    return abs(stated - reported) / abs(reported) <= tolerance


class NumericVerdict(StrEnum):
    """What comparing a stated figure against the filer's own statements established.

    Three values for the same reason the accounting identities have three: a check that could not run has not
    passed, and collapsing that into success is how a system ends up reporting confidence it never earned.
    """

    CONFIRMED = 'confirmed'
    CONTRADICTED = 'contradicted'
    UNCHECKED = 'unchecked'


@dataclass(frozen=True)
class FactIndex:
    """The figures a company actually reported, arranged for lookup by metric.

    Built once per run from the normalised facts. Holds every period rather than only the latest, because a
    claim often refers to a prior year -- rejecting a correct figure for the year before last would be worse
    than not checking at all.
    """

    by_metric: dict[str, tuple[float, ...]] = field(default_factory=dict)

    @classmethod
    def from_facts(cls, facts: Iterable) -> 'FactIndex':
        """Index normalised facts by their canonical metric."""
        grouped: dict[str, list[float]] = {}
        for fact in facts:
            metric = getattr(fact, 'metric', None)
            if metric:
                grouped.setdefault(metric, []).append(float(fact.value))
        return cls(by_metric={metric: tuple(values) for metric, values in grouped.items()})

    def knows(self, metric: str) -> bool:
        """True if the company reported this metric at all."""
        return metric in self.by_metric

    def check(self, claim: Claim) -> tuple[NumericVerdict, float | None]:
        """Compare a claim's figure against every value the company reported for that metric.

        Any reported period matching is enough. A model quoting last year's revenue is being accurate, and a
        check that insisted on the latest period would reject it for the wrong reason.

        Sign is compared on magnitude: the statements store outflows negative, while prose says 'capital
        expenditure of 8.2 billion' without a minus, and treating that as a contradiction would reject
        correct claims about every cash outflow a company makes.
        """
        if not claim.is_checkable_number:
            return NumericVerdict.UNCHECKED, None
        reported = self.by_metric.get(claim.metric or '')
        if not reported:
            return NumericVerdict.UNCHECKED, None

        for value in reported:
            if verify_numeric(claim, value) or verify_numeric(claim, abs(value)):
                return NumericVerdict.CONFIRMED, value
        return NumericVerdict.CONTRADICTED, reported[0]


def _evidence_id(request: EvidenceRequest, match: Match | None) -> str:
    """Content-addressed id, so the same citation from two agents collapses to one row."""
    from fin_research.ingest.store import digest
    if match is None:
        return digest(request.document_id, request.section_id, request.quoted_text, 'unmatched')
    return digest(request.document_id, match.char_start, match.char_end)


def verify_evidence(request: EvidenceRequest, sources: dict[str, SourceText]) -> Evidence:
    """Resolve one piece of offered evidence into a confirmed record, matched or not.

    Evidence that names no document, or a document that was never ingested, is recorded as unmatched rather
    than skipped -- an unresolvable citation is exactly as suspect as a wrong one, and silently dropping it
    would flatter the groundedness figure.
    """
    if request.kind is not EvidenceKind.SPAN:
        return Evidence(evidence_id=_evidence_id(request, None), kind=request.kind,
                        match_kind=MatchKind.EXACT if request.fact_id else MatchKind.NONE,
                        fact_id=request.fact_id, document_id=request.document_id)

    source = sources.get(request.document_id or '')
    match = source.locate(request.quoted_text or '') if source else None

    return Evidence(
        evidence_id=_evidence_id(request, match),
        kind=EvidenceKind.SPAN,
        match_kind=match.match_kind if match else MatchKind.NONE,
        document_id=request.document_id,
        section_id=request.section_id,
        char_start=match.char_start if match else None,
        char_end=match.char_end if match else None,
        quoted_text=request.quoted_text,
        matched_text=match.matched_text if match else None)


@dataclass(frozen=True)
class ClaimVerdict:
    """Whether one claim survived checking, and the evidence behind that decision."""

    claim: Claim
    evidence: tuple[Evidence, ...]
    grounded: bool
    reason: str
    numeric: 'NumericVerdict' = None  # type: ignore[assignment]

    @property
    def weakest_match(self) -> MatchKind | None:
        """The least exact rung any of this claim's evidence needed."""
        spans = [e.match_kind for e in self.evidence if e.kind is EvidenceKind.SPAN]
        return max(spans, key=lambda k: [m.value for m in MatchKind].index(k.value)) if spans else None


def verify_claim(claim: Claim, sources: dict[str, SourceText],
                 facts: FactIndex | None = None) -> ClaimVerdict:
    """Check one claim's citations, and its arithmetic where there is arithmetic to check.

    Judgement claims are exempt by design -- 'this is a good business' cannot be grounded in a quote, and
    pretending otherwise would make the groundedness figure meaningless. Everything checkable is checked, and
    a checkable claim offering no evidence fails outright rather than passing for lack of contradiction.

    A figure is checked twice over: the sentence it was quoted from must exist, and the number itself must
    match what the company reported. Those are different failures. A model can quote a real sentence about
    revenue and state the wrong revenue, and span checking alone would pass it.
    """
    evidence = tuple(verify_evidence(request, sources) for request in claim.evidence)

    if not claim.needs_evidence:
        return ClaimVerdict(claim=claim, evidence=evidence, grounded=True, reason='judgement, exempt by design')

    if not evidence:
        return ClaimVerdict(claim=claim, evidence=(), grounded=False, reason='no evidence offered')

    unmatched = [e for e in evidence if not e.is_grounded]
    if unmatched:
        return ClaimVerdict(claim=claim, evidence=evidence, grounded=False,
                            reason=f'{len(unmatched)} of {len(evidence)} citations not found in the source')

    numeric, reported = (facts.check(claim) if facts else (NumericVerdict.UNCHECKED, None))
    if numeric is NumericVerdict.CONTRADICTED:
        stated = (claim.numeric_value or 0) * parse_scale(claim.numeric_unit)
        return ClaimVerdict(
            claim=claim, evidence=evidence, grounded=False, numeric=numeric,
            reason=f'states {claim.metric} of {stated:,.0f} but the filer reported {reported:,.0f}')

    reason = ('every citation resolved, and the figure matches what was reported'
              if numeric is NumericVerdict.CONFIRMED else 'every citation resolved')
    return ClaimVerdict(claim=claim, evidence=evidence, grounded=True, numeric=numeric, reason=reason)


@dataclass(frozen=True)
class VerificationReport:
    """The outcome of checking a batch of claims."""

    verdicts: tuple[ClaimVerdict, ...]

    @property
    def grounded(self) -> tuple[ClaimVerdict, ...]:
        """Claims that may be used downstream."""
        return tuple(v for v in self.verdicts if v.grounded)

    @property
    def rejected(self) -> tuple[ClaimVerdict, ...]:
        """Claims dropped for failing their citations."""
        return tuple(v for v in self.verdicts if not v.grounded)

    @property
    def checkable(self) -> tuple[ClaimVerdict, ...]:
        """Claims that were actually subject to the check, excluding exempt judgements."""
        return tuple(v for v in self.verdicts if v.claim.needs_evidence)

    @property
    def groundedness(self) -> float | None:
        """Share of checkable claims whose citations resolved, or None if nothing was checkable.

        None rather than 1.0 when nothing could be checked: a batch of pure judgement has demonstrated
        nothing, and reporting a perfect score for it would be the same vacuous pass the identity checks
        were built to avoid.
        """
        checkable = self.checkable
        if not checkable:
            return None
        return sum(1 for v in checkable if v.grounded) / len(checkable)

    def numeric_outcomes(self) -> dict[str, int]:
        """Count of figures by what checking them established.

        Reported separately from citation matching because they answer different questions: whether the
        source says what was claimed, and whether the number is right.
        """
        counts: dict[str, int] = {}
        for verdict in self.verdicts:
            if verdict.numeric and verdict.numeric is not NumericVerdict.UNCHECKED:
                counts[verdict.numeric.value] = counts.get(verdict.numeric.value, 0) + 1
        return counts

    def match_kinds(self) -> dict[str, int]:
        """Count of evidence by match rung, so drift toward weaker matching is visible."""
        counts: dict[str, int] = {}
        for verdict in self.verdicts:
            for item in verdict.evidence:
                if item.kind is EvidenceKind.SPAN:
                    counts[item.match_kind.value] = counts.get(item.match_kind.value, 0) + 1
        return counts


def verify_claims(claims: list[Claim], sources: dict[str, SourceText],
                  facts: FactIndex | None = None) -> VerificationReport:
    """Check every claim in a batch."""
    report = VerificationReport(verdicts=tuple(verify_claim(claim, sources, facts) for claim in claims))
    for verdict in report.rejected:
        log.warning(f'dropping claim: {verdict.reason} -- {verdict.claim.text[:90]}')
    return report


def surviving_findings(findings: list[Finding], sources: dict[str, SourceText],
                       facts: FactIndex | None = None) -> tuple[list[Finding], VerificationReport]:
    """Strip ungrounded claims out of findings before anything downstream reads them.

    Dropping happens here rather than at report time so an expensive model never sees, and never builds on,
    a claim that failed its citations.
    """
    verdicts: list[ClaimVerdict] = []
    kept: list[Finding] = []

    for finding in findings:
        report = verify_claims(finding.claims, sources, facts)
        verdicts.extend(report.verdicts)
        survivors = [v.claim for v in report.grounded]
        if survivors:
            kept.append(finding.model_copy(update={'claims': survivors}))

    return kept, VerificationReport(verdicts=tuple(verdicts))
