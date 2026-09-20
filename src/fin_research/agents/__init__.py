"""The agent roster.

Importing this package registers every agent. Callers previously imported the individual modules themselves
with a noqa comment on each, which worked until someone added an agent and forgot one of the call sites --
the dashboard then listed eight agents while the system ran nine, with nothing to say it was wrong.
"""

from fin_research.agents import analysts, debate, synthesis, triage  # noqa: F401
from fin_research.agents.base import AGENT_DESCRIPTIONS, AGENTS

__all__ = ['AGENTS', 'AGENT_DESCRIPTIONS']
