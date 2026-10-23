import signal
import threading

import httpx

from capture.__main__ import main, run
from capture.check import check_session, latest_session
from juno.config import CaptureConfig
from test_upload import running_server, wait_for


def test_an_unknown_backend_in_the_device_settings_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JUNO_DATA_DIR", str(tmp_path))
    (tmp_path / "device.json").write_text('{"capture": {"backend": "windows"}}')
    assert run() == 2
    assert "capture.backend" in capsys.readouterr().err
    assert not (tmp_path / "screen").exists()


def test_fake_backend_records_a_session_that_passes_the_check(tmp_path, capsys):
    before = signal.getsignal(signal.SIGINT)
    code = main(CaptureConfig(backend="fake", fps=20, duration=3.5), tmp_path)
    assert code == 0
    assert signal.getsignal(signal.SIGINT) is before  # Ctrl+C still works afterwards

    session = latest_session(tmp_path)
    assert capsys.readouterr().out.strip() == str(session)
    report = check_session(session)
    assert report.ok, report.format()
    assert report.events["mouse_down"] >= 1
    assert report.events["key_press"] >= 3
    assert report.events["mouse_scroll"] >= 2
    assert "mouse_move" not in report.events


def test_switching_a_device_off_and_on_ends_its_session_and_starts_a_new_one(tmp_path, monkeypatch,
                                                                            capsys):
    monkeypatch.setenv("JUNO_DEVICE_ID", "laptop")
    server, thread, port = running_server(tmp_path)
    url = f"http://127.0.0.1:{port}"

    def reported():
        device = httpx.get(f"{url}/control").json()["devices"].get("laptop") or {}
        return (device.get("reported") or {}).get("capture")

    def switch():
        wait_for(lambda: reported() == "running", "recording")
        httpx.patch(f"{url}/control/devices/laptop", json={"capture": False})
        wait_for(lambda: reported() == "paused", "the pause")
        httpx.patch(f"{url}/control/devices/laptop", json={"capture": True})
        wait_for(lambda: reported() == "running", "recording again")

    switching = threading.Thread(target=switch)
    switching.start()
    try:
        code = main(CaptureConfig(backend="fake", fps=20, duration=8.0), tmp_path / "recorder",
                    server_url=url)
    finally:
        switching.join()
        server.should_exit = True
        thread.join(timeout=5.0)

    assert code == 0
    assert len(capsys.readouterr().out.split()) == 2
