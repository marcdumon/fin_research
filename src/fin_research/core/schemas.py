"""Typed artifacts that agents exchange, and that the verifier checks.

Agents do not talk to each other. They emit these objects, a supervisor routes them, and everything an agent
asserts arrives as a `Claim` carrying the evidence for it. Free-form conversation between models produces
drift and mutual agreement at ten times the cost; typed artifacts make an agent's output something a later
stage can check rather than merely read.

Pydantic rather than the frozen dataclasses used elsewhere in this codebase, for one specific reason: these
are the JSON schemas sent to the model as its output contract, and pydantic generates the schema, validates
the response and coerces the types from a single definition. With dataclasses all three would be written by
hand and would drift apart. `extra='forbid'` turns an invented field into a validation error that triggers a
re-prompt, instead of a silent discard.

**The most important rule in this module: nothing an agent emits contains a character offset.** A model
cannot count characters, so offsets it supplies are plausible and wrong, and a citation check against them
would be measuring nothing. Agents quote text; the verifier finds that text and assigns the offsets. This is
why `EvidenceRequest` and `Evidence` are separate types rather than one type with optional fields -- the
distinction between 'what was claimed' and 'what was confirmed' is enforced by the type system.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Base for every artifact: immutable, and rejecting fields the schema did not define."""

    model_config = ConfigDict(frozen=True, extra='forbid')


class EvidenceKind(StrEnum):
    """What a piece of evidence points at."""

    SPAN = 'span'
    FACT = 'fact'
    MARKET = 'market'


class MatchKind(StrEnum):
    """How closely a quoted span matched the source, or that it did not.

    Ordered from strongest to weakest. Anything other than NONE means the text is genuinely present; the
    weaker rungs exist because a model retypes a quote in plain ASCII while filings are full of typographic
    punctuation and invisible characters.
    """

    EXACT = 'exact'
    WHITESPACE = 'whitespace'
    UNICODE = 'unicode'
    CASE = 'case'
    NONE = 'none'


class ClaimType(StrEnum):
    """What kind of assertion a claim is, which decides how it can be checked."""

    FACT = 'fact'
    NUMERIC = 'numeric'
    INFERENCE = 'inference'
    JUDGEMENT = 'judgement'


class Polarity(StrEnum):
    """Which side of the argument a claim supports."""

    BULL = 'bull'
    BEAR = 'bear'
    NEUTRAL = 'neutral'


class EvidenceRequest(Strict):
    """What an agent is permitted to emit in support of a claim.

    Deliberately minimal. The agent names a source and quotes from it verbatim; it does not say where in the
    source the quote is, because it cannot know and would guess.
    """

    kind: EvidenceKind = EvidenceKind.SPAN
    document_id: str | None = Field(None, description='document the quote is taken from')
    section_id: str | None = Field(None, description='section of that document, when known')
    quoted_text: str | None = Field(None, max_length=800, description='verbatim quote from the source')
    fact_id: str | None = Field(None, description='reported metric this claim rests on')


class Evidence(Strict):
    """What the verifier confirmed, with offsets it assigned itself."""

    evidence_id: str
    kind: EvidenceKind
    match_kind: MatchKind
    document_id: str | None = None
    section_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    quoted_text: str | None = None
    matched_text: str | None = None
    fact_id: str | None = None
    accession: str | None = None
    filed: date | None = None

    @property
    def is_grounded(self) -> bool:
        """True if the quoted text was actually found in the source."""
        return self.match_kind is not MatchKind.NONE


class Claim(Strict):
    """One assertion, with the evidence offered for it."""

    claim_id: str = Field(default_factory=lambda: '', description='assigned by the runtime, not the model')
    claim_type: ClaimType
    text: str = Field(max_length=600)
    subject: str | None = None
    polarity: Polarity = Polarity.NEUTRAL
    importance: int = Field(3, ge=1, le=5)
    numeric_value: float | None = None
    numeric_unit: str | None = None
    # Which reported metric a figure refers to, when it refers to one. Naming it is what lets the number be
    # checked against the filer's own statements rather than merely against the sentence it was quoted from:
    # a model can cite a real sentence about revenue and still state the wrong revenue.
    metric: str | None = Field(None, description='canonical metric this figure reports, if it reports one')
    evidence: list[EvidenceRequest] = Field(default_factory=list, max_length=6)
    attacks: list[str] = Field(default_factory=list, description='claim ids this one disputes')

    @property
    def is_checkable_number(self) -> bool:
        """True if this claim states a figure that can be compared against a reported one."""
        return self.claim_type is ClaimType.NUMERIC and self.numeric_value is not None and bool(self.metric)

    @property
    def needs_evidence(self) -> bool:
        """True for claims that are checkable and therefore must be checked.

        Judgement is exempt: 'this is a good business at this price' cannot be grounded in a quote, and
        pretending otherwise would make the groundedness figure meaningless.
        """
        return self.claim_type in (ClaimType.FACT, ClaimType.NUMERIC)


