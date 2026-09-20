"""Choose which model an agent runs on, and price the call before making it.

Agents never name a model. They declare a tier, the tier maps to a model id in configuration, and swapping
every cheap agent onto a different provider is a one-line change. Model identifiers and prices churn faster
than code, so neither is written here.

Prices exist locally only to estimate a call's cost *before* it is sent, which is what makes the budget a
limit rather than a report. The authoritative figure is whatever the provider reports afterwards, and the
two are recorded separately so a drift between them shows up as stale configuration rather than silent
overspend.
"""

import logging
from dataclasses import dataclass

from fin_research.config import Config

log = logging.getLogger(__name__)

# Fallback prices in dollars per million tokens, used only when configuration does not give one. Deliberately
# pessimistic: a too-high estimate stops a run early, while a too-low one lets it overspend.
DEFAULT_PRICES = {'input': 1.0, 'output': 3.0}

# Conservative when a model's window is unknown: truncating too early loses text, but overflowing
# wastes the whole call.
DEFAULT_CONTEXT = 32_000

# Left for the reply and for the prompt's own scaffolding, so the source never fills the window.
CONTEXT_HEADROOM = 8_000

CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ModelChoice:
    """The model an agent will use, with the prices needed to budget for it."""

    agent: str
    tier: str
    model: str
    input_price: float
    output_price: float
    context: int = DEFAULT_CONTEXT

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Dollar cost of a call of this size."""
        return (prompt_tokens * self.input_price + completion_tokens * self.output_price) / 1_000_000

    @property
    def max_source_tokens(self) -> int:
        """How much source text this model can be given, once the reply and scaffolding are allowed for.

        Follows the model rather than a constant, because tiers differ enormously: one reasoning model on the
        middle tier has a 64k window while the cheap model beside it has 164k. A fixed limit would silently
        overflow the smaller one.
        """
        return max(self.context - CONTEXT_HEADROOM, 4_000)


def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting. Four characters per token is close enough to refuse a call."""
    return max(len(text) // CHARS_PER_TOKEN, 1)


def resolve(cfg: Config, agent: str, tier: str | None = None) -> ModelChoice:
    """Work out which model `agent` runs on, and what it costs.

    An explicit `tier` overrides the agent's configured one, which is how a single expensive step can be
    escalated without changing anyone's defaults.
    """
    resolved_tier = tier or cfg.llm.tier_for(agent)
    model = cfg.llm.model_for(agent, resolved_tier)
    prices = cfg.llm.prices.get(model, {})

    if not prices:
        log.debug(f'no price configured for {model}; estimating with defaults')

    return ModelChoice(
        agent=agent, tier=resolved_tier, model=model,
        input_price=prices.get('input', DEFAULT_PRICES['input']),
        output_price=prices.get('output', DEFAULT_PRICES['output']),
        context=int(prices.get('context', DEFAULT_CONTEXT)))
