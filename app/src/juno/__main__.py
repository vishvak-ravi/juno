"""The Juno app: ``python -m juno``.

One loop that starts the API server and the activity recorder as subprocesses,
watches them, and stops both when either exits or the app is told to stop.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from typing import Mapping

from juno.config import CONFIG_PATH_ENV, ConfigError, config_path, load_config

logger = logging.getLogger("juno")

# Modules run as ``python -m <name>``, in start order.
PROCESSES = ("server", "capture")
POLL_INTERVAL_S = 0.5


def spawn(module: str, env: Mapping[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.DEVNULL,
        env=env,
        # Its own session, so a terminal Ctrl+C reaches only this loop, which
        # then stops the process itself.
        start_new_session=True,
    )


def stop_all(processes: Mapping[str, subprocess.Popen], timeout: float) -> None:
    """Stop the running processes one at a time, last started first.

    Each gets SIGTERM, then SIGKILL if it is still up after ``timeout`` seconds.
    The recorder goes before the server, so the requests it sends while
    flushing its last aggregations still find the server.
    """
    for name, process in reversed(list(processes.items())):
        if process.poll() is not None:
            continue
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("%s did not stop within %.0f s; killing it", name, timeout)
            process.kill()
            process.wait()


def exit_status(returncode: int) -> int:
    """A Popen return code as a shell exit status (128 + signal number if it was killed)."""
    return returncode if returncode >= 0 else 128 - returncode


def main() -> int:
    path = config_path()
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # The processes read the same file, wherever they are started from.
    env = {**os.environ, CONFIG_PATH_ENV: str(path.resolve())}

    stopping = threading.Event()
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.signal(signum, lambda *_: stopping.set()) for signum in signals}
    processes: dict[str, subprocess.Popen] = {}
    status = 0
    try:
        for module in PROCESSES:
            processes[module] = spawn(module, env)
            logger.info("started %s (pid %s)", module, processes[module].pid)
        while not stopping.wait(POLL_INTERVAL_S):
            exited = [name for name, process in processes.items()
                      if process.poll() is not None]
            if exited:
                returncode = processes[exited[0]].returncode
                logger.info("%s exited (code %s); stopping", exited[0], returncode)
                status = exit_status(returncode)
                break
    finally:
        stop_all(processes, config.stop_timeout_seconds)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return status


if __name__ == "__main__":
    sys.exit(main())
