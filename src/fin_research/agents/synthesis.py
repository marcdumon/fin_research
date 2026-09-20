"""Valuation and the final write-up: the two places an expensive model earns its price.

Both produce work no mechanical check can validate. A valuation is only as good as its assumptions, and
choosing them is judgement; a research note is judgement end to end. Everything these two agents read has
already been verified, so the expensive model spends its context on reasoning rather than on material that
may not survive checking.

The synthesiser also arbitrates. After the debate, some claims are disputed and some of those disputes cannot
be settled on the evidence available. Those are not quietly dropped -- they go into the report as unresolved
disagreements. An analyst who knows what is genuinely contested is better served than one handed a false
consensus.
"""

from dataclasses import dataclass

from fin_research.agents.base import Analyst, RunContext, findings_digest, register
from fin_research.agents.debate import thesis_digest
from fin_research.core.schemas import Finding, Report, Thesis, Valuation


@dataclass
class ValuationAnalyst(Analyst):
    """Turns verified figures into scenarios, stating what each one assumes."""

    name: str = 'valuation'
    stage: int = 5
    output_type: str = 'Valuation'
    schema: type = Valuation

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose a prompt demanding scenarios and explicit assumptions, not a point estimate."""
        findings: list[Finding] = list(inputs['findings'])  # type: ignore[arg-type]
        price = inputs.get('price')
        price_line = f'Current share price: {price}' if price else 'Current share price: not available'

        return f"""Company: {ctx.ticker}, as of {ctx.as_of}. {price_line}

Value this company. Give a bear, base and bull scenario with a value per share and a probability for each,
and state every assumption you used with the reason for it.

A single number would hide the only interesting part, which is what has to be true for it to hold. So:
- Each assumption needs a name, a value, a unit and a rationale. Where the rationale rests on something in
  the findings, quote it as evidence.
- Probabilities should reflect genuine uncertainty. Three scenarios all at roughly a third is usually a sign
  you have not thought about which is likelier.
- Name the valuation method you used in `method`. If the available information does not support a credible
  valuation, say so in the method field and give wide scenarios rather than false precision.

--- FINDINGS ---
{findings_digest(findings, limit=20)}"""


@dataclass
class Synthesiser(Analyst):
    """Writes the report, and rules on what the two sides could not settle."""

    name: str = 'synthesis'
    stage: int = 6
    output_type: str = 'Report'
    schema: type = Report

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose the final prompt from the verified findings, both theses and the valuation."""
        findings: list[Finding] = list(inputs['findings'])  # type: ignore[arg-type]
        bull: Thesis | None = inputs.get('bull')  # type: ignore[assignment]
        bear: Thesis | None = inputs.get('bear')  # type: ignore[assignment]
        rebuttal: Thesis | None = inputs.get('rebuttal')  # type: ignore[assignment]
        valuation = inputs.get('valuation')

        return f"""Company: {ctx.ticker}. Write the research note as of {ctx.as_of}.

Everything below has already been checked: every claim's quotation was confirmed against the filing. Claims
that failed have been removed, so an argument you expected to see may be absent because it could not be
supported.

Your job:
1. State a thesis, with a direction and an honest confidence level.
2. Carry forward the strongest claims from each side, keeping their evidence intact.
3. Rule on the disputes. Where the two sides read the same evidence differently, decide -- and where the
   evidence genuinely does not settle it, record it as an unresolved disagreement with `resolved` false
   rather than picking a side for the sake of tidiness.
4. State the falsifiers: what would have to become true for this thesis to be wrong.

Confidence must reflect the evidence. If the sources were thin, or the debate exposed a real hole, say
'low'. A confident note on weak evidence is worse than no note.

--- VERIFIED FINDINGS ---
{findings_digest(findings, limit=20)}

--- CONSTRUCTIVE CASE ---
{thesis_digest(bull)}

--- SCEPTICAL CASE ---
{thesis_digest(bear)}

--- CONSTRUCTIVE REBUTTAL ---
{thesis_digest(rebuttal)}

--- VALUATION ---
{valuation if valuation else '(none)'}"""


valuation = register(ValuationAnalyst(
    system_prompt='You are an equity analyst valuing a company. You state your assumptions explicitly and '
                  'you prefer honest ranges to false precision.'),
    'produces valuation scenarios with their assumptions stated')

synthesis = register(Synthesiser(
    system_prompt='You are a senior analyst writing the final research note. You weigh both sides, you are '
                  'candid about uncertainty, and you never assert more than the evidence supports.'),
    'writes the research note and rules on what the debate left unresolved')
