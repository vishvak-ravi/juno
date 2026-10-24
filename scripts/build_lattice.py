"""Build a behavior lattice over the stored events: activity captions and imported conversations.

    uv run python scripts/build_lattice.py [--device ID] [--since TIME] [--until TIME]
        [--user-name NAME] [--window-size N] [--min-insights N] [--session-gap-hours H]
        [--layer SPEC ...] [--events TYPE[,TYPE]] [--turn-chars N]

The method, models and knobs are ``lattice`` in ``config.yaml``
(``juno.config.LatticeConfig``); each flag overrides one knob. ``--since`` and
``--until`` are Unix seconds or an ISO datetime. ``--layer`` is ``type`` or
``type:value`` (``time:day``, ``session:3``, ``number:20``, ``all``), given once
per layer, layer 1 first; any ``--layer`` replaces the configured layers.
``--events`` names the event types read, ``activity`` and/or ``message``.

The events are read from the store the server uses (``capture.data_dir``);
the build goes to ``<data dir>/lattice/<build id>/`` (``lattice.store``), its
record saved as each stage ends. Each stage is logged with its node count,
calls, failures, seconds and token totals; the build id and directory are
printed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from annotate.store import FileActivityCaptions
from juno.config import ConfigError, LatticeConfig, LayerSpec, devices_dir, load_config, resolve_data_dir
from lattice.build import build_lattice, new_build
from lattice.models import LatticeParams
from lattice.store import FileLattices, lattice_dir
from server.conversations import FileConversations

logger = logging.getLogger("build_lattice")


def parse_time(value: Optional[str]) -> Optional[float]:
    """Unix seconds from a number or an ISO datetime; None from None."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return datetime.fromisoformat(value).timestamp()


def parse_layer(value: str) -> LayerSpec:
    """A ``LayerSpec`` from ``type`` or ``type:value``."""
    kind, _, rest = value.partition(":")
    return LayerSpec(type=kind.strip(), value=rest.strip() or None)


def params_of(config: LatticeConfig, args: argparse.Namespace) -> LatticeParams:
    """The configured knobs with the flags' overrides, the description naming the user."""
    user_name = args.user_name or config.user_name
    return LatticeParams(
        user_name=user_name,
        description=config.description.replace("{user_name}", user_name),
        window_size=args.window_size or config.window_size,
        min_insights=args.min_insights or config.min_insights,
        session_gap_hours=(args.session_gap_hours if args.session_gap_hours is not None
                           else config.session_gap_hours),
        layers=[parse_layer(spec) for spec in args.layer] if args.layer else list(config.layers),
        events=[kind.strip() for kind in args.events.split(",")] if args.events else list(config.events),
        turn_chars=args.turn_chars or config.turn_chars)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="build over this device's captions; default: every device's")
    parser.add_argument("--since", help="Unix seconds or ISO datetime; captions before it are left out")
    parser.add_argument("--until", help="Unix seconds or ISO datetime; captions after it are left out")
    parser.add_argument("--user-name", help="the name the prompts call the user; default: lattice.user_name")
    parser.add_argument("--window-size", type=int, help="interactions per observer window")
    parser.add_argument("--min-insights", type=int, help="insights asked for per group, at least")
    parser.add_argument("--session-gap-hours", type=float,
                        help="hours without an interaction that start a new session")
    parser.add_argument("--layer", action="append",
                        help="a layer, type or type:value, layer 1 first; replaces the configured layers")
    parser.add_argument("--events", help="the event types read, comma-separated: activity, message")
    parser.add_argument("--turn-chars", type=int, help="characters of a conversation turn shown, at most")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # litellm logs every completion it makes at INFO; the build logs each stage itself.
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config()
        params = params_of(config.lattice, args)
        since, until = parse_time(args.since), parse_time(args.until)
    except (ConfigError, ValueError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data_dir = resolve_data_dir(config.capture)
    captions = FileActivityCaptions(devices_dir(data_dir))
    conversations = FileConversations(devices_dir(data_dir))
    lattices = FileLattices(lattice_dir(data_dir))
    build = new_build(config.lattice, params, args.device, since, until)
    logger.info("build %s: device %s, models %s, params %s", build.id, args.device or "all",
                build.models, params.model_dump())
    try:
        lattice = asyncio.run(build_lattice(build, captions, conversations, lattices, config.lattice))
    except Exception as exc:
        logger.error("build %s failed: %s", build.id, exc)
        print(f"{build.id} failed: {exc}\n{lattices.build_dir(build.id)}", file=sys.stderr)
        return 1
    logger.info("build %s done: %d captions, %d conversations, %d sessions, nodes per layer %s, "
                "seconds %s, calls %s, failures %s", build.id, build.captions, build.conversations,
                len(build.sessions), build.node_counts,
                build.stage_seconds, build.stage_calls, build.stage_failures)
    print(f"{lattice.build.id}\n{lattices.build_dir(lattice.build.id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
