"""The one place agents genuinely respond to each other, deliberately bounded to three calls.

Everywhere else in this system agents exchange typed artifacts and never see each other's reasoning, because
free-running model conversation drifts and agrees with itself. The exception is worth making exactly once: a
bull case and a bear case built from the same evidence, where each side has read the other's argument. That
pressure is what stops a thesis being a list of reasons to buy.

The length is fixed at bull, bear, bull rebuttal. There is no convergence test and no loop, and none should be
added -- an unbounded debate between two models is a bill with no ceiling and no better an answer at the end
of it. Both sides work from finding headlines rather than full evidence text, which keeps the argument
grounded in what was actually found without paying to restate the whole corpus three times.
"""

from dataclasses import dataclass

from fin_research.agents.base import Analyst, RunContext, findings_digest, register
from fin_research.core.schemas import Finding, Thesis

DEBATE_RULES = """
Every claim you make must carry evidence, quoting the source verbatim with its document_id, exactly as the
findings below do. A claim whose quote cannot be found in the source is discarded.

Mark opinions as claim_type 'judgement' and reasoning as 'inference'. Do not dress an opinion as a fact.
In `falsifiers`, name the specific, observable conditions that would show your case to be wrong. Be concrete:
"gross margin falls below 40% for two consecutive quarters" is a falsifier; "if things get worse" is not.
""".strip()


@dataclass
class BullAnalyst(Analyst):
    """Argues the constructive case from the evidence gathered."""

    name: str = 'bull'
    stage: int = 3
    output_type: str = 'Thesis'
    schema: type = Thesis

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose the opening argument, or the rebuttal when the bear case is supplied."""
        findings: list[Finding] = list(inputs['findings'])  # type: ignore[arg-type]
        opposing = inputs.get('opposing')

        if opposing is None:
            return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

Build the strongest honest case that this company is undervalued or improving, using only the findings below.

{DEBATE_RULES}

--- FINDINGS ---
{findings_digest(findings, limit=20)}"""

        return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

You argued the constructive case. The bear case below answers it. Reply once: concede the points that are
right, and rebut the ones that are not. Do not repeat your opening argument.

Set `round` to 2. Where you dispute a specific bear claim, put its claim text in your claim's `attacks` list.
Conceding a point is a correct move and improves your case; refusing to concede anything is a sign you are
arguing rather than analysing.

{DEBATE_RULES}

--- THE BEAR CASE ---
{opposing}

--- FINDINGS ---
{findings_digest(findings, limit=20)}"""


@dataclass
class BearAnalyst(Analyst):
    """Argues the sceptical case, having read the bull case."""

    name: str = 'bear'
    stage: int = 4
    output_type: str = 'Thesis'
    schema: type = Thesis

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose the sceptical case against the argument already made."""
        findings: list[Finding] = list(inputs['findings'])  # type: ignore[arg-type]
        opposing = inputs.get('opposing', '')

        return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

Build the strongest honest case that this company is overvalued, deteriorating, or riskier than it appears,
using only the findings below. The constructive case is given; attack it where it is weak.

Each claim must either cite evidence the constructive case ignored, or dispute one of its claims -- put the
disputed claim's text in `attacks`. Do not manufacture a disagreement where you actually agree; saying the
constructive case is right on a point is useful information.

{DEBATE_RULES}

--- THE CONSTRUCTIVE CASE ---
{opposing}

--- FINDINGS ---
{findings_digest(findings, limit=20)}"""


bull = register(BullAnalyst(
    system_prompt='You are an equity analyst arguing the constructive case. You are rigorous, you quote your '
                  'sources, and you would rather be right than persuasive.'),
    'argues the constructive case, and answers the sceptical one once')

bear = register(BearAnalyst(
    system_prompt='You are an equity analyst arguing the sceptical case. You are rigorous, you quote your '
                  'sources, and you attack arguments rather than assert conclusions.'),
    'argues the sceptical case against the constructive one')


def thesis_digest(thesis: Thesis | None) -> str:
    """Render a thesis compactly enough to hand to the other side."""
    if thesis is None:
        return '(none)'
    claims = '\n'.join(f'- [{c.claim_type.value}] {c.text}' for c in thesis.claims[:12])
    falsifiers = '\n'.join(f'- {f}' for f in thesis.falsifiers[:6])
    return f'{thesis.summary}\n\nClaims:\n{claims}\n\nWould be wrong if:\n{falsifiers or "- (none given)"}'
