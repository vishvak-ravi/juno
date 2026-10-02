"""Chat completions from the OpenAI-compatible server (vLLM) the ``juno`` script runs."""

import httpx

from juno.config import LlmConfig

# How much of an error response from the server is kept in the error message.
ERROR_BODY_CHARS = 500


class LlmError(Exception):
    """The server could not be reached or did not return a reply."""


def complete(config: LlmConfig, messages: list[dict]) -> str:
    """The assistant's reply to ``messages`` (OpenAI chat messages, oldest first).

    The model is the first one the server lists, as in the ``juno`` script.
    """
    try:
        with httpx.Client(base_url=config.base_url, timeout=config.timeout_seconds) as client:
            models = client.get("/v1/models")
            models.raise_for_status()
            response = client.post("/v1/chat/completions", json={
                "model": models.json()["data"][0]["id"],
                "messages": messages,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            })
            response.raise_for_status()
            choice = response.json()["choices"][0]
            content = choice["message"].get("content")
    except httpx.HTTPStatusError as exc:
        raise LlmError(f"the model server answered {exc.response.status_code}: "
                       f"{exc.response.text[:ERROR_BODY_CHARS]}")
    except httpx.HTTPError as exc:
        raise LlmError(f"could not reach the model server at {config.base_url}: {exc!r}")
    except (ValueError, LookupError, TypeError, AttributeError) as exc:
        raise LlmError(f"unexpected response from the model server: {exc!r}")
    if not content:
        # For example max_tokens used up before any visible text was produced.
        raise LlmError("the model server returned an empty reply "
                       f"(finish_reason: {choice.get('finish_reason')})")
    return content
