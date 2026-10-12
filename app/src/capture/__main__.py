"""Run the activity recorder: ``python -m capture``.

Settings come from the ``capture`` section of the YAML config (see
``juno.config``). With ``annotate.live`` set, each aggregation is uploaded to
the server as it is written (see ``capture.upload``), which annotates it, and
every ``control.poll_seconds`` the recorder reports to the server's
``/control`` and records or not as the answer says (see ``server.control``).
Turning recording off ends the session; turning it on again starts a new one.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from typing import Optional

import httpx
from dotenv import find_dotenv, load_dotenv

from capture.sources import BackendUnavailable, load_backend
from capture.upload import AggregationUploader
from juno.config import (
    CaptureConfig,
    ConfigError,
    ControlConfig,
    load_config,
    resolve_data_dir,
    resolve_device_id,
    server_host,
)
from juno.parent_watch import start_parent_watch

logger = logging.getLogger(__name__)


def report(report_url: str, session_id: Optional[str], recording: bool,
           timeout_seconds: float) -> bool:
    """Tell the server whether a session is being recorded; returns whether one should be.

    If the server cannot be reached or refuses the report, the answer is ``recording``.
    """
    body = {"capture": "running" if session_id else "paused", "session_id": session_id}
    try:
        response = httpx.put(report_url, json=body, timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()["capture"]
    except httpx.HTTPError as exc:
        logger.warning("could not report to %s: %s", report_url, exc)
        return recording


def main(config: CaptureConfig, server_url: Optional[str] = None,
         control: Optional[ControlConfig] = None) -> int:
    """
    Args:
        server_url: the server the session is uploaded to and that says
            whether to record; None records without either.
    """
    logging.basicConfig(
        level=logging.DEBUG if config.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    control = control or ControlConfig()

    try:
        backend = load_backend(config.backend, fps=config.fps)
    except BackendUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Imported late so backend errors do not pay for napsack's imports.
    from capture.recorder import Recorder

    device_id = resolve_device_id() if server_url is not None else None
    recorder: Optional[Recorder] = None
    uploader: Optional[AggregationUploader] = None
    complete = True

    def start() -> None:
        nonlocal recorder, uploader
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
        if server_url is not None:
            uploader = AggregationUploader(recorder.session_dir, f"{server_url}/events", device_id)
            recorder.on_aggregation = uploader.on_aggregation
        recorder.start()
        print(f"Recording to {recorder.session_dir}. Press Ctrl+C to stop.", file=sys.stderr)

    def stop() -> None:
        nonlocal recorder, uploader, complete
        try:
            complete = recorder.stop() and complete
            if uploader is not None:
                uploader.close()
        finally:
            print(f"Session saved to {recorder.session_dir} "
                  f"({recorder.processed_aggregations} aggregations).", file=sys.stderr)
            print(recorder.session_dir)
            recorder = uploader = None

    done = threading.Event()
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.signal(signum, lambda *_: done.set()) for signum in signals}
    if config.duration is not None:
        timer = threading.Timer(config.duration, done.set)
        timer.daemon = True
        timer.start()
    if backend.name == "fake":
        print("NOTE: the fake backend records generated frames and a scripted "
              "'h', 'i', Enter, click, scroll loop.\n"
              "      Your real keyboard, mouse and screen are NOT being read.",
              file=sys.stderr)
    try:
        while True:
            recording = recorder is not None
            if server_url is None:
                wanted = True
            else:
                wanted = report(f"{server_url}/control/devices/{device_id}/report",
                                uploader.session_id if uploader else None, recording,
                                control.poll_seconds)
            if wanted and not recording:
                start()
            elif recording and not wanted:
                stop()
            if done.wait(control.poll_seconds if server_url is not None else None):
                break
    finally:
        # A second Ctrl+C must not interrupt the flush.
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if recorder is not None:
                stop()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    if not complete:
        print("warning: some events were not captured in any aggregation", file=sys.stderr)
    return 0


def run() -> int:
    # This device's settings may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    start_parent_watch()
    if not config.annotate.live:
        return main(config.capture)
    return main(config.capture, server_url=f"http://{server_host()}:{config.server.port}",
                control=config.control)


if __name__ == "__main__":
    sys.exit(run())
