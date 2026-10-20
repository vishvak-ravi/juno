"""Sends recorded sessions to the server, one aggregation at a time.

Row ``n`` of a session's ``raw_aggregations.jsonl`` goes out as
``PUT <events_url>/<device id>-<session name>-<n>`` with the row and its
screenshot (see ``server.routes.events``).

While recording, the recorder calls ``AggregationUploader.on_aggregation``
after each aggregation is written. The uploader's own thread then sends every
complete row it has not sent yet, in order, so recording never waits on the
server. A row that cannot be sent stays where it is and is tried again, with
the rows after it, the next time an aggregation is written.

``python -m capture.upload`` copies every session already on this computer to
the server, without asking for annotations; run it again to continue a copy
that stopped. Settings come from the YAML config (see ``juno.config``).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv

from juno.config import (
    ConfigError,
    load_config,
    resolve_data_dir,
    resolve_device_id,
    server_host,
    sessions_dir,
)
from juno.events import event_id_of
from juno.sessions import AGGREGATIONS_FILE

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = 30.0


def complete_rows(session_dir: Path) -> list[bytes]:
    """The session's rows written so far; a row counts once its newline is written."""
    try:
        written = (session_dir / AGGREGATIONS_FILE).read_bytes()
    except FileNotFoundError:
        return []
    return written[:written.rfind(b"\n") + 1].splitlines()


def upload_rows(session_dir: Path, events_url: str, session_id: str, sent: int,
                annotate: bool = True,
                timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Send the session's rows from row ``sent`` on, stopping at the first that fails.

    Args:
        session_id: the session's id on the server, ``<device id>-<session name>``.
        sent: how many rows the server is believed to have.
        annotate: whether the server annotates each chunk these rows complete.

    Returns:
        How many rows the server has now.
    """
    rows = complete_rows(session_dir)
    while sent < len(rows):
        row = rows[sent]
        url = f"{events_url}/{event_id_of(session_id, sent)}"
        screenshot_path = json.loads(row).get("screenshot_path")
        # (None, text) is a plain form field; the body is multipart either way.
        files = {"aggregation": (None, row.decode())}
        if screenshot_path and Path(screenshot_path).is_file():
            files["screenshot"] = (Path(screenshot_path).name,
                                   Path(screenshot_path).read_bytes(), "image/png")
        try:
            response = httpx.put(url, files=files, params={"annotate": annotate},
                                 timeout=timeout_seconds)
        except httpx.HTTPError as exc:
            logger.warning("could not upload %s: %s", url, exc)
            return sent
        if response.status_code == 409:
            # The server holds a different number of rows than was believed.
            sent = response.json()["detail"]["expected_row"]
        elif response.is_success:
            sent += 1
        else:
            logger.warning("could not upload %s: %s %s", url, response.status_code,
                           response.text[:500])
            return sent
    return sent


class AggregationUploader:

    def __init__(self, session_dir: Path, events_url: str, device_id: str,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        """
        Args:
            events_url: the server's events collection, e.g.
                ``http://127.0.0.1:8765/events``.
            device_id: the name the server stores this computer's sessions under.
        """
        self.session_dir = Path(session_dir)
        self.events_url = events_url.rstrip("/")
        self.session_id = f"{device_id}-{self.session_dir.name}"
        self.timeout_seconds = timeout_seconds
        self._sent = 0  # rows the server has
        self._wake = threading.Event()
        self._closing = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="aggregation-uploader")
        self._thread.start()

    def on_aggregation(self, processed=None) -> None:
        self._wake.set()

    def close(self, timeout_seconds: float = 60.0) -> None:
        """Send what is left, then stop."""
        self._closing = True
        self._wake.set()
        self._thread.join(timeout=timeout_seconds)

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            closing = self._closing
            try:
                self._sent = upload_rows(self.session_dir, self.events_url,
                                         self.session_id, self._sent,
                                         timeout_seconds=self.timeout_seconds)
            except Exception:
                logger.exception("upload of %s failed", self.session_id)
            if closing:
                return


def copy_sessions(recorded_dir: Path, events_url: str, device_id: str) -> bool:
    """Send every session in ``recorded_dir`` to the server, oldest first, unannotated.

    A session the server already has part of continues after the rows it has.
    Returns whether every row is now on the server.
    """
    complete = True
    for session_dir in sorted(p for p in recorded_dir.glob("*") if p.is_dir()):
        rows = len(complete_rows(session_dir))
        session_id = f"{device_id}-{session_dir.name}"
        # Offering the last row first makes the server name the row it is missing (409).
        sent = upload_rows(session_dir, events_url, session_id,
                           max(rows - 1, 0), annotate=False)
        print(f"{session_id}: {sent} of {rows} aggregations on the server", file=sys.stderr)
        complete = complete and sent == rows
    return complete


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    # This device's settings may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config()
        device_id = resolve_device_id()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    url = f"http://{server_host()}:{config.server.port}/events"
    recorded_dir = sessions_dir(resolve_data_dir(config.capture))
    if not copy_sessions(recorded_dir, url, device_id):
        print("error: not everything was copied; run again to continue", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
