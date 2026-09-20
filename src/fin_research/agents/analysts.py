"""The agents that read sources and produce findings.

All four run on the cheap tier, which looks wrong until you apply the rule this system is built on: a model's
tier follows how checkable its output is, not how hard the work feels. Pulling risk factors out of a filing
feels harder than writing an investment thesis. It is not, because every claim it makes must quote the filing
and every quote is verified mechanically -- a cheap model's mistakes here are caught by code. Writing a thesis
is easy to do badly and impossible to check, so that goes to an expensive model.

The prompts share a shape: say what the source is, demand a verbatim quote for every factual claim, and say
explicitly that not finding something is an acceptable answer. That last instruction does real work. Without
it a model asked for ten risks will invent the tenth.
"""

from dataclasses import dataclass

from fin_research.agents.base import Analyst, RunContext, register, truncate
from fin_research.core.schemas import FindingSet

CITATION_RULES = """
Every factual or numeric claim must carry evidence: a VERBATIM quote from the source, copied exactly,
long enough to be unambiguous (at least a dozen characters). Set document_id to the id given below.

Rules you must follow:
- Quote exactly. A quote that does not appear in the source word for word is discarded, and the claim with it.
- Never state a figure the source does not contain. Do not calculate, round or convert unless asked.
- If the source does not support something, say so in `unresolved` rather than inventing it.
- Returning few findings is a correct answer when the source says little. Do not pad.
- claim_type: 'fact' for something stated in the source, 'numeric' for a figure, 'inference' for a
  conclusion you drew from stated facts, 'judgement' for an opinion. Be honest about which.
""".strip()


@dataclass
class SourceAnalyst(Analyst):
    """An analyst that reads one named document and reports what it found."""

    task: str = ''
    guidance: str = ''

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose a prompt around one document, naming the id that citations must use."""
        document_id = str(inputs['document_id'])
        text = str(inputs['text'])
        label = str(inputs.get('label', 'the filing'))

        return f"""Company: {ctx.ticker}. Source: {label}. document_id: {document_id}

{self.task}

{self.guidance}

{CITATION_RULES}

--- SOURCE BEGINS ---
{truncate(text, limit)}
--- SOURCE ENDS ---"""


business = register(SourceAnalyst(
    name='business',
    system_prompt='You are an equity analyst reading a company filing. You report only what the document '
                  'states, and you quote it.',
    task='Describe what this company actually does: its products, how it makes money, who its customers are, '
         'what it depends on, and how it competes.',
    guidance='Prefer specifics over adjectives. "Two customers accounted for 38% of revenue" is a finding; '
             '"a strong customer base" is not.',
    schema=FindingSet, stage=1),
    'reads Item 1 and describes the business as the filing states it')


risk_factors = register(SourceAnalyst(
    name='risk_factors',
    system_prompt='You are an equity analyst reading the risk factors of a company filing. You report only '
                  'what the document states, and you quote it.',
    task='Identify the risks that could materially change this company\'s earnings or its ability to operate.',
    guidance='Most risk factors are boilerplate that every filer includes. Prioritise those that are specific '
             'to this company, newly worded, or attached to a number. Ignore generic ones unless the filing '
             'gives them substance. Note in `tags` whether a risk reads as specific or boilerplate.',
    schema=FindingSet, stage=1),
    'reads Item 1A and separates company-specific risks from boilerplate')


management_view = register(SourceAnalyst(
    name='management_view',
    system_prompt='You are an equity analyst reading management\'s discussion of results. You report only '
                  'what the document states, and you quote it.',
    task='Report how management explains its results: what they credit, what they blame, what they say about '
         'margins, costs, demand and outlook.',
    guidance='Pay attention to what is explained away and what is not mentioned at all. Where management '
             'gives a reason for a change, record the reason and the figure together.',
    schema=FindingSet, stage=1),
    'reads Item 7 and reports how management accounts for results')


events = register(SourceAnalyst(
    name='events',
    system_prompt='You are an equity analyst reading a company announcement. You report only what the '
                  'document states, and you quote it.',
    task='Report what this announcement discloses and why it matters to the company\'s value.',
    guidance='Be concrete about figures and dates. If the announcement is routine, say so rather than '
             'manufacturing significance.',
    schema=FindingSet, stage=1),
    'reads material events and press releases')


@dataclass
class FinancialAnalyst(Analyst):
    """Reads the reported statements, which arrive already reconciled.

    Runs a tier up because interpreting a trend is judgement, even though the numbers themselves are checked.
    The statements are handed over as the rendered tables -- about two thousand tokens for all six -- rather
    than as the filing, so this is cheap despite the tier.
    """

    name: str = 'financials'
    stage: int = 2
    schema: type = FindingSet

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose a prompt around the reported statements and the checks they passed."""
        statements = str(inputs['statements'])
        identities = str(inputs.get('identities', 'not checked'))
        document_id = str(inputs['document_id'])

        return f"""Company: {ctx.ticker}. Source: reported financial statements. document_id: {document_id}

These figures are taken from the company's own filed statements and have been checked for internal
consistency. The result of that check was: {identities}

Report what the numbers show: growth, margins, cash generation, balance sheet strength, and how these have
changed. Say which direction each is moving and over what period.

Where you state a figure, it must appear in the source below. Do not compute ratios the source does not
contain unless you show both inputs from the source in the same claim. Mark such a claim 'inference'.

{CITATION_RULES}

--- STATEMENTS BEGIN ---
{truncate(statements, limit)}
--- STATEMENTS END ---"""


financials = register(FinancialAnalyst(
    system_prompt='You are an equity analyst reading reported financial statements. You quote the figures '
                  'you use.'),
    'reads the reported statements and describes what the numbers show')
