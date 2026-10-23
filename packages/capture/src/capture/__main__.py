"""Run the activity recorder: ``python -m capture``.

Settings come from the ``capture`` section of this computer's
``<data_dir>/device.json`` (see ``juno.config.DeviceSettings``). With
``capture.upload`` set, each aggregation is uploaded to the server at
JUNO_SERVER_URL as it is written (see ``capture.upload``), which annotates it,
and the recorder reports to the server's ``/control``, records or not as the
answer says and reports again when it says (see ``server.control``). Turning
recording off ends the session; turning it on again starts a new one.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

import httpx
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from capture.sources import BackendUnavailable, load_backend
from capture.upload import AggregationUploader
from capture.parent_watch import start_parent_watch
from juno.config import (
    CaptureConfig,
    ConfigError,
    ControlConfig,
    data_dir,
    resolve_device_id,
    server_url,
)
from juno.control import Instruction, Report
from juno.settings import device_settings

logger = logging.getLogger(__name__)

# Seconds to wait for the server's answer to a report; longer counts as unreachable.
REPORT_TIMEOUT_SECONDS = 5.0


def report(report_url: str, session_id: Optional[str],
           timeout_seconds: float = REPORT_TIMEOUT_SECONDS) -> Optional[Instruction]:
    """Tell the server whether a session is being recorded; returns its answer.

    None if the server cannot be reached, refuses the report or answers with
    something other than an ``Instruction``.
    """
    body = Report(capture="running" if session_id else "paused", session_id=session_id)
    try:
        response = httpx.put(report_url, json=body.model_dump(), timeout=timeout_seconds)
        response.raise_for_status()
        return Instruction.model_validate(response.json())
    except (httpx.HTTPError, ValidationError, ValueError) as exc:
        # ValueError: the body is not JSON.
        logger.warning("could not report to %s: %s", report_url, exc)
        return None


def main(config: CaptureConfig, base_dir: Path, server_url: Optional[str] = None) -> int:
    """
    Args:
        base_dir: the base directory; sessions go under ``screen/sessions/``.
        server_url: the server the session is uploaded to and that says
            whether to record and when to ask again; None records without either.
    """
    logging.basicConfig(
        level=logging.DEBUG if config.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Until the server's first answer says otherwise.
    poll_seconds = ControlConfig().poll_seconds

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
            data_dir=base_dir,
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
                answer = report(f"{server_url}/control/devices/{device_id}/report",
                                uploader.session_id if uploader else None)
                wanted = recording if answer is None else answer.capture
                if answer is not None:
                    poll_seconds = answer.poll_seconds
            if wanted and not recording:
                start()
            elif recording and not wanted:
                stop()
            if done.wait(poll_seconds if server_url is not None else None):
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
    # This device's environment (data directory, server, device id) may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        base_dir = data_dir()
        config = device_settings(base_dir).read().capture
        url = server_url() if config.upload else None
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    start_parent_watch()
    return main(config, base_dir, server_url=url)


if __name__ == "__main__":
    sys.exit(run())
