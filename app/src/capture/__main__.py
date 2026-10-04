"""Run the activity recorder: ``python -m capture``.

Settings come from the ``capture`` section of the YAML config (see
``juno.config``). With ``annotate.live`` set, each aggregation is uploaded to
the server as it is written (see ``capture.upload``), which annotates it.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Optional

from capture.sources import BackendUnavailable, load_backend
from capture.upload import AggregationUploader
from juno.config import (
    CaptureConfig,
    ConfigError,
    load_config,
    resolve_data_dir,
    resolve_device_id,
)
from juno.parent_watch import start_parent_watch


def main(config: CaptureConfig, aggregations_url: Optional[str] = None) -> int:
    """
    Args:
        aggregations_url: the server's aggregations collection, which the
            session is uploaded to; None records without uploading.
    """
    logging.basicConfig(
        level=logging.DEBUG if config.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        backend = load_backend(config.backend, fps=config.fps)
    except BackendUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Imported late so backend errors do not pay for napsack's imports.
    from capture.recorder import Recorder

    recorder = Recorder(
        backend,
        data_dir=resolve_data_dir(config),
        fps=config.fps,
        buffer_seconds=config.buffer_seconds,
        target_dpi=config.dpi,
        scale=config.scale,
        max_res=config.max_res,
        save_screenshots=config.screenshots,
        disable=config.disable,
    )
    uploader = None
    if aggregations_url is not None:
        uploader = AggregationUploader(recorder.session_dir, aggregations_url,
                                       resolve_device_id(config))
        recorder.on_aggregation = uploader.on_aggregation

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
        done.wait(config.duration)
    finally:
        # A second Ctrl+C must not interrupt the flush.
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            complete = recorder.stop()
            if uploader is not None:
                uploader.close()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    print(f"Session saved to {recorder.session_dir} "
          f"({recorder.processed_aggregations} aggregations).", file=sys.stderr)
    if not complete:
        print("warning: some events were not captured in any aggregation", file=sys.stderr)
    print(recorder.session_dir)
    return 0


def run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    start_parent_watch()
    if not config.annotate.live:
        return main(config.capture)
    return main(config.capture,
                aggregations_url=f"http://{config.server.host}:{config.server.port}/aggregations")


if __name__ == "__main__":
    sys.exit(run())
