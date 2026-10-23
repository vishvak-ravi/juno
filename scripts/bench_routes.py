"""Time the server routes the desktop timeline calls, against a fixed copy of the store.

    uv run python scripts/bench_routes.py snapshot --out DIR
    uv run python scripts/bench_routes.py run --snapshot DIR [--label NAME] [--repeats N]
    uv run python scripts/bench_routes.py run --snapshot DIR --url URL [--label NAME]

``snapshot`` copies the stored sessions (``<data dir>/screen/devices``) to
``DIR``, screenshots hard-linked rather than copied (so ``DIR`` is on the
store's filesystem), and writes a
``control.json`` with annotation off so a server on the copy changes nothing
in it. ``bench.json`` in ``DIR`` names the day timed: the local day of the
newest stored row.

``run`` starts ``python -m server`` on the copy, on a free port of 127.0.0.1,
with ``DIR/config.yaml``: this checkout's config with ``capture.data_dir``
set to ``DIR``. It then requests what the timeline requests for that day
(``apps/desktop/src/renderer/browse.js``) once right after the server is up
(pass ``cold``) and ``--repeats`` times more (``warm``). ``open day`` is
``/events`` and ``/indices`` requested together, as the linear view does.
With ``--url`` it times the server already at that address instead; the day
is still the snapshot's.

Each request is one line of ``DIR/results.jsonl``: the label, pass, name,
status, milliseconds to the headers, to the whole body, the server's own
milliseconds (``Server-Timing: app;dur``) and the body's bytes. The run
prints the median and 95th percentile of each over the warm passes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import yaml
from pydantic import BaseModel

from juno.config import (CONFIG_PATH_ENV, SERVER_HOST_ENV, config_path, control_file,
                         devices_dir, load_config, resolve_data_dir)
from juno.sessions import session_dirs
from server.events import EVENT_ROWS_FILE, SCREENSHOTS_DIR

ROOT_DIR = Path(__file__).resolve().parents[1]
BENCH_FILE = "bench.json"
RESULTS_FILE = "results.jsonl"
SERVER_LOG = "server.log"
# The timeline's page size (PAGE in browse.js).
PAGE = 100


class Timing(BaseModel):
    label: str
    pass_name: str
    name: str
    status: int
    headers_ms: float
    total_ms: float
    server_ms: Optional[float]
    bytes: int


def copy_or_link(source: str, target: str) -> None:
    """Hard-link a screenshot, which is never rewritten; copy anything else."""
    if Path(source).parent.name == SCREENSHOTS_DIR:
        os.link(source, target)
    else:
        shutil.copy2(source, target)


def last_timestamp(rows_file: Path) -> Optional[float]:
    """The timestamp of the last row of a session's rows file; None if it has none."""
    lines = [line for line in rows_file.read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])["timestamp"] if lines else None


def snapshot(out: Path) -> None:
    data_dir = resolve_data_dir(load_config().capture)
    if out.exists():
        sys.exit(f"error: {out} exists; snapshot into a new directory")
    try:
        shutil.copytree(devices_dir(data_dir), devices_dir(out), copy_function=copy_or_link)
    except (OSError, shutil.Error) as exc:
        sys.exit(f"error: {exc}; screenshots are hard-linked, so {out} must be on the "
                 f"filesystem of {data_dir}")
    control = json.loads(control_file(data_dir).read_text()) if control_file(data_dir).exists() else {}
    control.setdefault("server", {})["annotate"] = False
    control_file(out).write_text(json.dumps(control, indent=2))
    stamps = [t for d in session_dirs(devices_dir(out))
              if (d / EVENT_ROWS_FILE).exists() and (t := last_timestamp(d / EVENT_ROWS_FILE))]
    day = datetime.fromtimestamp(max(stamps)).date().isoformat()
    rows = sum(len((d / EVENT_ROWS_FILE).read_text().splitlines())
               for d in session_dirs(devices_dir(out)) if (d / EVENT_ROWS_FILE).exists())
    (out / BENCH_FILE).write_text(json.dumps({"day": day, "taken_at": time.time(),
                                              "sessions": len(session_dirs(devices_dir(out))),
                                              "rows": rows}, indent=2))
    print(f"{out}: {rows} rows; timed day {day}")


def midnight(day: datetime) -> float:
    return datetime(day.year, day.month, day.day).timestamp()


