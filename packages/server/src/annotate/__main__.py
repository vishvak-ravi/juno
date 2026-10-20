"""Annotate one chunk of a recorded session: ``python -m annotate SESSION_DIR``.

Sends a single request (``--chunk``, default the first), sanitized by
``annotate.sanitize``, and prints the model's output as JSON. Settings come from the ``annotate`` section of the YAML config
(see ``juno.config``); the API key comes from the environment or a ``.env``
file in the working directory or above it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from annotate.label import AnnotationError, annotate
from juno.sessions import read_aggregations
from annotate.request import chunk_request
from annotate.sanitize import sanitize
from juno.config import ConfigError, load_config

GEMINI_KEY_ENV = "GEMINI_API_KEY"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m annotate", description=__doc__.splitlines()[0])
    parser.add_argument("session_dir", type=Path,
                        help="a session directory holding raw_aggregations.jsonl")
    parser.add_argument("--chunk", type=int, default=0,
                        help="which chunk of the session to send, counting from 0")
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

    request = chunk_request(aggregations, args.chunk, chunk_size=config.chunk_size)
    if request is None:
        print(f"error: {args.session_dir} has no chunk {args.chunk} with a screenshot",
              file=sys.stderr)
        return 2

    request = sanitize(request)
    print(f"chunk {args.chunk}: {len(request.frames)} frames, "
          f"{request.frames[0].screenshot_path} to {request.frames[-1].screenshot_path}",
          file=sys.stderr)
    print(f"sanitization: {request.sanitization.model_dump_json()}", file=sys.stderr)
    if args.dry_run:
        print(request)
        return 0

    if config.model.startswith("gemini/") and not os.environ.get(GEMINI_KEY_ENV):
        print(f"error: {GEMINI_KEY_ENV} is not set. Put {GEMINI_KEY_ENV}=<your key> in a "
              ".env file here, or export it.", file=sys.stderr)
        return 2
    try:
        annotation = annotate(request, model=config.model, max_tokens=config.max_tokens)
    except AnnotationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # whatever litellm raises: bad key, rate limit, request too large
        print(f"error: the call to {config.model} failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    print(annotation.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
