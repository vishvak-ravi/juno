"""Parent-death watchdog for the processes ``python -m juno`` starts.

Each one runs in its own session, so nothing else stops it if the app is
killed before it can stop them itself.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0


def watch_parent() -> None:
    """SIGTERM this process once its parent dies (it is then re-parented)."""
    ppid = os.getppid()
    while os.getppid() == ppid:
        time.sleep(POLL_INTERVAL_S)
    logger.info("Parent process died, shutting down")
    os.kill(os.getpid(), signal.SIGTERM)


def start_parent_watch() -> None:
    threading.Thread(target=watch_parent, daemon=True, name="parent-watch").start()
