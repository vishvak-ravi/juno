from pathlib import Path

import pytest

from juno.config import (
    Config,
    ConfigError,
    default_data_dir,
    load_config,
    resolve_data_dir,
)

SHIPPED_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_default_data_dir_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / "juno"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / ".local" / "share" / "juno"


def test_shipped_config_matches_the_defaults():
    assert load_config(SHIPPED_CONFIG) == Config()


def test_keys_left_out_take_the_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("capture:\n  fps: 9\n  max_res: [640, 360]\n")
    config = load_config(path)
    assert config.capture.fps == 9
    assert config.capture.max_res == (640, 360)
    assert config.capture.buffer_seconds == Config().capture.buffer_seconds
    assert config.server == Config().server

    path.write_text("")
    assert load_config(path) == Config()


def test_config_path_comes_from_the_environment(monkeypatch, tmp_path):
    path = tmp_path / "elsewhere.yaml"
    path.write_text("server:\n  port: 9123\n")
    monkeypatch.setenv("JUNO_CONFIG_PATH", str(path))
    assert load_config().server.port == 9123

    monkeypatch.delenv("JUNO_CONFIG_PATH")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 9124\n")
    assert load_config().server.port == 9124


def test_data_dir_defaults_and_expands_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_data_dir(Config().capture) == tmp_path / "xdg" / "juno"

    path = tmp_path / "config.yaml"
    path.write_text("capture:\n  data_dir: ~/recordings\n")
    assert resolve_data_dir(load_config(path).capture) == tmp_path / "recordings"


@pytest.mark.parametrize("text", [
    "capture:\n  fsp: 5\n",       # misspelled key
    "server:\n  port: none\n",    # wrong type
    "- not\n- a mapping\n",
    "capture: [unclosed\n",       # not YAML
])
def test_invalid_config_is_rejected(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="config.yaml"):
        load_config(path)


def test_missing_config_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="could not read"):
        load_config(tmp_path / "config.yaml")
