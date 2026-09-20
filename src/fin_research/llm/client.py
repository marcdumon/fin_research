"""The one place a model is ever called.

Every agent in this system goes through `ModelClient.call`, and nothing else imports the provider API. That
single constraint is what makes the rest possible: the cost meter, the live trace a dashboard will read, the
per-run budget, deterministic replay from cache, and the ability to run the entire pipeline in tests without
a key or a cent. Scatter provider calls through the agents and every one of those has to be rebuilt per agent,
and will be rebuilt inconsistently.

Budget is enforced before the request is sent, not after it returns. Metering afterwards tells you what
happened; refusing beforehand is what makes a stated budget a limit. The estimate uses locally configured
prices while the recorded figure is whatever the provider reports, and both are stored, so stale prices show
up as a discrepancy rather than as an overspend.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel

from fin_research.config import Config
from fin_research.core.state import State
from fin_research.llm.parsing import ParseError, ParseMode, coerce, strict_schema
from fin_research.llm.router import ModelChoice, estimate_tokens, resolve

log = logging.getLogger(__name__)

API_KEY_VARIABLE = 'OPENROUTER_API_KEY'
COMPLETIONS_PATH = '/chat/completions'

MAX_ATTEMPTS = 3
BASE_RETRY_DELAY = 1.0
BACKOFF_FACTOR = 2.0
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class ModelCallError(RuntimeError):
    """Raised when a call could not be completed after every attempt."""


@dataclass(frozen=True)
class ModelRequest:
    """What an agent is asking for."""

    agent: str
    system: str
    user: str
    schema: type[BaseModel] | None = None
    tier: str | None = None
    max_tokens: int = 4096
    # Zero by default: research runs should be reproducible, and sampling variation here is noise, not
    # creativity. It also makes the prompt cache worth having.
    temperature: float = 0.0

    @property
    def messages(self) -> list[dict[str, str]]:
        """The request as the chat API expects it."""
        return [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': self.user}]


@dataclass(frozen=True)
class ModelResult:
    """What came back, and what it cost."""

    text: str
    model: str
    call_id: str
    parsed: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    retries: int = 0
    cache_hit: bool = False
    parse_mode: str = ParseMode.STRICT.value
    finish_reason: str | None = None


@dataclass
class ModelClient:
    """Calls models on behalf of agents, metering and recording every one.

    Holds the run it is spending against, so an agent cannot call a model without the cost landing on a
    budget. The HTTP client is injectable purely so tests exercise this code rather than a substitute for it.
    """

    cfg: Config
    state: State
    run_id: str
    http: httpx.AsyncClient | None = None
    api_key: str | None = None
    _owns_http: bool = field(default=False, init=False)

    async def __aenter__(self) -> 'ModelClient':
        """Open an HTTP client if one was not supplied."""
        if self.http is None:
            self.http = httpx.AsyncClient(base_url=self.cfg.llm.base_url, timeout=180.0)
            self._owns_http = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the HTTP client, but only if this instance created it."""
        if self._owns_http and self.http is not None:
            await self.http.aclose()
            self.http = None

    def _key(self) -> str:
        """The API key, from the environment unless one was passed in."""
        key = self.api_key or os.environ.get(API_KEY_VARIABLE)
        if not key:
            raise ModelCallError(
                f'{API_KEY_VARIABLE} is not set. Put it in .env; it is read at the command line edge only.')
        return key

    @staticmethod
    def cache_key(choice: ModelChoice, request: ModelRequest) -> str:
        """Identity of a prompt: same model, same settings, same text means the same answer.

        The schema is part of it because asking for a different shape is a different question.
        """
        schema_name = request.schema.__name__ if request.schema else '-'
        payload = '|'.join([choice.model, f'{request.temperature}', f'{request.max_tokens}',
                            schema_name, request.system, request.user])
        return hashlib.sha256(payload.encode()).hexdigest()

    def _body(self, choice: ModelChoice, request: ModelRequest) -> dict:
        """The request payload, asking for a strict schema when the agent declared one."""
        body: dict[str, Any] = {
            'model': choice.model,
            'messages': request.messages,
            'temperature': request.temperature,
            'max_tokens': request.max_tokens,
            'usage': {'include': True},
        }
        if request.schema is not None:
            body['response_format'] = {
                'type': 'json_schema',
                'json_schema': {'name': request.schema.__name__, 'strict': True,
                                'schema': strict_schema(request.schema)},
            }
        return body

    async def _post(self, body: dict) -> dict:
        """Send one request, retrying on the failures that are worth retrying.

        Rate limiting and transient server errors are retried with exponential backoff; a malformed request or
        a rejected key is not, because repeating it only wastes time and money.
        """
        assert self.http is not None, 'ModelClient must be used as an async context manager'
        headers = {'Authorization': f'Bearer {self._key()}', 'Content-Type': 'application/json'}
        last: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self.http.post(COMPLETIONS_PATH, json=body, headers=headers)
                if response.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(f'retryable status {response.status_code}',
                                                request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as error:
                last = error
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                if status is not None and status not in RETRYABLE_STATUS:
                    raise ModelCallError(f'{status} from provider: {error}') from error
                if attempt < MAX_ATTEMPTS - 1:
                    delay = BASE_RETRY_DELAY * BACKOFF_FACTOR ** attempt
                    log.warning(f'model call failed ({error}); retrying in {delay:.1f}s')
                    await asyncio.sleep(delay)

        raise ModelCallError(f'model call failed after {MAX_ATTEMPTS} attempts: {last}')

    @staticmethod
    def _content(payload: dict) -> tuple[str, str | None]:
        """Text and finish reason from a completions response."""
        choices = payload.get('choices') or [{}]
        message = choices[0].get('message') or {}
        return message.get('content') or '', choices[0].get('finish_reason')

    async def call(self, request: ModelRequest, task_id: str | None = None) -> ModelResult:
        """Run one model call: route it, budget it, send it, parse it, and record it.

        A parse failure is retried once with the validation error appended to the prompt, because telling a
        model exactly what was wrong with its output works far better than asking the same question again.
        """
        choice = resolve(self.cfg, request.agent, request.tier)
        key = self.cache_key(choice, request)

        cached = self.state.cached_response(key)
        if cached is not None:
            return self._finish(choice, request, cached, task_id, cache_hit=True, retries=0, latency_ms=0)

        prompt_tokens = estimate_tokens(request.system + request.user)
        self.state.check_budget(self.run_id, choice.cost(prompt_tokens, request.max_tokens))

        body = self._body(choice, request)
        started = time.monotonic()
        retries = 0

        try:
            payload = await self._post(body)
            text, _ = self._content(payload)

            if request.schema is not None:
                try:
                    coerce(text, request.schema)
                except ParseError as error:
                    retries = 1
                    log.info(f'{request.agent}: reply did not match {request.schema.__name__}; re-prompting')
                    body['messages'] = [*request.messages,
                                        {'role': 'assistant', 'content': text},
                                        {'role': 'user', 'content':
                                         f'That did not validate: {error}. Reply with JSON matching the '
                                         f'schema exactly, and nothing else.'}]
                    payload = await self._post(body)
        except Exception as error:
            latency = int((time.monotonic() - started) * 1000)
            self.state.record_call(self.run_id, request.agent, choice.tier, choice.model, key,
                                   task_id=task_id, latency_ms=latency, retries=retries, error=str(error))
            raise

        self.state.cache_response(key, choice.model, payload)
        latency = int((time.monotonic() - started) * 1000)
        return self._finish(choice, request, payload, task_id, cache_hit=False, retries=retries,
                            latency_ms=latency)

    def _finish(self, choice: ModelChoice, request: ModelRequest, payload: dict, task_id: str | None,
                *, cache_hit: bool, retries: int, latency_ms: int) -> ModelResult:
        """Parse a response, price it, and write its ledger row.

        A cached response costs nothing but is still recorded, so the trace shows every step a run took rather
        than only the ones that were paid for.
        """
        text, finish_reason = self._content(payload)
        usage = payload.get('usage') or {}
        prompt_tokens = int(usage.get('prompt_tokens') or 0)
        completion_tokens = int(usage.get('completion_tokens') or 0)

        # The provider's own figure wins when present; the local price table is only a fallback for budgeting.
        reported = usage.get('cost')
        cost = 0.0 if cache_hit else float(reported if reported is not None
                                           else choice.cost(prompt_tokens, completion_tokens))

        parsed: Any = None
        mode = ParseMode.STRICT
        error: str | None = None
        if request.schema is not None:
            try:
                parsed, mode = coerce(text, request.schema)
            except ParseError as failure:
                mode = ParseMode.FAILED
                error = str(failure)
                log.warning(f'{request.agent}: {failure}')

        call_id = self.state.record_call(
            self.run_id, request.agent, choice.tier, choice.model, self.cache_key(choice, request),
            task_id=task_id, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_usd=cost, latency_ms=latency_ms, retries=retries, cache_hit=cache_hit,
            parse_mode=mode.value, finish_reason=finish_reason, error=error)

        return ModelResult(text=text, model=choice.model, call_id=call_id, parsed=parsed,
                           prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                           cost_usd=cost, latency_ms=latency_ms, retries=retries, cache_hit=cache_hit,
                           parse_mode=mode.value, finish_reason=finish_reason)


def response_payload(content: str, *, prompt_tokens: int = 100, completion_tokens: int = 50,
                     cost: float | None = None, finish_reason: str = 'stop') -> dict:
    """Build a completions response body. Used by tests, and to document the shape this client expects."""
    usage: dict[str, Any] = {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}
    if cost is not None:
        usage['cost'] = cost
    return {'choices': [{'message': {'role': 'assistant', 'content': content},
                         'finish_reason': finish_reason}], 'usage': usage}


def json_dumps(model: BaseModel) -> str:
    """Serialise an artifact the way a model would return it."""
    return json.dumps(model.model_dump(mode='json'))
