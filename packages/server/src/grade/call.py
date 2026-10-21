"""One litellm call, repeated while the provider is busy."""

from __future__ import annotations

import sys
import time
from typing import Optional

import litellm

from annotate.label import PROVIDER_BUSY, retry_after_seconds
from grade.judge import Judge
from juno.config import GradeConfig


class CallFailed(Exception):
    """A model call failed, or kept failing while the provider was busy."""


def complete(model: str, api_base: Optional[str], messages: list[dict], max_attempts: int,
             retry_seconds: float, **params) -> str:
    """The reply text of one call to ``model``, made up to ``max_attempts`` times while busy."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = litellm.completion(model=model, messages=messages, api_base=api_base,
                                          **params)
            return response.choices[0].message.content or ""
        except PROVIDER_BUSY as exc:
            if attempt == max_attempts:
                raise CallFailed(f"{model}: {type(exc).__name__}: {exc}") from exc
            wait = retry_after_seconds(exc) or retry_seconds
            print(f"  {model} busy ({type(exc).__name__}); retrying in {wait:.0f}s",
                  file=sys.stderr)
            time.sleep(wait)
        except Exception as exc:  # whatever litellm raises: bad key, bad request, bad model
            raise CallFailed(f"{model}: {type(exc).__name__}: {exc}") from exc
    raise AssertionError("unreachable")


def judge_for(config: GradeConfig) -> Judge:
    """The judge ``config`` names, as a prompt-to-reply function."""
    def judge(prompt: str) -> str:
        return complete(config.model, config.api_base, [{"role": "user", "content": prompt}],
                        config.max_attempts, config.retry_seconds)
    return judge
