"""One prompt to one model, the way the lattice build sends it.

``ModelCall`` is what the build calls: a model string, the prompt and, for a
structured reply, the JSON schema the reply must follow; it returns the reply's
text and token counts. ``litellm_call`` is the one that sends the prompt
through ``litellm.acompletion`` as ``annotate.label.completion_params`` does:
``api_base`` points an ``openai/`` model at a server of its own, a ``gemini/``
model is sent the schema as ``response_schema``, any other model as an OpenAI
``json_schema`` response format. Tests pass a ``ModelCall`` of their own.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Awaitable, Callable, Optional

import litellm
from pydantic import BaseModel

from annotate.label import LOCAL_SERVER_KEY, PROVIDER_BUSY, retry_after_seconds
from juno.config import LatticeConfig

logger = logging.getLogger(__name__)


class Reply(BaseModel):
    """What a model call came back with."""

    text: str
    # From the provider's usage report; None when it gave none.
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


ModelCall = Callable[[str, str, Optional[dict]], Awaitable[Reply]]


def completion_params(model: str, prompt: str, max_tokens: int, timeout_seconds: float,
                      api_base: Optional[str], schema: Optional[dict]) -> dict:
    """The ``litellm.acompletion`` arguments for ``prompt`` as one user message."""
    api_key = os.environ.get("OPENAI_API_KEY", LOCAL_SERVER_KEY) if api_base is not None else None
    params = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "timeout": timeout_seconds,
        "api_base": api_base,
        "api_key": api_key,
    }
    if schema is None:
        return params
    if model.startswith("gemini/"):
        params["response_schema"] = schema
        params["response_mime_type"] = "application/json"
    else:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "reply", "schema": schema},
        }
    return params


def busy(exc: BaseException) -> bool:
    """Whether ``exc`` says the provider could not take the call just then."""
    return isinstance(exc, (*PROVIDER_BUSY, TimeoutError))


def reply_of(completion: object) -> Reply:
    """The text and token counts of a litellm completion."""
    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    return Reply(text=text,
                 prompt_tokens=getattr(usage, "prompt_tokens", None),
                 completion_tokens=getattr(usage, "completion_tokens", None))


def litellm_call(config: LatticeConfig) -> ModelCall:
    """A ``ModelCall`` that sends prompts with ``config``'s endpoint, token and time limits.

    A call the provider could not take (``annotate.label.PROVIDER_BUSY``, a
    timeout) is made again up to ``config.max_attempts`` times, waiting
    ``config.retry_base_seconds * 2 ** n`` (at most ``config.retry_max_seconds``,
    jittered, or longer if the provider asked) between them. Any other error is
    raised as it is.
    """

    async def call(model: str, prompt: str, schema: Optional[dict]) -> Reply:
        params = completion_params(model, prompt, config.max_tokens, config.timeout_seconds,
                                   config.api_base, schema)
        for attempt in range(1, config.max_attempts + 1):
            try:
                return reply_of(await litellm.acompletion(**params))
            except Exception as exc:
                if not busy(exc) or attempt == config.max_attempts:
                    raise
                backoff = min(config.retry_max_seconds,
                              config.retry_base_seconds * 2 ** (attempt - 1))
                wait = max(backoff * random.uniform(0.5, 1.5), retry_after_seconds(exc) or 0.0)
                logger.warning("%s: attempt %d of %d failed (%s); retrying in %.1fs",
                               model, attempt, config.max_attempts, exc, wait)
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    return call
