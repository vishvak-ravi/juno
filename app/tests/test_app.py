"""The app loop (``python -m juno``), run as a real process tree."""

import http.client
import os
import signal
import socket
import subprocess
import sys
import time

import yaml

from capture.check import latest_session

HOST = "127.0.0.1"
TIMEOUT_S = 60
# How long the server gets to answer /api/status after the app starts.
SERVER_START_TIMEOUT_S = 10


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def api_answers(port: int) -> bool:
    connection = http.client.HTTPConnection(HOST, port, timeout=1.0)
    try:
        connection.request("GET", "/api/status")
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_for(condition, timeout: float = TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def start_app(tmp_path, port, **capture) -> subprocess.Popen:
    """Start the app on a fake-backend config; its stderr (and its children's) is piped."""
    config = {
        "server": {"port": port},
        "capture": {"backend": "fake", "data_dir": str(tmp_path), "fps": 20,
                    "screenshots": False, **capture},
        "annotate": {"live": False},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return subprocess.Popen(
        [sys.executable, "-m", "juno"],
        # Set here, so a JUNO_SERVER_HOST in a .env file does not apply.
        env={**os.environ, "JUNO_CONFIG_PATH": str(path), "JUNO_SERVER_HOST": HOST},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )


def finish(app: subprocess.Popen) -> str:
    """Wait for the app and both of its processes to exit; return what they logged."""
    try:
        # The pipe only reaches EOF once the server and the recorder are gone too.
        return app.communicate(timeout=TIMEOUT_S)[1]
    finally:
        app.kill()


def test_app_runs_the_server_and_the_recorder(tmp_path):
    port = free_port()
    # The recording lasts as long as the server may take to start, so its end
    # cannot stop the app before the server answers.
    app = start_app(tmp_path, port, duration=SERVER_START_TIMEOUT_S)
    try:
        assert wait_for(lambda: api_answers(port), SERVER_START_TIMEOUT_S)
        assert wait_for(lambda: latest_session(tmp_path) is not None)
    finally:
        log = finish(app)

    # The recorder reaching its duration ends the app, which stops the server.
    assert app.returncode == 0, log
    assert "Session saved" in log
    assert not api_answers(port)


def test_stopping_the_app_stops_both_processes(tmp_path):
    port = free_port()
    app = start_app(tmp_path, port)
    try:
        assert wait_for(lambda: api_answers(port))
        assert wait_for(lambda: latest_session(tmp_path) is not None)
        app.send_signal(signal.SIGINT)
    finally:
        log = finish(app)

    assert app.returncode == 0, log
    assert "Session saved" in log  # the recorder was given time to flush
    assert not api_answers(port)


def test_app_exits_when_the_server_cannot_start(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((HOST, 0))
        taken.listen()
        app = start_app(tmp_path, taken.getsockname()[1])
        log = finish(app)

    assert app.returncode not in (0, None), log
    assert "server exited" in log


def test_processes_exit_when_the_app_is_killed(tmp_path):
    port = free_port()
    app = start_app(tmp_path, port)
    try:
        assert wait_for(lambda: api_answers(port))
        assert wait_for(lambda: latest_session(tmp_path) is not None)
        app.kill()
    finally:
        log = finish(app)

    assert "Session saved" in log
    assert not api_answers(port)


def test_app_rejects_an_invalid_config(tmp_path):
    app = start_app(tmp_path, free_port(), fsp=5)
    log = finish(app)

    assert app.returncode == 2
    assert "fsp" in log
    assert latest_session(tmp_path) is None
