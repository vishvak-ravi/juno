"""Import the conversations an assistant's harness kept on this computer into the server's store.

    uv run python scripts/import_conversations.py [--harness claude_code] [--source DIR]
        [--device ID] [--since TIME] [--until TIME] [--dry-run]

The harness's own files (``--source``; Claude Code's: ``CLAUDE_CONFIG_DIR/projects``,
default ``~/.claude/projects``) are read by its reader in ``capture.chats`` and
each thread is written as one conversation of ``--device`` (default: this
computer's id, ``JUNO_DEVICE_ID`` or the host name) into the store the server
reads (``capture.data_dir`` in ``config.yaml``; ``server.conversations``). A
thread imported before is replaced, so running the script again brings the
store up to date. ``--since`` and ``--until`` are Unix seconds or an ISO
datetime; a thread with no turn between them is left out. ``--dry-run`` reads
and counts without writing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from dotenv import find_dotenv, load_dotenv

from build_lattice import parse_time
from capture.chats import claude_code
from juno.config import ConfigError, devices_dir, load_config, resolve_data_dir, resolve_device_id
from juno.conversations import CLAUDE_CODE, Conversation
from server.conversations import FileConversations, overlaps

logger = logging.getLogger("import_conversations")

# Harness -> (its default source directory, its reader).
READERS: dict[str, tuple[Callable[[], Path], Callable[[Path, str], list[Conversation]]]] = {
    CLAUDE_CODE: (claude_code.default_projects_dir, claude_code.read_conversations),
}



def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", default=CLAUDE_CODE, choices=sorted(READERS),
                        help="whose conversations to import")
    parser.add_argument("--source", type=Path, help="the harness's own files; default: where it keeps them")
    parser.add_argument("--device", help="the device the conversations ran on; default: this computer's id")
    parser.add_argument("--since", help="Unix seconds or ISO datetime; threads ending before it are left out")
    parser.add_argument("--until", help="Unix seconds or ISO datetime; threads starting after it are left out")
    parser.add_argument("--dry-run", action="store_true", help="read and count, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config()
        device_id = args.device or resolve_device_id()
        since, until = parse_time(args.since), parse_time(args.until)
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    default_source, read = READERS[args.harness]
    source = args.source or default_source()
    if not source.is_dir():
        print(f"error: {source} is not a directory", file=sys.stderr)
        return 2
    store = FileConversations(devices_dir(resolve_data_dir(config.capture)))

    conversations = [c for c in read(source, device_id) if overlaps(c, since, until)]
    for conversation in conversations:
        if not args.dry_run:
            store.save(conversation)
    turns = sum(len(c.turns) for c in conversations)
    prompts = sum(1 for c in conversations for turn in c.turns if turn.role == "user")
    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {len(conversations)} {args.harness} conversations of {device_id} from {source}: "
          f"{turns} turns, {prompts} of them the user's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
