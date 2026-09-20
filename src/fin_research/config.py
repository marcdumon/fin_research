"""Configuration reading.

Reads `fin_research.toml` into a frozen `Config`, with built-in defaults so the package runs with no config
file at all. Secrets are NOT here: the OpenRouter key comes from the environment, loaded at the CLI edge.

Nested frozen dataclasses rather than one flat bag, because the three concerns (SEC access, storage, model
routing) are read by different layers and a flat namespace would let any of them reach into the others.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fin_research import paths

_DEFAULT_DB = 'fin_research.duckdb'
_DEFAULT_STATE_DB = 'state.sqlite'
_DEFAULT_LOG_LEVEL = 'INFO'

# SEC requires a declaring User-Agent with a contact address; anonymous clients get blocked.
_DEFAULT_USER_AGENT = 'fin_research 7793950+marcdumon@users.noreply.github.com'
_DEFAULT_RATE_LIMIT = 0.11
_DEFAULT_CACHE_TTL_DAYS = 7

_DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
_DEFAULT_CONCURRENCY = 8
_DEFAULT_BUDGET_USD = 2.0

# Tier names are stable; the model behind a tier is expected to churn as prices move.
_DEFAULT_TIERS = {
    'cheap': 'deepseek/deepseek-chat',
    'mid': 'deepseek/deepseek-r1',
    'expensive': 'anthropic/claude-sonnet-5',
}

# Dollars per million tokens, plus each model's context window. Prices are used only to estimate a call's
# cost before sending it -- the provider's reported figure is authoritative -- but the context limit is load
# bearing: it decides where a long filing gets truncated, and it differs by an order of magnitude between
# models on adjacent tiers. Verified against openrouter.ai/api/v1/models.
_DEFAULT_PRICES = {
    'deepseek/deepseek-chat': {'input': 0.32, 'output': 0.89, 'context': 163_840},
    'deepseek/deepseek-r1': {'input': 0.70, 'output': 2.50, 'context': 64_000},
    'anthropic/claude-sonnet-5': {'input': 2.00, 'output': 10.00, 'context': 1_000_000},
}

# Assigned by verifiability, not difficulty: an agent whose output can be checked by code gets a cheap model.
_DEFAULT_AGENT_TIERS = {
    # Cheap: every claim these make must quote the source, and every quote is checked by code.
    'business': 'cheap',
    'risk_factors': 'cheap',
    'management_view': 'cheap',
    'events': 'cheap',
    # Mid: the figures are checkable but reading a trend out of them is not.
    'financials': 'mid',
    'bull': 'mid',
    'bear': 'mid',
    # Expensive: judgement no check can catch, and the one artifact a human reads end to end.
    'valuation': 'expensive',
    'synthesis': 'expensive',
}


@dataclass(frozen=True)
class EdgarConfig:
    """How we talk to SEC EDGAR."""

    user_agent: str = _DEFAULT_USER_AGENT
    rate_limit_seconds: float = _DEFAULT_RATE_LIMIT
    cache_ttl_days: int = _DEFAULT_CACHE_TTL_DAYS

    @property
    def headers(self) -> dict[str, str]:
        """Request headers every SEC call must carry."""
        return {'User-Agent': self.user_agent}


@dataclass(frozen=True)
class LLMConfig:
    """Model routing and spend limits.

    `agents` maps an agent name to a tier name and `tiers` maps a tier name to a concrete model id, so
    swapping every cheap agent onto a new provider is a one-line config change.
    """

    base_url: str = _DEFAULT_BASE_URL
    max_concurrency: int = _DEFAULT_CONCURRENCY
    default_budget_usd: float = _DEFAULT_BUDGET_USD
    # Empty by default: a gated run waits for a human, and an unattended job would otherwise stall silently.
    gate_tiers: tuple[str, ...] = ()
    gate_timeout_seconds: float = 900.0
    tiers: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_TIERS))
    agents: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_AGENT_TIERS))
    prices: dict[str, dict[str, float]] = field(default_factory=lambda: dict(_DEFAULT_PRICES))

    def needs_approval(self, agent: str) -> bool:
        """True if a human must approve this agent before it runs."""
        return self.tier_for(agent) in self.gate_tiers

    def tier_for(self, agent: str) -> str:
        """Tier name assigned to `agent`, defaulting to 'cheap' for anything unlisted."""
        return self.agents.get(agent, 'cheap')

    def model_for(self, agent: str, tier: str | None = None) -> str:
        """Concrete model id for `agent`, or for an explicit `tier` override."""
        resolved = tier or self.tier_for(agent)
        if resolved not in self.tiers:
            raise KeyError(f'unknown model tier {resolved!r}; known tiers: {sorted(self.tiers)}')
        return self.tiers[resolved]


@dataclass(frozen=True)
class Config:
    """Resolved settings for the whole package."""

    data_root: Path = paths.DATA_ROOT
    db: str = _DEFAULT_DB
    state_db: str = _DEFAULT_STATE_DB
    log_level: str = _DEFAULT_LOG_LEVEL
    investalyze_db: Path | None = None
    edgar: EdgarConfig = field(default_factory=EdgarConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def db_path(self) -> Path:
        """Full path to the DuckDB analytical store."""
        return self.data_root / self.db

    @property
    def state_path(self) -> Path:
        """Full path to the SQLite operational store."""
        return self.data_root / self.state_db


def read(path: Path | None = None) -> Config:
    """Read `Config` from a TOML file, falling back to defaults for anything absent.

    A missing or unspecified file yields an all-defaults config, so tests and a fresh clone both work with no
    setup. Relative paths in the file resolve against the file's own directory, not the process cwd, so running
    a CLI from anywhere behaves the same.
    """
    if path is None or not path.exists():
        return Config()

    raw = tomllib.loads(path.read_text())
    base = path.parent

    def _resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    edgar_raw = raw.get('edgar', {})
    llm_raw = raw.get('llm', {})
    investalyze_raw = raw.get('investalyze', {}).get('db_path')

    return Config(
        data_root=_resolve(raw['data_root']) if 'data_root' in raw else paths.DATA_ROOT,
        db=raw.get('db', _DEFAULT_DB),
        state_db=raw.get('state_db', _DEFAULT_STATE_DB),
        log_level=raw.get('log_level', _DEFAULT_LOG_LEVEL),
        investalyze_db=_resolve(investalyze_raw) if investalyze_raw else None,
        edgar=EdgarConfig(
            user_agent=edgar_raw.get('user_agent', _DEFAULT_USER_AGENT),
            rate_limit_seconds=edgar_raw.get('rate_limit_seconds', _DEFAULT_RATE_LIMIT),
            cache_ttl_days=edgar_raw.get('cache_ttl_days', _DEFAULT_CACHE_TTL_DAYS),
        ),
        llm=LLMConfig(
            base_url=llm_raw.get('base_url', _DEFAULT_BASE_URL),
            max_concurrency=llm_raw.get('max_concurrency', _DEFAULT_CONCURRENCY),
            default_budget_usd=llm_raw.get('default_budget_usd', _DEFAULT_BUDGET_USD),
            gate_tiers=tuple(llm_raw.get('gate_tiers', ())),
            gate_timeout_seconds=llm_raw.get('gate_timeout_seconds', 900.0),
            tiers={**_DEFAULT_TIERS, **llm_raw.get('tiers', {})},
            agents={**_DEFAULT_AGENT_TIERS, **llm_raw.get('agents', {})},
            prices={**_DEFAULT_PRICES, **llm_raw.get('prices', {})},
        ),
    )
