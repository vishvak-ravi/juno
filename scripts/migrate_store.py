"""Move the server's store to the layout ``server.events``, ``annotate.store`` and ``keypoints.store`` read.

    uv run python scripts/migrate_store.py [--dry-run]

In every session under ``<data_dir>/screen/devices/``:

- ``raw_aggregations.jsonl`` becomes ``event_rows.jsonl``;
- ``annotations/NNNN.json`` becomes ``activity_captions/NNNN.json``;
- ``detections/jev/NNNN.json`` becomes ``question_answers/NNNN.json``, its
  ``annotation_updated_at`` named ``caption_updated_at``.

Screenshot paths in rows and in caption frames are rewritten relative to the
session directory (``screenshots/<name>``) when that file is there; any other
path is kept as it is, and ``GET /api/integrity`` reports it. Screenshots
themselves are not moved. Each new file is written whole before the old one is
removed, so a stopped run can be run again and continues. A session whose new
file already holds something else (a server on the new layout wrote it) is left
as it is and reported. Stop the server first. Settings come from the ``capture`` section of ``config.yaml`` (or
JUNO_CONFIG_PATH).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel

from annotate.store import CAPTIONS_DIR
from juno.config import ConfigError, devices_dir, load_config, resolve_data_dir
from juno.sessions import replace_file, session_dirs, stored_path
from keypoints.store import ANSWERS_DIR
from server.events import EVENT_ROWS_FILE, SCREENSHOTS_DIR

ROWS_FILE = "raw_aggregations.jsonl"
CAPTIONS_FROM = "annotations"
ANSWERS_FROM = Path("detections") / "jev"


class Move(BaseModel):
    """One file to rewrite under its new name."""
    source: Path
    target: Path


def screenshot_key(directory: Path, path: Optional[str]) -> Optional[str]:
    """``screenshots/<name>`` if the session holds that file, else the path as given."""
    if path is None:
        return None
    name = Path(path).name
    if (directory / SCREENSHOTS_DIR / name).is_file():
        return f"{SCREENSHOTS_DIR}/{name}"
    return stored_path(directory, path)


def migrated_rows(directory: Path, text: str) -> str:
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return "".join(json.dumps({**row, "screenshot_path": screenshot_key(directory, row.get("screenshot_path"))})
                   + "\n" for row in rows)


def migrated_caption(directory: Path, text: str) -> str:
    caption = json.loads(text)
    caption["frames"] = [{**frame, "screenshot_path": screenshot_key(directory, frame.get("screenshot_path"))}
                         for frame in caption.get("frames", [])]
    return json.dumps(caption, indent=2)


def migrated_answers(text: str) -> str:
    answers = json.loads(text)
    if "annotation_updated_at" in answers:
        answers["caption_updated_at"] = answers.pop("annotation_updated_at")
    return json.dumps(answers, indent=2)


def session_moves(directory: Path) -> list[tuple[Move, Callable[[str], str]]]:
    """What to rewrite in one session, each with how its text changes."""
    moves = []
    if (directory / ROWS_FILE).is_file():
        moves.append((Move(source=directory / ROWS_FILE, target=directory / EVENT_ROWS_FILE),
                      lambda text: migrated_rows(directory, text)))
    for path in sorted((directory / CAPTIONS_FROM).glob("*.json")):
        moves.append((Move(source=path, target=directory / CAPTIONS_DIR / path.name),
                      lambda text: migrated_caption(directory, text)))
    for path in sorted((directory / ANSWERS_FROM).glob("*.json")):
        moves.append((Move(source=path, target=directory / ANSWERS_DIR / path.name),
                      migrated_answers))
    return moves


def remove_empty(directory: Path) -> None:
    for emptied in (directory / CAPTIONS_FROM, directory / ANSWERS_FROM, directory / ANSWERS_FROM.parent):
        if emptied.is_dir() and not any(emptied.iterdir()):
            emptied.rmdir()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print what would move, change nothing")
    args = parser.parse_args(argv)
    try:
        store = devices_dir(resolve_data_dir(load_config().capture))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    conflicts = []
    for directory in session_dirs(store):
        planned = [(move, change(move.source.read_text())) for move, change in session_moves(directory)]
        if not planned:
            continue
        # A target a stopped run wrote holds exactly what it would be written with.
        clashing = [move.target for move, text in planned
                    if move.target.exists() and move.target.read_text() != text]
        if clashing:
            conflicts.extend(clashing)
            continue
        print(f"{directory.relative_to(store)}: {len(planned)} files", file=sys.stderr)
        if args.dry_run:
            continue
        for move, text in planned:
            if not move.target.exists():
                replace_file(move.target, text)
            move.source.unlink()
        remove_empty(directory)
    for target in conflicts:
        print(f"error: {target} already holds other data; its session was left as it is",
              file=sys.stderr)
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