def calendar_edges(day: datetime) -> list[float]:
    """The edges the calendar asks for: six weeks from the Sunday on or before the
    first of the day's month, and the midnight after (``monthGrid``, ``gridEdges``)."""
    first = datetime(day.year, day.month, 1)
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    return [midnight(start + timedelta(days=n)) for n in range(43)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(snapshot_dir: Path, timeout: float) -> tuple[subprocess.Popen, str]:
    """``python -m server`` on the snapshot; returns once ``/api/status`` answers."""
    port = free_port()
    raw = yaml.safe_load(config_path().read_text()) or {}
    raw.setdefault("capture", {})["data_dir"] = str(snapshot_dir.resolve())
    raw.setdefault("server", {})["port"] = port
    config = snapshot_dir / "config.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=False))
    env = {**os.environ, CONFIG_PATH_ENV: str(config), SERVER_HOST_ENV: "127.0.0.1"}
    log = open(snapshot_dir / SERVER_LOG, "a")
    process = subprocess.Popen([sys.executable, "-m", "server"], cwd=ROOT_DIR, env=env,
                               stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"error: the server exited ({process.returncode}); see {snapshot_dir / SERVER_LOG}")
        try:
            if httpx.get(f"{url}/api/status", timeout=1.0).status_code == 200:
                return process, url
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    process.terminate()
    sys.exit(f"error: the server did not answer within {timeout:.0f}s")


def timed(client: httpx.Client, path: str) -> tuple[dict, int, float, float, Optional[float], int]:
    """GET ``path``: its body, status, ms to headers, ms to body, server ms and bytes."""
    start = time.perf_counter()
    with client.stream("GET", path) as response:
        headers_ms = (time.perf_counter() - start) * 1000
        content = response.read()
    total_ms = (time.perf_counter() - start) * 1000
    header = response.headers.get("server-timing", "")
    server_ms = float(header.split("dur=")[1]) if "dur=" in header else None
    body = response.json() if response.status_code == 200 else {}
    return body, response.status_code, headers_ms, total_ms, server_ms, len(content)


def one_pass(client: httpx.Client, day: datetime, label: str, pass_name: str) -> list[Timing]:
    """Every request of the timeline for ``day``, in the order the client makes them."""
    since, until = midnight(day), midnight(day + timedelta(days=1))
    timings = []

    def record(name: str, path: str) -> dict:
        body, status, headers_ms, total_ms, server_ms, size = timed(client, path)
        timings.append(Timing(label=label, pass_name=pass_name, name=name, status=status,
                              headers_ms=headers_ms, total_ms=total_ms, server_ms=server_ms,
                              bytes=size))
        return body

    edges = ",".join(f"{edge:.0f}" for edge in calendar_edges(day))
    record("calendar", f"/events/activity?edges={edges}")
    events_path = f"/events?since={since:.0f}&until={until:.0f}&order=desc&limit={PAGE}"
    indices_path = f"/indices?since={since:.0f}&until={until:.0f}"
    first = record("day events", events_path)
    record("day indices", indices_path)
    if first.get("last_id"):
        record("day events, next page", f"{events_path}&after={first['last_id']}")
    sessions = record("day sessions", f"/sessions?since={since:.0f}&until={until:.0f}&limit={PAGE}")
    if sessions.get("data"):
        record("open session", f"/sessions/{sessions['data'][0]['id']}")

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        both = list(pool.map(lambda path: timed(client, path), [events_path, indices_path]))
    timings.append(Timing(label=label, pass_name=pass_name, name="open day (events + indices)",
                          status=max(b[1] for b in both),
                          headers_ms=max(b[2] for b in both),
                          total_ms=(time.perf_counter() - start) * 1000,
                          server_ms=max((b[4] or 0) for b in both), bytes=sum(b[5] for b in both)))
    return timings


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def report(timings: list[Timing]) -> None:
    cold = {t.name: t for t in timings if t.pass_name == "cold"}
    warm: dict[str, list[Timing]] = {}
    for t in timings:
        if t.pass_name == "warm":
            warm.setdefault(t.name, []).append(t)
    print(f"{'request':<30} {'cold ms':>9} {'p50 ms':>9} {'p95 ms':>9} "
          f"{'server p50':>11} {'headers p50':>12} {'bytes':>9}")
    for name, cold_timing in cold.items():
        runs = warm.get(name, [cold_timing])
        total = [t.total_ms for t in runs]
        print(f"{name:<30} {cold_timing.total_ms:>9.0f} {statistics.median(total):>9.0f} "
              f"{percentile(total, 0.95):>9.0f} "
              f"{statistics.median([t.server_ms or 0 for t in runs]):>11.0f} "
              f"{statistics.median([t.headers_ms for t in runs]):>12.0f} "
              f"{runs[0].bytes:>9}")


def run(snapshot_dir: Path, label: str, repeats: int, url: Optional[str], timeout: float) -> None:
    bench = json.loads((snapshot_dir / BENCH_FILE).read_text())
    day = datetime.fromisoformat(bench["day"])
    process = None
    if url is None:
        process, url = start_server(snapshot_dir, timeout)
    try:
        with httpx.Client(base_url=url, timeout=None) as client:
            timings = one_pass(client, day, label, "cold")
            for _ in range(repeats):
                timings += one_pass(client, day, label, "warm")
    finally:
        if process is not None:
            process.terminate()
            process.wait()
    with open(snapshot_dir / RESULTS_FILE, "a") as results:
        for timing in timings:
            results.write(timing.model_dump_json() + "\n")
    print(f"{label}: {bench['rows']} rows, day {bench['day']}, {url}, {repeats} warm passes")
    report(timings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("snapshot", help="copy the store to time against")
    take.add_argument("--out", type=Path, required=True)
    bench = commands.add_parser("run", help="time the timeline's routes on a snapshot")
    bench.add_argument("--snapshot", type=Path, required=True)
    bench.add_argument("--label", default="run", help="names this run in results.jsonl")
    bench.add_argument("--repeats", type=int, default=10, help="warm passes after the cold one")
    bench.add_argument("--url", help="time the server at this address instead of starting one")
    bench.add_argument("--timeout", type=float, default=300.0,
                       help="seconds to wait for the started server to answer")
    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot(args.out)
    else:
        run(args.snapshot, args.label, args.repeats, args.url, args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
