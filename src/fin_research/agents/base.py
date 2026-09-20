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
from fin_research.core.schemas import Claim, Finding, FindingSet
from fin_research.core.state import State
from fin_research.core.verification import SourceText, VerificationReport, surviving_findings, verify_claims
from fin_research.llm.client import ModelClient, ModelRequest
from fin_research.llm.router import estimate_tokens, resolve

log = logging.getLogger(__name__)

# A section longer than this is truncated before being sent. A large bank's filing runs past a cheap model's
# context entirely, so something has to give; losing the tail of one section is better than losing the run.
DEFAULT_MAX_INPUT_TOKENS = 100_000


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

    def add_source(self, document_id: str, text: str) -> None:
        """Register a document that agents may cite."""
        self.sources[document_id] = SourceText(text)


@dataclass(frozen=True)
class AgentResult:
    """An agent's output after checking, with what was dropped on the way."""

    agent: str
    artifact: BaseModel
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

    async def run(self, ctx: RunContext, **inputs: object) -> AgentResult:
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

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose the user prompt, fitting sources into `limit` tokens.

        The limit is passed in rather than read from the instance because agents are shared singletons and
        several run concurrently; storing it on self would let one agent's window leak into another's.
        """
        raise NotImplementedError

    def _check(self, artifact: BaseModel, ctx: RunContext) -> tuple[BaseModel, VerificationReport]:
        """Drop any claim whose citations do not resolve, before the artifact goes anywhere.

        Done here rather than at report time so an expensive model never reads, and never builds on, a claim
        that failed. A finding left with no surviving claims is discarded entirely.
        """
        if isinstance(artifact, FindingSet):
            kept, report = surviving_findings(list(artifact.findings), ctx.sources)
            return artifact.model_copy(update={'findings': kept}), report

        claims = getattr(artifact, 'claims', None)
        if isinstance(claims, list) and all(isinstance(c, Claim) for c in claims):
            report = verify_claims(claims, ctx.sources)
            return artifact.model_copy(update={'claims': [v.claim for v in report.grounded]}), report

        return artifact, VerificationReport(verdicts=())

    async def run(self, ctx: RunContext, task_id: str | None = None, **inputs: object) -> AgentResult:
        """Build the prompt, call the model, parse it, and return only what survived checking."""
        # The window follows the model, not a constant: a reasoning model on the middle tier has a quarter
        # the context of the cheap model beside it, and a fixed limit would silently overflow it.
        choice = resolve(ctx.cfg, self.name)
        limit = min(self.max_input_tokens, choice.max_source_tokens)

        request = ModelRequest(agent=self.name, system=self.system_prompt,
                               user=self.build_prompt(ctx, limit, **inputs), schema=self.schema)
        result = await ctx.client.call(request, task_id=task_id)

        if result.parsed is None:
            log.warning(f'{self.name}: model returned nothing usable; treating as an empty result')
            artifact: BaseModel = self.schema.model_construct()
            return AgentResult(agent=self.name, artifact=artifact, cost_usd=result.cost_usd,
                               cache_hit=result.cache_hit, verification=VerificationReport(verdicts=()))

        checked, report = self._check(result.parsed, ctx)
        dropped = len(report.rejected)
        if dropped:
            log.info(f'{self.name}: dropped {dropped} unverifiable claim(s) of {len(report.checkable)}')

        return AgentResult(agent=self.name, artifact=checked, verification=report,
                           cost_usd=result.cost_usd, cache_hit=result.cache_hit)


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
