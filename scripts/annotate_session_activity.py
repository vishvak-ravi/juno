"""Annotate a recorded session, chunk by chunk, into a store of this run's own.

    uv run python scripts/annotate_session_activity.py SESSION_DIR [--chunk N]
        [--model MODEL] [--api-base URL] [--out DIR] [--dry-run]

Every chunk of the session is annotated, or only chunk ``--chunk``. Each request
is built and sanitized as the server builds it (``annotate.request``,
``annotate.sanitize``) and sent with ``annotate.label.completion_params``, one
call at a time. ``--model`` and ``--api-base`` default to ``annotate.model`` and
``annotate.api_base`` in the config.

The session directory is only read. Results go to ``--out``, by default
``data/annotations/<session id>/<model with / as _>/`` under the checkout:

- ``annotations/NNNN.json``: the chunk's ``AnnotationRecord``, as the server
  stores it, so a run here never touches the server's own annotations
- ``calls.jsonl``: one line per model call, failed ones included: its timing,
  the size of what was sent, the token counts, the reply and its reasoning, and
  the server's ``timings`` when it reports them (llama.cpp's server does)
- ``runs.jsonl``: one line per invocation: model, endpoint, settings, git commit
  and, from a llama.cpp server, its ``/props``

A chunk whose record in ``--out`` is done and covers the rows the chunk holds
now is skipped, so a stopped run continues where it left off.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import litellm
from dotenv import find_dotenv, load_dotenv
from PIL import Image

from annotate.label import (AnnotationError, completion_params, parse_completion,
                            retry_after_seconds, retryable)
from annotate.request import AnnotationRequest, chunk_request
from annotate.sanitize import sanitize
from annotate.store import covers, new_record, read_record, write_record
from juno.config import AnnotateConfig, ConfigError, load_config
from juno.sessions import Aggregation, read_aggregations, session_id_of

ROOT_DIR = Path(__file__).resolve().parents[1]
GEMINI_KEY_ENV = "GEMINI_API_KEY"


def default_out(session_id: str, model: str) -> Path:
    return ROOT_DIR / "data" / "annotations" / session_id / model.replace("/", "_")


def chunk_count(aggregations: list[Aggregation], chunk_size: int) -> int:
    return -(-len(aggregations) // chunk_size)


def chunk_rows(aggregations: list[Aggregation], chunk: int, chunk_size: int) -> int:
    return len(aggregations[chunk * chunk_size:(chunk + 1) * chunk_size])


def input_size(request: AnnotationRequest, messages: list[dict]) -> dict:
    """How much the model is sent for ``request``: frames, pixels, image bytes, text."""
    pixels = 0
    for frame in request.frames:
        with Image.open(frame.screenshot_path) as image:
            pixels += image.width * image.height
    parts = messages[0]["content"]
    image_bytes = sum(len(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
                      for part in parts if part["type"] == "image_url")
    return {"frames": len(request.frames), "pixels": pixels, "image_bytes": image_bytes,
            "text_chars": sum(len(part["text"]) for part in parts if part["type"] == "text")}


def completion_fields(completion) -> dict:
    """What a call's reply is made of: tokens, finish reason, reasoning, server timings."""
    choice = completion.choices[0]
    usage = completion.usage
    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "cached_tokens": getattr(details, "cached_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "finish_reason": choice.finish_reason,
        "content": choice.message.content,
        "reasoning": getattr(choice.message, "reasoning_content", None),
        "timings": completion.model_dump().get("timings"),
    }


def server_props(api_base: Optional[str]) -> Optional[dict]:
    """A llama.cpp server's ``/props`` (model file, context, slots); None from any other server."""
    if api_base is None:
        return None
    try:
        response = httpx.get(api_base.rstrip("/").removesuffix("/v1") + "/props", timeout=5)
        response.raise_for_status()
        props = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return {key: props.get(key) for key in ("model_path", "build_info", "total_slots",
                                            "modalities", "default_generation_settings")}


def git_commit() -> Optional[str]:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, capture_output=True,
                            text=True)
    return result.stdout.strip() or None


def append_line(path: Path, row: dict) -> None:
    with open(path, "a") as file:
        file.write(json.dumps(row, default=str) + "\n")


