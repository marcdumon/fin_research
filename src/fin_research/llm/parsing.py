"""Coerce a model's reply into a typed artifact, down a ladder of increasingly forgiving attempts.

Cheap models are the point of this system and they are unreliable at structured output. A provider may
advertise JSON-schema support and still route a request to a backend that silently degrades to best-effort
JSON; models wrap their answer in a code fence, prefix it with 'Here is the JSON:', or emit trailing commas.
Treating any of that as a failed call would throw away a usable answer and pay for a retry.

So parsing descends: strict JSON first, then a fenced block, then the first balanced object in the text. Which
rung succeeded is recorded on every call, because a model drifting down the ladder is a signal worth seeing
before it becomes a cost problem.
"""

import json
import logging
import re
from enum import StrEnum

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_FENCED = re.compile(r'```(?:json)?\s*(.+?)```', re.DOTALL | re.IGNORECASE)
# Trailing commas before a closing brace or bracket are the single most common malformation.
_TRAILING_COMMA = re.compile(r',\s*([}\]])')


class ParseMode(StrEnum):
    """Which rung of the ladder produced a usable object."""

    STRICT = 'strict'
    FENCED = 'fenced'
    EMBEDDED = 'embedded'
    FAILED = 'failed'


class ParseError(ValueError):
    """Raised when no rung of the ladder yields an object matching the schema."""


def _balanced_object(text: str) -> str | None:
    """First balanced `{...}` in `text`, ignoring braces inside strings.

    A brace counter alone breaks on any JSON containing a brace in a quoted value, which filing text does
    constantly, so string state is tracked explicitly.
    """
    start = text.find('{')
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        character = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return text[start:position + 1]
    return None


def candidates(text: str) -> list[tuple[ParseMode, str]]:
    """Every substring of a reply that might be the intended JSON, best first."""
    stripped = text.strip()
    found: list[tuple[ParseMode, str]] = [(ParseMode.STRICT, stripped)]

    fenced = _FENCED.search(text)
    if fenced:
        found.append((ParseMode.FENCED, fenced.group(1).strip()))

    embedded = _balanced_object(text)
    if embedded and embedded != stripped:
        found.append((ParseMode.EMBEDDED, embedded))

    return found


def coerce(text: str, schema: type[BaseModel]) -> tuple[BaseModel, ParseMode]:
    """Parse `text` into `schema`, returning the object and how it had to be extracted.

    Raises `ParseError` carrying the validation message when nothing works, so the caller can put that message
    back in front of the model on retry -- telling it what was wrong is far more effective than asking again.
    """
    last_error: Exception | None = None

    for mode, candidate in candidates(text):
        for attempt in (candidate, _TRAILING_COMMA.sub(r'\1', candidate)):
            try:
                return schema.model_validate(json.loads(attempt)), mode
            except (json.JSONDecodeError, ValidationError) as error:
                last_error = error

    raise ParseError(f'could not parse a {schema.__name__} from the reply: {last_error}')
