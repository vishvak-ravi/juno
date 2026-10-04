import signal

from capture.__main__ import main
from capture.check import check_session, latest_session
from juno.config import CaptureConfig


def test_unavailable_backend_exits_with_a_clear_error(tmp_path, capsys):
    assert main(CaptureConfig(backend="windows", data_dir=tmp_path)) == 2
    assert "Unknown backend" in capsys.readouterr().err
    assert not (tmp_path / "screen").exists()


def test_fake_backend_records_a_session_that_passes_the_check(tmp_path, capsys):
    before = signal.getsignal(signal.SIGINT)
    code = main(CaptureConfig(backend="fake", data_dir=tmp_path, fps=20, duration=3.5))
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
