"""What an agent is, and the one path every agent's work takes.

An agent is a prompt, an output schema and a tier. It does not choose a model, does not call a provider, does
not decide what to do next, and never speaks to another agent. It is handed typed input and returns typed
output; the supervisor routes and the verifier checks. Keeping agents this thin is what makes them cheap to
add, possible to test without a network, and impossible to have quietly drift into conversation with each
other.

The shared run in `Analyst.run` is deliberately not overridable in the normal case. Every agent builds a
prompt, calls the one client, parses into its schema, and has its claims checked against the sources before
anything downstream sees them. An agent that could skip the check would be an agent whose output nobody can
trust, so the check is in the base class rather than left to each implementation to remember.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from pydantic import BaseModel

from fin_research.config import Config
from fin_research.core.passages import PassageIndex
from fin_research.core.schemas import Claim, Finding, FindingSet, Thesis
from fin_research.core.state import State
from fin_research.core.verification import FactIndex, SourceText, VerificationReport, surviving_findings, verify_claims
from fin_research.llm.client import ModelClient, ModelRequest
from fin_research.llm.router import estimate_tokens, resolve

log = logging.getLogger(__name__)

# A section longer than this is truncated before being sent. A large bank's filing runs past a cheap model's
# context entirely, so something has to give; losing the tail of one section is better than losing the run.
DEFAULT_MAX_INPUT_TOKENS = 100_000

# Enough for a set of findings. Agents producing a valuation or a whole report override it upward.
DEFAULT_MAX_OUTPUT_TOKENS = 4_096


@dataclass
class RunContext:
    """Everything an agent is allowed to reach.

    Agents get no database connection and no HTTP client of their own. They see the text they are meant to
    read and the one client that meters spend, which is what keeps every call on the ledger.
    """

    cfg: Config
    state: State
    client: ModelClient
    run_id: str
    ticker: str
    as_of: date
    sources: dict[str, SourceText] = field(default_factory=dict)
    passages: dict[str, PassageIndex] = field(default_factory=dict)
    # What the company actually reported. Without it a figure is only checked against the sentence it was
    # quoted from, which a model can satisfy while stating the wrong number.
    facts: FactIndex | None = None

    def add_source(self, document_id: str, text: str) -> None:
        """Register a document that agents may cite, numbering its passages."""
        self.sources[document_id] = SourceText(text)
        self.passages[document_id] = PassageIndex.build(document_id, text)

    def numbered(self, document_id: str) -> str:
        """The document as the model should see it: passages, numbered."""
        index = self.passages.get(document_id)
        return index.render() if index else ''


@dataclass(frozen=True)
class AgentResult:
    """An agent's output after checking, with what was dropped on the way.

    `artifact` is None when the model returned nothing this agent's schema could accept. A placeholder built
    without validation used to stand in its place, and every reader of it raised AttributeError on the first
    required field -- a run that had produced a full set of findings and a complete debate died rendering the
    note. Absent is the honest answer, and the callers already handle it: the note simply says that stage
    produced nothing.
    """

    agent: str
    artifact: BaseModel | None
    verification: VerificationReport
    cost_usd: float
    cache_hit: bool

    @property
    def groundedness(self) -> float | None:
        """Share of this agent's checkable claims that survived."""
        return self.verification.groundedness


class Agent(Protocol):
    """The shape every agent shares."""

    name: str
    stage: int
    output_type: str

    async def run(self, ctx: RunContext, tier: str | None = None, **inputs: object) -> AgentResult:
        """Do this agent's work and return its typed output."""
        ...


def truncate(text: str, max_tokens: int = DEFAULT_MAX_INPUT_TOKENS) -> str:
    """Cut text to fit a model's context, saying so in the text itself.

    The marker matters: a model handed a silently truncated filing will reason confidently about a document
    it only partly saw, and may cite the missing part from memory. Telling it what happened means it can say
    the source did not answer the question.
    """
    if estimate_tokens(text) <= max_tokens:
        return text
    cutoff = max_tokens * 4
    return (text[:cutoff] + '\n\n[TRUNCATED: this source was longer than the context available. '
            'Do not draw conclusions about the part you cannot see.]')