def annotate_chunk(session_dir: Path, aggregations: list[Aggregation], chunk: int,
                   out: Path, model: str, api_base: Optional[str],
                   config: AnnotateConfig) -> str:
    """Annotate one chunk into ``out``, retrying as the server does. Returns the record's status."""
    session_id = session_id_of(session_dir)
    request = chunk_request(aggregations, chunk, chunk_size=config.chunk_size)
    if request is None:
        return "empty"
    request = sanitize(request)
    rows = chunk_rows(aggregations, chunk, config.chunk_size)
    record = new_record(session_id, chunk, model).model_copy(update={
        "frames": request.frames, "rows": rows,
        "start_time": request.frames[0].timestamp, "end_time": request.frames[-1].timestamp})
    params = completion_params(request, model, config.max_tokens, api_base=api_base)
    size = input_size(request, params["messages"])

    for attempt in range(1, config.max_attempts + 1):
        call = {"session_id": session_id, "chunk": chunk, "attempt": attempt, "model": model,
                "started_at": time.time(), **size}
        try:
            completion = litellm.completion(**params, timeout=config.timeout_seconds)
            call["wall_seconds"] = time.time() - call["started_at"]
            call.update(completion_fields(completion))
            annotation = parse_completion(completion, request, model)
            call.update(status="done", actions=len(annotation.actions))
            record = record.model_copy(update={"status": "done", "annotation": annotation,
                                               "error": None, "attempts": attempt})
        except Exception as exc:  # recorded on the call and the record, whatever litellm raised
            call.setdefault("wall_seconds", time.time() - call["started_at"])
            call.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            record = record.model_copy(update={"status": "failed", "error": call["error"],
                                               "attempts": attempt})
            if retryable(exc) and attempt < config.max_attempts:
                wait = max(retry_after_seconds(exc) or 0.0,
                           min(config.retry_max_seconds,
                               config.retry_base_seconds * 2 ** (attempt - 1)))
                append_line(out / "calls.jsonl", call)
                print(f"  chunk {chunk}: attempt {attempt} failed, next in {wait:.0f}s: "
                      f"{call['error'][:200]}", file=sys.stderr)
                time.sleep(wait)
                continue
        append_line(out / "calls.jsonl", call)
        break
    write_record(out, record.model_copy(update={"updated_at": time.time()}))
    return record.status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_dir", type=Path,
                        help="a session directory holding raw_aggregations.jsonl")
    parser.add_argument("--chunk", type=int,
                        help="annotate only this chunk, counting from 0; default: every chunk")
    parser.add_argument("--model", help="litellm model; default: annotate.model")
    parser.add_argument("--api-base", help="OpenAI-compatible server; default: annotate.api_base")
    parser.add_argument("--out", type=Path,
                        help="where results go; default: data/annotations/<session id>/<model>/")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the text the model would be sent, and send nothing")
    args = parser.parse_args(argv)

    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config().annotate
        aggregations = read_aggregations(args.session_dir)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    model = args.model or config.model
    api_base = args.api_base or config.api_base
    session_dir = args.session_dir.resolve()
    session_id = session_id_of(session_dir)
    out = (args.out or default_out(session_id, model)).resolve()
    if out == session_dir or session_dir in out.parents:
        print(f"error: --out {out} is inside the session directory; results go elsewhere",
              file=sys.stderr)
        return 2

    total = chunk_count(aggregations, config.chunk_size)
    chunks = range(total) if args.chunk is None else [args.chunk]
    if args.chunk is not None and not 0 <= args.chunk < total:
        print(f"error: {session_id} has chunks 0 to {total - 1}", file=sys.stderr)
        return 2

    if args.dry_run:
        for chunk in chunks:
            request = chunk_request(aggregations, chunk, chunk_size=config.chunk_size)
            if request is not None:
                print(f"--- chunk {chunk}")
                print(sanitize(request))
        return 0

    if model.startswith("gemini/") and not os.environ.get(GEMINI_KEY_ENV):
        print(f"error: {GEMINI_KEY_ENV} is not set. Put {GEMINI_KEY_ENV}=<your key> in a "
              ".env file here, or export it.", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    append_line(out / "runs.jsonl", {
        "started_at": time.time(), "session_id": session_id, "session_dir": str(session_dir),
        "model": model, "api_base": api_base, "chunks": total if args.chunk is None else 1,
        "settings": config.model_dump(), "git_commit": git_commit(),
        "server": server_props(api_base)})
    print(f"{session_id}: {total} chunks -> {out}", file=sys.stderr)

    counts = {"done": 0, "failed": 0, "skipped": 0, "empty": 0}
    for number, chunk in enumerate(chunks, start=1):
        rows = chunk_rows(aggregations, chunk, config.chunk_size)
        if covers(read_record(out, chunk), rows):
            counts["skipped"] += 1
            continue
        started = time.time()
        status = annotate_chunk(session_dir, aggregations, chunk, out, model, api_base, config)
        counts[status] += 1
        print(f"[{number}/{len(chunks)}] chunk {chunk}: {status} in {time.time() - started:.1f}s",
              file=sys.stderr)
    print(json.dumps({"session_id": session_id, "model": model, "out": str(out), **counts}))
    if args.chunk is not None and counts["done"]:
        print(read_record(out, args.chunk).annotation.model_dump_json(indent=2))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
