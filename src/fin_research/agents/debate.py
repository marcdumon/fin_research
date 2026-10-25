"""The one place agents genuinely respond to each other, deliberately bounded to three calls.

Everywhere else in this system agents exchange typed artifacts and never see each other's reasoning, because
free-running model conversation drifts and agrees with itself. The exception is worth making exactly once: a
bull case and a bear case built from the same evidence, where each side has read the other's argument. That
pressure is what stops a thesis being a list of reasons to buy.

The length is fixed at bull, bear, bull rebuttal. There is no convergence test and no loop, and none should be
added -- an unbounded debate between two models is a bill with no ceiling and no better an answer at the end
of it.

Both sides argue over claims that have already been checked, listed with their identifiers, rather than over
the source text. That keeps the argument grounded in what was actually found without paying to restate the
whole corpus three times -- and it settles what a debater is for. Asking one to quote the filings while
showing it only summaries produced claims citing documents it had never seen, and every one was discarded: a
whole stage of expensive reasoning thrown away for a fault in the prompt rather than in the argument.
"""

from dataclasses import dataclass

from fin_research.agents.base import Analyst, RunContext, claim_pool, register, truncate
from fin_research.core.schemas import Finding, Thesis

DEBATE_RULES = """
You argue from the established claims listed below and nothing else. You have not been shown the filings, so
you cannot quote them: a claim of yours that quotes a source is discarded, because the quotation cannot be
checked. Leave `evidence` empty and name the claims you are reasoning from by their identifiers, in your own
claim text -- 'on [4f2a9c01] and [9b1e2d77], the margin story rests on a contract that expires next year'.

Mark reasoning as claim_type 'inference' and opinion as 'judgement'. Do not restate an established claim as
though it were your own; the note already has it, and your job is what follows from it.

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
        pool, _ = claim_pool(findings)

        if opposing is None:
            return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

Build the strongest honest case that this company is undervalued or improving, from the claims below.

{DEBATE_RULES}

--- ESTABLISHED CLAIMS ---
{truncate(pool, limit)}"""

        return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

You argued the constructive case. The bear case below answers it. Reply once: concede the points that are
right, and rebut the ones that are not. Do not repeat your opening argument.

Set `round` to 2. Where you dispute a specific bear claim, put its claim text in your claim's `attacks` list.
Conceding a point is a correct move and improves your case; refusing to concede anything is a sign you are
arguing rather than analysing.

{DEBATE_RULES}

--- THE BEAR CASE ---
{opposing}

--- ESTABLISHED CLAIMS ---
{truncate(pool, limit)}"""


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
        pool, _ = claim_pool(findings)

        return f"""Company: {ctx.ticker}, as of {ctx.as_of}.

Build the strongest honest case that this company is overvalued, deteriorating, or riskier than it appears,
from the claims below. The constructive case is given; attack it where it is weak.

Each claim must either reason from an established claim the constructive case passed over, or dispute one of
its claims -- put the disputed claim's text in `attacks`. Do not manufacture a disagreement where you
actually agree; saying the constructive case is right on a point is useful information.

{DEBATE_RULES}

--- THE CONSTRUCTIVE CASE ---
{opposing}

--- ESTABLISHED CLAIMS ---
{truncate(pool, limit)}"""


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