class Finding(Strict):
    """A single observation an analyst agent wants to put on the record."""

    headline: str = Field(max_length=200)
    detail: str = Field(max_length=2000)
    claims: list[Claim] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=6)


class FindingSet(Strict):
    """What every analyst agent returns: its findings, and what it could not determine."""

    findings: list[Finding] = Field(default_factory=list, max_length=20)
    unresolved: list[str] = Field(default_factory=list, max_length=10,
                                  description='questions the source did not answer')


class Assumption(Strict):
    """One input to a valuation, and the reason for it."""

    name: str = Field(max_length=120)
    value: float
    unit: str = Field(max_length=40)
    rationale: str = Field(max_length=600)
    evidence: list[EvidenceRequest] = Field(default_factory=list, max_length=4)


class Scenario(Strict):
    """One valuation outcome under a named set of conditions."""

    name: Literal['bear', 'base', 'bull']
    value_per_share: float
    probability: float = Field(ge=0.0, le=1.0)
    drivers: list[str] = Field(default_factory=list, max_length=6)


class Valuation(Strict):
    """A valuation expressed as scenarios rather than a single number.

    A point estimate hides the only interesting part -- what has to be true for it to hold -- so the schema
    makes scenarios and their assumptions mandatory structure rather than optional prose.
    """

    method: str = Field(max_length=120)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=12)
    scenarios: list[Scenario] = Field(default_factory=list, max_length=3)
    current_price: float | None = None

    @property
    def expected_value(self) -> float | None:
        """Probability-weighted value per share, or None if no scenarios were given."""
        if not self.scenarios:
            return None
        total = sum(s.probability for s in self.scenarios)
        if total <= 0:
            return None
        return sum(s.value_per_share * s.probability for s in self.scenarios) / total


class Thesis(Strict):
    """One side of the argument, in a debate of fixed length."""

    stance: Polarity
    round: int = Field(1, ge=1, le=2)
    summary: str = Field(max_length=1500)
    claims: list[Claim] = Field(default_factory=list, max_length=12)
    # Naming what would break the thesis is the difference between an argument and an advertisement.
    falsifiers: list[str] = Field(default_factory=list, max_length=6,
                                  description='conditions that would break this thesis')


class Disagreement(Strict):
    """A point the two sides read differently, and how it was resolved."""

    question: str = Field(max_length=300)
    bull_position: str = Field(max_length=600)
    bear_position: str = Field(max_length=600)
    resolution: str | None = Field(None, max_length=600)
    resolved: bool = False


class Triage(Strict):
    """A quick read on whether a company is worth a full deep dive.

    The verdict is a closed set rather than a score, because a number invites averaging and this decision is
    binary in practice: something either earns an hour of expensive reading or it does not. `pass` exists as
    a middle answer so the model is not forced to reject a company it simply could not judge from what it was
    given -- which is a different and more useful thing to know than a rejection.
    """

    ticker: str
    verdict: Literal['escalate', 'pass', 'reject']
    rationale: str = Field(max_length=800)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    # What the deep dive should settle. This is the part that makes triage worth doing rather than skipping:
    # it tells the expensive stage where to spend its attention.
    open_questions: list[str] = Field(default_factory=list, max_length=5)
    claims: list[Claim] = Field(default_factory=list, max_length=8)

    @property
    def is_escalated(self) -> bool:
        """True if this company earns a deep dive."""
        return self.verdict == 'escalate'


class Report(Strict):
    """The finished research note."""

    ticker: str
    as_of: date
    thesis: str = Field(max_length=2000)
    direction: Literal['constructive', 'cautious', 'neutral']
    confidence: Literal['low', 'medium', 'high']
    numbers: list[Finding] = Field(default_factory=list, max_length=12)
    bull_case: list[Claim] = Field(default_factory=list, max_length=12)
    bear_case: list[Claim] = Field(default_factory=list, max_length=12)
    falsifiers: list[str] = Field(default_factory=list, max_length=6)
    disagreements: list[Disagreement] = Field(default_factory=list, max_length=6)
    valuation: Valuation | None = None
    generated_at: datetime | None = None


# Registry of the artifact types a task can produce, mirroring the name -> thing plus name -> description
# pattern the neighbouring projects use for providers and checks.
ARTIFACTS = {
    'FindingSet': FindingSet,
    'Triage': Triage,
    'Thesis': Thesis,
    'Valuation': Valuation,
    'Report': Report,
}

ARTIFACT_DESCRIPTIONS = {
    'FindingSet': 'observations drawn from one source, each claim carrying its evidence',
    'Triage': 'a quick verdict on whether a company deserves the expensive reading',
    'Thesis': 'one side of the argument, with the conditions that would break it',
    'Valuation': 'scenarios and the assumptions behind them',
    'Report': 'the finished research note',
}
