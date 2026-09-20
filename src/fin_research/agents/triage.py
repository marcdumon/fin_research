"""The middle stage of the funnel: a cheap read that decides what deserves an expensive one.

A screen narrows thousands of companies to tens on numbers alone, and a deep dive costs real money per name.
Triage sits between them: one cheap call per survivor, reading the business description and the risk factors,
answering only whether this is worth an hour of expensive attention and what the deep dive should settle.

It runs on the cheap tier, which is the same rule applied everywhere here -- its claims must quote the filing
and every quote is checked, so a cheap model's errors are caught by code rather than carried forward.

The prompt says plainly that rejecting is a good outcome. A model asked to assess thirty companies will
otherwise find something interesting in all thirty, which would defeat the point of having a funnel at all.
"""

from dataclasses import dataclass

from fin_research.agents.base import Analyst, RunContext, register, truncate
from fin_research.core.schemas import Triage


@dataclass
class TriageAnalyst(Analyst):
    """Decides whether one company earns a deep dive."""

    name: str = 'triage'
    stage: int = 0
    output_type: str = 'Triage'
    schema: type = Triage

    def build_prompt(self, ctx: RunContext, limit: int, **inputs: object) -> str:
        """Compose a prompt around one candidate's numbers and its own description of itself."""
        document_id = str(inputs['document_id'])
        text = str(inputs['text'])
        numbers = str(inputs.get('numbers', 'not available'))
        reason = str(inputs.get('screen_reason', 'a quantitative screen'))

        return f"""Company: {ctx.ticker}. It reached you through {reason}. document_id: {document_id}

Its reported figures:
{numbers}

Decide whether this company is worth a full research deep dive, which is expensive. Answer with one verdict:

- 'escalate' -- there is something here worth the cost, and you can say what.
- 'reject'   -- the numbers are explained by something that rules it out: a business in decline, a one-off
                gain flattering the figures, an industry the screen mismeasures.
- 'pass'     -- you genuinely cannot tell from what you were given. This is a real answer, not a hedge.

Rejecting is a good outcome. Most companies that pass a numeric screen do not deserve deep research, and a
funnel that escalates everything has not narrowed anything. Do not manufacture interest.

In `open_questions`, say what the deep dive should settle. That is the most valuable thing you produce: it
tells the expensive stage where to look.

Every factual claim must quote the source verbatim, setting document_id to {document_id}. A quote that does
not appear word for word is discarded along with its claim.

--- WHAT THE COMPANY SAYS ABOUT ITSELF ---
{truncate(text, limit)}
--- ENDS ---"""


triage = register(TriageAnalyst(
    system_prompt='You are an analyst deciding what deserves expensive attention. You are sceptical, you '
                  'quote your sources, and you consider rejecting a company a useful result.'),
    'reads a screened candidate and decides whether it earns a deep dive')
