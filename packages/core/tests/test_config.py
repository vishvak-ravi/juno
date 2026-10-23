import json

import pytest

from juno.config import (ConfigError, DeviceSettings, ServerSettings, data_dir,
                         resolve_device_id, server_url)
from juno.settings import SettingsError, device_settings, main, server_settings


def test_the_data_dir_comes_from_the_environment_then_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("JUNO_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert data_dir() == tmp_path / "xdg" / "juno"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert data_dir() == tmp_path / ".local" / "share" / "juno"
    monkeypatch.setenv("JUNO_DATA_DIR", "~/recordings")
    assert data_dir() == tmp_path / "recordings"


def test_the_server_url_is_built_from_the_host_and_port_unless_given(monkeypatch):
    for name in ("JUNO_SERVER_URL", "JUNO_SERVER_HOST", "JUNO_SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)
    assert server_url() == "http://127.0.0.1:8765"
    monkeypatch.setenv("JUNO_SERVER_HOST", "10.0.0.2")
    monkeypatch.setenv("JUNO_SERVER_PORT", "9000")
    assert server_url() == "http://10.0.0.2:9000"
    monkeypatch.setenv("JUNO_SERVER_URL", "https://juno.example/")
    assert server_url() == "https://juno.example"
    monkeypatch.delenv("JUNO_SERVER_URL")
    monkeypatch.setenv("JUNO_SERVER_PORT", "eighty")
    with pytest.raises(ConfigError, match="JUNO_SERVER_PORT"):
        server_url()


def test_a_device_id_must_be_lowercase_letters_and_digits(monkeypatch):
    monkeypatch.setenv("JUNO_DEVICE_ID", "laptop2")
    assert resolve_device_id() == "laptop2"
    monkeypatch.setenv("JUNO_DEVICE_ID", "Laptop-2")
    with pytest.raises(ConfigError, match="JUNO_DEVICE_ID"):
        resolve_device_id()


def test_without_a_file_every_setting_is_its_default(tmp_path):
    assert server_settings(tmp_path).read() == ServerSettings()
    assert device_settings(tmp_path).read() == DeviceSettings()


def test_the_file_keeps_only_what_differs_from_the_defaults(tmp_path):
    store = server_settings(tmp_path)
    settings = store.update({"annotate.model": "openai/local", "annotate.api_base": "http://h/v1",
                             "keypoints.moment_on": 0.7})
    assert settings.annotate.model == "openai/local"
    assert json.loads((tmp_path / "settings.json").read_text()) == {
        "annotate": {"model": "openai/local", "api_base": "http://h/v1"}}

    store.update(resets=["annotate.api_base"])
    assert store.read().annotate.api_base is None
    assert json.loads((tmp_path / "settings.json").read_text()) == {"annotate": {"model": "openai/local"}}


def test_a_default_changed_in_code_reaches_an_install_that_never_set_it(tmp_path):
    server_settings(tmp_path).update({"annotate.model": "openai/local"})
    assert server_settings(tmp_path).read().annotate.max_tokens == ServerSettings().annotate.max_tokens


@pytest.mark.parametrize("change, path", [
    ({"capture.fsp": 5}, "capture.fsp"),          # misspelled
    ({"capture": {"fps": 5}}, "capture"),         # a section, not a setting
    ({"capture.fps": "fast"}, "capture.fps"),     # wrong type
    ({"capture.backend": "windows"}, "capture.backend"),
])
def test_a_refused_change_names_the_setting_and_saves_nothing(tmp_path, change, path):
    store = device_settings(tmp_path)
    store.update({"capture.fps": 3})
    with pytest.raises(SettingsError) as refused:
        store.update(change)
    assert path in refused.value.errors
    assert store.read().capture.fps == 3


def test_an_invalid_file_is_reported_with_its_path(tmp_path):
    (tmp_path / "device.json").write_text('{"capture": {"fps": "fast"}}')
    with pytest.raises(ConfigError, match="device.json"):
        device_settings(tmp_path).read()
    (tmp_path / "settings.json").write_text("{not json")
    with pytest.raises(ConfigError, match="settings.json"):
        server_settings(tmp_path).read()


def test_the_command_line_sets_prints_and_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JUNO_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["device", "set", "capture.scale=0.5", "capture.max_res=[640, 360]"]) == 0
    fields = {f["path"]: f for s in json.loads(capsys.readouterr().out)["sections"] for f in s["fields"]}
    assert fields["capture.scale"]["value"] == 0.5 and fields["capture.max_res"]["value"] == [640, 360]
    assert fields["capture.backend"] == {**fields["capture.backend"], "kind": "enum", "default": "auto"}

    assert main(["server", "set", "keypoints.moment_on=high"]) == 1
    assert "keypoints.moment_on" in json.loads(capsys.readouterr().out)["errors"]


def test_a_key_shows_as_set_or_not_and_names_the_settings_that_need_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JUNO_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["server"]) == 0
    env = {v["name"]: v for v in json.loads(capsys.readouterr().out)["environment"]}
    assert env["GEMINI_API_KEY"]["set"] and env["GEMINI_API_KEY"]["value"] is None
    assert "annotate.model" in env["GEMINI_API_KEY"]["needed_by"]
    assert not env["TYPESAFE_API_KEY"]["set"] and env["TYPESAFE_API_KEY"]["needed_by"] == ["keypoints.enabled"]