@dataclass
class Analyst:
    """Base for an agent that reads sources and returns findings.

    Subclasses supply a name, a system prompt and a way to build the user prompt. Everything else -- routing,
    metering, parsing, verification, dropping ungrounded claims -- happens here, identically for all of them.
    """

    name: str = 'analyst'
    stage: int = 1
    output_type: str = 'FindingSet'
    system_prompt: str = ''
    schema: type[BaseModel] = FindingSet
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    # Room for the reply. A truncated response is not a refusal -- it is valid JSON up to the cut, which then
    # fails to parse and is retried at full price. The agents producing the largest artifacts need the most.
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose the user prompt, fitting sources into `limit` tokens.

        The limit is passed in rather than read from the instance because agents are shared singletons and
        several run concurrently; storing it on self would let one agent's window leak into another's.
        """
        raise NotImplementedError

    def _check(self, artifact: BaseModel, ctx: RunContext) -> tuple[BaseModel, VerificationReport]:
        """Drop any claim whose citations do not resolve, or whose figure contradicts the statements.

        Done here rather than at report time so an expensive model never reads, and never builds on, a claim
        that failed. A finding left with no surviving claims is discarded entirely.
        """
        if isinstance(artifact, FindingSet):
            kept, report = surviving_findings(list(artifact.findings), ctx.sources, ctx.facts, ctx.passages)
            identified = [f.model_copy(update={'claims': [identify(c) for c in f.claims]}) for f in kept]
            return artifact.model_copy(update={'findings': identified}), report

        claims = getattr(artifact, 'claims', None)
        if isinstance(claims, list) and all(isinstance(c, Claim) for c in claims):
            report = verify_claims(claims, ctx.sources, ctx.facts, ctx.passages)
            return artifact.model_copy(
                update={'claims': [identify(v.claim) for v in report.grounded]}), report

        return artifact, VerificationReport(verdicts=())

    async def run(self, ctx: RunContext, task_id: str | None = None, tier: str | None = None,
                  **inputs: object) -> AgentResult:
        """Build the prompt, call the model, parse it, and return only what survived checking.

        `tier` overrides the agent's configured one, which is how a decision taken at an approval gate to run
        a step on something cheaper actually changes which model is used.
        """
        # The window follows the model, not a constant: a reasoning model on the middle tier has a quarter
        # the context of the cheap model beside it, and a fixed limit would silently overflow it.
        choice = resolve(ctx.cfg, self.name, tier)
        limit = min(self.max_input_tokens, choice.max_source_tokens)

        request = ModelRequest(agent=self.name, system=self.system_prompt,
                               user=self.build_prompt(ctx, limit, **inputs), schema=self.schema,
                               max_tokens=self.max_output_tokens, tier=tier)
        result = await ctx.client.call(request, task_id=task_id)

        if result.parsed is None:
            log.warning(f'{self.name}: model returned nothing this schema accepts; the stage produced nothing')
            return AgentResult(agent=self.name, artifact=None, cost_usd=result.cost_usd,
                               cache_hit=result.cache_hit, verification=VerificationReport(verdicts=()))

        checked, report = self._check(result.parsed, ctx)
        dropped = len(report.rejected)
        if dropped:
            log.info(f'{self.name}: dropped {dropped} unverifiable claim(s) of {len(report.checkable)}')

        return AgentResult(agent=self.name, artifact=checked, verification=report,
                           cost_usd=result.cost_usd, cache_hit=result.cache_hit)


def identify(claim: Claim) -> Claim:
    """Give a claim a stable identifier derived from its text.

    Derived rather than random so the same claim made twice collapses to one reference, and so an identifier
    survives a re-run of the same stage.

    Whatever the model put in the field is overwritten. Structured output requires every property, so a model
    fills `claim_id` whether or not it has anything to put there, and it reaches for 'c1'. Two agents doing
    that independently would hand two different claims the same identifier, and the report would then cite
    one while displaying the other.
    """
    from fin_research.ingest.store import digest

    return claim.model_copy(update={'claim_id': digest(claim.text)[:8]})


def claim_pool(findings: list[Finding],
               theses: list[Thesis | None] | None = None) -> tuple[str, dict[str, Claim]]:
    """Every claim that survived verification, listed with its identifier.

    An agent downstream of verification argues over these rather than writing claims of its own. Everything
    here has already been checked against a source, so an argument built from these identifiers inherits that
    check; an argument that restated them would be attaching fresh citations to work it did not do.
    """
    pool: dict[str, Claim] = {}
    for finding in findings:
        for claim in finding.claims:
            if claim.claim_id:
                pool[claim.claim_id] = claim
    for thesis in (theses or []):
        for claim in (thesis.claims if thesis else []):
            if claim.claim_id:
                pool[claim.claim_id] = claim

    lines = [f'[{claim.claim_id}] ({claim.claim_type.value}/{claim.polarity.value}) {claim.text}'
             for claim in pool.values()]
    return '\n'.join(lines), pool


def findings_digest(findings: list[Finding], limit: int = 12) -> str:
    """One line per finding, for handing to a later agent without resending everything.

    The debate stage is where token cost quietly returns: three expensive calls each tempted to carry the full
    evidence set. Passing headlines keeps the argument grounded in what was found without paying to restate it.
    """
    lines = []
    for finding in findings[:limit]:
        claims = '; '.join(claim.text for claim in finding.claims[:3])
        lines.append(f'- {finding.headline}' + (f' ({claims})' if claims else ''))
    return '\n'.join(lines)


AGENTS: dict[str, Agent] = {}
AGENT_DESCRIPTIONS: dict[str, str] = {}


def register(agent: Agent, description: str) -> Agent:
    """Add an agent to the roster, following the registry pattern used across this codebase."""
    AGENTS[agent.name] = agent
    AGENT_DESCRIPTIONS[agent.name] = description
    return agent
