"""Sends one ``AnnotationRequest`` to the model and parses what comes back.

The call is the one napsack's ``LiteLLMClient.generate`` makes for Tada's
labeler: the same messages, token limit and JSON response schema.
"""

from __future__ import annotations

import json
from typing import Optional

import litellm
from napsack.label.clients.client import DENSE_IMAGE_CAPTION_SCHEMA
from pydantic import BaseModel, ValidationError

from annotate.request import AnnotationRequest, to_messages
from annotate.sanitize import Sanitization


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
                      api_key: Optional[str] = None) -> dict:
    """The ``litellm.completion`` arguments for ``request``."""
    params = {
        "model": model,
        "messages": to_messages(request),
        "max_tokens": max_tokens,
        "api_base": None,
        "api_key": api_key,
    }
    if model.startswith("gemini/"):
        params["response_schema"] = DENSE_IMAGE_CAPTION_SCHEMA
        params["response_mime_type"] = "application/json"
    elif model.startswith("hosted_vllm/"):
        params["extra_body"] = {"guided_json": DENSE_IMAGE_CAPTION_SCHEMA}
    else:
        params["response_format"] = {"type": "json_object"}
    return params


def annotate(request: AnnotationRequest, model: str, max_tokens: int,
             api_key: Optional[str] = None) -> Annotation:
    """One model call for ``request``. ``api_key`` None leaves the key to litellm's env lookup.

    Raises ``UnsanitizedRequest``, sending nothing, if ``request.sanitization`` is None.
    """
    if request.sanitization is None:
        raise UnsanitizedRequest(
            "the request has no sanitization; pass it through annotate.sanitize.sanitize first")
    completion = litellm.completion(**completion_params(request, model, max_tokens, api_key))
    choice = completion.choices[0]
    raw = choice.message.content
    try:
        return Annotation.model_validate(
            {**json.loads(raw), "sanitization": request.sanitization})
    except (TypeError, ValueError, ValidationError) as exc:
        raise AnnotationError(
            f"{model} did not return an annotation ({exc}); "
            f"finish_reason={getattr(choice, 'finish_reason', None)!r}, raw reply: {raw!r}")
