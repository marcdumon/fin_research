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

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    """Base for every artifact: immutable, and rejecting fields the schema did not define.

    A string or list longer than its declared maximum is cut to fit rather than rejected. The lengths are
    there to stop a model writing an essay into a field, not to decide whether its work is usable: two runs
    lost an entire valuation -- the scenarios, the assumptions, the reasoning -- one because the method ran
    to 130 characters where 120 were asked for, the other because it listed 19 assumptions where 12 were.
    Cutting keeps the limit doing its job at a cost proportional to the fault. A missing field or a wrong
    type still fails, because those are faults in the work itself rather than in how much of it there is.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def _fit_to_declared_limits(cls, data: object) -> object:
        """Cut any over-long string or list to the maximum its field declares."""
        if not isinstance(data, dict):
            return data

        trimmed = dict(data)
        for name, field in cls.model_fields.items():
            value = trimmed.get(name)
            if not isinstance(value, str | list):
                continue
            limit = next((m.max_length for m in field.metadata if getattr(m, 'max_length', None)), None)
            if limit is not None and len(value) > limit:
                trimmed[name] = value[:limit]
        return trimmed


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

    POSITIONAL is the odd one, and the one to watch. It means a passage number resolved and nothing
    corroborated it: the citation points at real text, but no check established that this is the text the
    claim was about. A number cannot be compared against what it was meant to point at, so a model citing
    passage 57 when it meant 75 produces a confident, exactly located, wrong citation. The note counts these
    separately for that reason -- one resting mostly on positional matches has been located, not checked.
    """

    EXACT = 'exact'
    WHITESPACE = 'whitespace'
    UNICODE = 'unicode'
    CASE = 'case'
    POSITIONAL = 'positional'
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

    Deliberately minimal, and it never carries an offset: the agent names a passage of a source, and the
    verifier turns that into a location. An earlier version asked for a verbatim quote instead, which failed
    in the most awkward way possible -- models reproduce a sentence with one word changed, leaving an
    accurate claim with an uncheckable citation.
    """

    kind: EvidenceKind = EvidenceKind.SPAN
    document_id: str | None = Field(None, description='document this evidence comes from')
    # The preferred form. Sources are shown to the model with numbered passages, and citing the number is
    # exact by construction -- a number cannot be paraphrased, which quotations demonstrably can be.
    passage_id: int | None = Field(None, description='number of the passage in the source, as shown')
    section_id: str | None = Field(None, description='section of that document, when known')
    quoted_text: str | None = Field(None, max_length=800, description='verbatim quote, if citing by text')
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

    claim_id: str = Field('', description='assigned by the runtime, not the model')
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
    def states_a_share(self) -> bool:
        """True if the figure is a proportion rather than an amount."""
        unit = (self.numeric_unit or '').lower()
        return '%' in unit or 'percent' in unit or 'share of' in unit or 'basis point' in unit

    @property
    def is_checkable_number(self) -> bool:
        """True if this claim states a figure that can be compared against a reported one.

        A proportion is not one, whatever it is labelled. Models routinely attach a metric to a percentage
        of it -- 'the five largest contracts generated 19% of total revenues' arrives tagged Revenue -- and
        comparing 19 against the reported revenue rejects a correct, quoted claim for saying something it
        never said. The span check still applies, so the sentence must exist; only the arithmetic is skipped.
        """
        return (self.claim_type is ClaimType.NUMERIC and self.numeric_value is not None
                and bool(self.metric) and not self.states_a_share)

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
    """The finished research note.

    Carries identifiers rather than content. Everything it refers to has already been produced and checked,
    so restating it here would mean the expensive model transcribing verified work and attaching citations it
    cannot have. The renderer resolves the identifiers back to the original claims and their evidence.

    The valuation is not part of this schema either, for the same reason and one more: embedding it made the
    schema deep enough that a provider rejected the request outright, since structured output has nesting and
    size limits.
    """

    ticker: str
    as_of: date
    thesis: str = Field(max_length=2000)
    direction: Literal['constructive', 'cautious', 'neutral']
    confidence: Literal['low', 'medium', 'high']
    # Identifiers of claims established earlier, not new claims. The synthesiser selects and orders what
    # survived verification; it does not restate it. Asking it to restate produced claims citing a document
    # that did not exist, because it was shown summaries rather than sources and had nothing real to cite.
    numbers: list[str] = Field(default_factory=list, max_length=12,
                               description="identifiers of claims about the figures, e.g. '4f2a9c01'")
    bull_case: list[str] = Field(default_factory=list, max_length=12,
                                 description='identifiers of claims supporting the constructive case')
    bear_case: list[str] = Field(default_factory=list, max_length=12,
                                 description='identifiers of claims supporting the sceptical case')
    falsifiers: list[str] = Field(default_factory=list, max_length=6)
    disagreements: list[Disagreement] = Field(default_factory=list, max_length=6)
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
