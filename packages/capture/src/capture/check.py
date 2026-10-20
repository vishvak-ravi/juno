"""Acceptance check for a recorded session: ``python -m capture.check``.

Answers one question: did using the computer get logged as events? It reads
only the files a session leaves on disk, so it works for any backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from juno.config import ConfigError, load_config, resolve_data_dir

# A frame whose brightest pixel is at or below this is treated as black, which
# is what an X11-only grabber returns on Wayland.
BLACK_MAX = 8
# Share of aggregations allowed to lack a screenshot, or events allowed to be
# missing from every aggregation, before the session fails. napsack itself
# drops a few at the edges of a session.
TOLERANCE = 0.05


@dataclass
class Report:
    session_dir: Path
    aggregations: int = 0
    with_screenshot: int = 0
    screenshots_checked: int = 0
    black_screenshots: int = 0
    events: Counter = field(default_factory=Counter)
    orphaned_events: int = 0
    duration_s: float = 0.0
    active_minutes: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def total_events(self) -> int:
        return sum(self.events.values())

    def format(self) -> str:
        lines = [
            f"Session:       {self.session_dir}",
            f"Duration:      {self.duration_s / 60:.1f} min "
            f"({self.active_minutes} with input)",
            f"Events:        {self.total_events} "
            + (", ".join(f"{k}={v}" for k, v in sorted(self.events.items())) or "none"),
            f"Aggregations:  {self.aggregations} "
            f"({self.with_screenshot} with a screenshot on disk)",
            f"Screenshots:   {self.screenshots_checked} inspected, "
            f"{self.black_screenshots} black",
            f"Orphan events: {self.orphaned_events}",
        ]
        lines += [f"WARNING: {w}" for w in self.warnings]
        lines += [f"FAIL: {p}" for p in self.problems]
        lines.append("PASS" if self.ok else "FAILED")
        return "\n".join(lines)


def latest_session(data_dir: Path) -> Optional[Path]:
    sessions = sorted((Path(data_dir) / "screen" / "sessions").glob("session_*"))
    return sessions[-1] if sessions else None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _resolve_screenshot(session_dir: Path, recorded: Optional[str]) -> Optional[Path]:
    if not recorded:
        return None
    path = Path(recorded)
    if path.exists():
        return path
    # The session may have been moved since it was recorded.
    moved = session_dir / "screenshots" / path.name
    return moved if moved.exists() else None


def _event_key(event: dict) -> tuple:
    return (event.get("timestamp"), event.get("event_type"))


def check_session(session_dir: Path, sample: int = 50) -> Report:
    session_dir = Path(session_dir)
    report = Report(session_dir=session_dir)

    aggregations = _read_jsonl(session_dir / "raw_aggregations.jsonl")
    events = _read_jsonl(session_dir / "input_events.jsonl")
    report.aggregations = len(aggregations)
    report.events = Counter(e.get("event_type", "unknown") for e in events)

    if not events:
        report.problems.append("no input events were logged")
    if not aggregations:
        report.problems.append("no aggregations were written")
        return report

    timestamps = [e["timestamp"] for e in events if e.get("timestamp") is not None]
    if timestamps:
        report.duration_s = max(timestamps) - min(timestamps)
        report.active_minutes = len({int(t // 60) for t in timestamps})

    screenshots = [_resolve_screenshot(session_dir, a.get("screenshot_path"))
                   for a in aggregations]
    found = [p for p in screenshots if p is not None]
    report.with_screenshot = len(found)
    missing = report.aggregations - report.with_screenshot
    if not found:
        report.problems.append("no aggregation has a screenshot on disk")
    elif missing > report.aggregations * TOLERANCE:
        report.problems.append(
            f"{missing} of {report.aggregations} aggregations have no screenshot on disk")
    elif missing:
        report.warnings.append(f"{missing} aggregations have no screenshot on disk")

    if found and sample > 0:
        step = max(1, len(found) // sample)
        for path in found[::step][:sample]:
            report.screenshots_checked += 1
            try:
                with Image.open(path) as img:
                    brightest = int(np.asarray(img.convert("L")).max())
            except Exception as exc:
                report.problems.append(f"unreadable screenshot {path.name}: {exc}")
                continue
            if brightest <= BLACK_MAX:
                report.black_screenshots += 1
        if report.black_screenshots:
            report.problems.append(
                f"{report.black_screenshots} of {report.screenshots_checked} "
                "inspected screenshots are black")

    aggregated = {_event_key(e) for a in aggregations for e in a.get("events", [])}
    report.orphaned_events = sum(1 for e in events if _event_key(e) not in aggregated)
    if report.orphaned_events > len(events) * TOLERANCE:
        report.problems.append(
            f"{report.orphaned_events} of {len(events)} events are in no aggregation")
    elif report.orphaned_events:
        report.warnings.append(f"{report.orphaned_events} events are in no aggregation")

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m capture.check",
        description="Check that a recorded session contains events and screenshots.",
    )
    parser.add_argument("session_dir", nargs="?", type=Path,
                        help="Session to check (default: newest under --data-dir)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Base directory (default: data_dir from the YAML config)")
    parser.add_argument("--sample", type=int, default=50,
                        help="Screenshots to inspect for black frames (default: %(default)s)")
    args = parser.parse_args(argv)

    session_dir = args.session_dir
    if session_dir is None:
        data_dir = args.data_dir
        if data_dir is None:
            try:
                data_dir = resolve_data_dir(load_config().capture)
            except ConfigError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        session_dir = latest_session(data_dir)
        if session_dir is None:
            print(f"error: no session found under {data_dir}", file=sys.stderr)
            return 2
    if not session_dir.is_dir():
        print(f"error: {session_dir} is not a session directory", file=sys.stderr)
        return 2

    report = check_session(session_dir, sample=args.sample)
    print(report.format())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
