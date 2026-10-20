"""Chat completions from the OpenAI-compatible server (vLLM) the ``juno`` script runs."""

from typing import Callable, Optional

import httpx

from juno.config import LlmConfig

# How much of an error response from the server is kept in the error message.
ERROR_BODY_CHARS = 500


class LlmError(Exception):
    """The server could not be reached or did not return a reply."""


def complete(config: LlmConfig, messages: list[dict], tools: Optional[list[dict]] = None,
             call_tool: Optional[Callable[[str, str], str]] = None) -> str:
    """The assistant's reply to ``messages`` (OpenAI chat messages, oldest first).

    The model is the first one the server lists, as in the ``juno`` script.

    Args:
        tools: chat-completions tool definitions the model may call.
        call_tool: runs one call, given the tool's name and its arguments as
            the JSON text the model wrote; returns the text shown to the model.
            Calls are run and the model asked again until it replies without any.
    """
    messages = list(messages)
    try:
        with httpx.Client(base_url=config.base_url, timeout=config.timeout_seconds) as client:
            models = client.get("/v1/models")
            models.raise_for_status()
            body = {
                "model": models.json()["data"][0]["id"],
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            }
            if tools and call_tool:
                body["tools"] = tools
            for _ in range(config.max_tool_rounds):
                response = client.post("/v1/chat/completions",
                                       json={**body, "messages": messages})
                response.raise_for_status()
                choice = response.json()["choices"][0]
                content = choice["message"].get("content")
                calls = choice["message"].get("tool_calls")
                if not calls or not call_tool:
                    break
                messages.append({"role": "assistant", "content": content or "",
                                 "tool_calls": calls})
                for call in calls:
                    result = call_tool(call["function"]["name"], call["function"]["arguments"])
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": result[:config.tool_result_chars]})
    except httpx.HTTPStatusError as exc:
        raise LlmError(f"the model server answered {exc.response.status_code}: "
                       f"{exc.response.text[:ERROR_BODY_CHARS]}")
    except httpx.HTTPError as exc:
        raise LlmError(f"could not reach the model server at {config.base_url}: {exc!r}")
    except (ValueError, LookupError, TypeError, AttributeError) as exc:
        raise LlmError(f"unexpected response from the model server: {exc!r}")
    if not content:
        # For example max_tokens used up before any visible text was produced,
        # or the model still calling tools after max_tool_rounds.
        raise LlmError("the model server returned an empty reply "
                       f"(finish_reason: {choice.get('finish_reason')})")
    return content
