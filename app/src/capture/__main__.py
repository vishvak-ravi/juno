"""Run the activity recorder: ``python -m capture``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from capture.sources import BACKENDS, BackendUnavailable, load_backend


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "juno"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m capture",
        description="Record screen activity with input events (napsack session format).",
    )
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="Base directory; sessions go under screen/sessions/ "
                             "(default: %(default)s)")
    parser.add_argument("--backend", default="auto",
                        choices=["auto", *sorted(BACKENDS)],
                        help="Platform sources to use (default: detect from the session)")
    parser.add_argument("-f", "--fps", type=int, default=5,
                        help="Frames per second to capture (default: %(default)s)")
    parser.add_argument("-s", "--buffer-seconds", type=int, default=120,
                        help="Seconds of frames to keep in memory (default: %(default)s)")
    parser.add_argument("-d", "--dpi", type=int, default=100,
                        help="Target DPI for saved screenshots (default: %(default)s)")
    parser.add_argument("--scale", type=float, default=None,
                        help="Scale factor for screenshots (0.0-1.0, overrides --dpi)")
    parser.add_argument("-r", "--max-res", type=int, nargs=2, default=None,
                        metavar=("WIDTH", "HEIGHT"),
                        help="Maximal resolution for screenshots")
    parser.add_argument("--disable", nargs="*", default=["move"],
                        help="Event types to skip: move, scroll, click, key "
                             "(default: move)")
    parser.add_argument("--no-screenshots", action="store_true",
                        help="Do not write screenshot files")
    parser.add_argument("--duration", type=float, default=None,
                        help="Stop after this many seconds (default: run until Ctrl+C)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        backend = load_backend(args.backend, fps=args.fps)
    except BackendUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Imported late so --help and backend errors do not pay for napsack's imports.
    from capture.recorder import Recorder

    recorder = Recorder(
        backend,
        data_dir=args.data_dir,
        fps=args.fps,
        buffer_seconds=args.buffer_seconds,
        target_dpi=args.dpi,
        scale=args.scale,
        max_res=tuple(args.max_res) if args.max_res else None,
        save_screenshots=not args.no_screenshots,
        disable=args.disable,
    )

    done = threading.Event()
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.signal(signum, lambda *_: done.set()) for signum in signals}
    try:
        recorder.start()
        print(f"Recording to {recorder.session_dir}. Press Ctrl+C to stop.", file=sys.stderr)
        if backend.name == "fake":
            print("NOTE: the fake backend records generated frames and a scripted "
                  "'h', 'i', Enter, click, scroll loop.\n"
                  "      Your real keyboard, mouse and screen are NOT being read.",
                  file=sys.stderr)
        done.wait(args.duration)
    finally:
        # A second Ctrl+C must not interrupt the flush.
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            complete = recorder.stop()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    print(f"Session saved to {recorder.session_dir} "
          f"({recorder.processed_aggregations} aggregations).", file=sys.stderr)
    if not complete:
        print("warning: some events were not captured in any aggregation", file=sys.stderr)
    print(recorder.session_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
