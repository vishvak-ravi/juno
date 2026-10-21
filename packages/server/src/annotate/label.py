"""Sends one ``AnnotationRequest`` to the model and parses what comes back.

The call is the one napsack's ``LiteLLMClient.generate`` makes for Tada's
labeler: the same messages, token limit and JSON response schema. A model is
any litellm model string; ``api_base`` points it at a server of its own.
"""

from __future__ import annotations

import json
import asyncio
import os
from typing import Any, Optional

import litellm
from napsack.label.clients.client import DENSE_IMAGE_CAPTION_SCHEMA
from pydantic import BaseModel, ValidationError

from annotate.request import AnnotationRequest, to_messages
from annotate.sanitize import Sanitization


# litellm's OpenAI client sends no request without a key; a local server ignores it.
LOCAL_SERVER_KEY = "unused"


class AnnotationError(Exception):
    """The model's reply was not the JSON the schema asks for."""


class UnsanitizedRequest(Exception):
    """The request has not been through ``annotate.sanitize.sanitize``."""


class Action(BaseModel):
    """One thing the user did, over frames ``start`` to ``end`` (1-based, inclusive)."""

    start: int
    end: int
    caption: str


class Annotation(BaseModel):
    """The model's output for one request."""

    actions: list[Action]
    # The text the user was reading or writing, for search.
    dense_caption: str
    # How the request this answers was cleaned before it was sent.
    sanitization: Sanitization


def completion_params(request: AnnotationRequest, model: str, max_tokens: int,
                      api_key: Optional[str] = None,
                      api_base: Optional[str] = None) -> dict:
    """The ``litellm.completion`` arguments for ``request``.

    A ``gemini/`` model is sent Tada's schema as napsack sends it
    (``response_schema``). Any other model is sent the same schema as an OpenAI
    ``json_schema`` response format, which litellm passes on to the provider or
    to the OpenAI-compatible server at ``api_base`` (llama.cpp, vLLM, SGLang).
    """
    if api_key is None and api_base is not None:
        api_key = os.environ.get("OPENAI_API_KEY", LOCAL_SERVER_KEY)
    params = {
        "model": model,
        "messages": to_messages(request),
        "max_tokens": max_tokens,
        "api_base": api_base,
        "api_key": api_key,
    }
    if model.startswith("gemini/"):
        params["response_schema"] = DENSE_IMAGE_CAPTION_SCHEMA
        params["response_mime_type"] = "application/json"
    else:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "annotation", "schema": DENSE_IMAGE_CAPTION_SCHEMA},
        }
    return params


# What the provider raises when the same request may succeed later.
PROVIDER_BUSY = (litellm.RateLimitError, litellm.ServiceUnavailableError, litellm.Timeout,
                 litellm.APIConnectionError, litellm.InternalServerError)


def retryable(exc: BaseException) -> bool:
    """Whether the call that raised ``exc`` is worth making again."""
    return isinstance(exc, (*PROVIDER_BUSY, AnnotationError, TimeoutError))


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """How long the provider asked to wait (its ``Retry-After`` header), if it said."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def require_sanitized(request: AnnotationRequest) -> None:
    if request.sanitization is None:
        raise UnsanitizedRequest(
            "the request has no sanitization; pass it through annotate.sanitize.sanitize first")


def annotate(request: AnnotationRequest, model: str, max_tokens: int,
             api_key: Optional[str] = None, api_base: Optional[str] = None) -> Annotation:
    """One model call for ``request``. ``api_key`` None leaves the key to litellm's env lookup.

    Raises ``UnsanitizedRequest``, sending nothing, if ``request.sanitization`` is None.
    """
    require_sanitized(request)
    completion = litellm.completion(
        **completion_params(request, model, max_tokens, api_key, api_base))
    return parse_completion(completion, request, model)


async def annotate_async(request: AnnotationRequest, model: str, max_tokens: int,
                         api_key: Optional[str] = None,
                         api_base: Optional[str] = None) -> Annotation:
    """``annotate`` without holding a thread while the provider answers."""
    require_sanitized(request)
    # Encoding the screenshots takes CPU time; keep it off the event loop.
    params = await asyncio.to_thread(completion_params, request, model, max_tokens, api_key,
                                     api_base)
    return parse_completion(await litellm.acompletion(**params), request, model)


def parse_completion(completion: Any, request: AnnotationRequest, model: str) -> Annotation:
    """The annotation in a litellm completion for ``request``."""
    choice = completion.choices[0]
    raw = choice.message.content
    try:
        return Annotation.model_validate(
            {**json.loads(raw), "sanitization": request.sanitization})
    except (TypeError, ValueError, ValidationError) as exc:
        raise AnnotationError(
            f"{model} did not return an annotation ({exc}); "
            f"finish_reason={getattr(choice, 'finish_reason', None)!r}, raw reply: {raw!r}")
