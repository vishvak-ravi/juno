"""Installing the Claude Code prompt hook into Claude Code's user settings."""

import json

import pytest

from capture.claude_hook import main


@pytest.fixture
def claude_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("JUNO_SERVER_HOST", "10.0.0.2")
    monkeypatch.setenv("JUNO_DEVICE_ID", "laptop")
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9000\nclaude_hook:\n  timeout_seconds: 4\n")
    monkeypatch.setenv("JUNO_CONFIG_PATH", str(config))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "claude"


OTHER = {"model": "opus", "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}


def settings(claude_dir):
    return json.loads((claude_dir / "settings.json").read_text())


def test_install_adds_one_hook_to_this_devices_server_and_keeps_the_rest(claude_dir):
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(OTHER))
    assert main(["install"]) == 0
    assert main(["install"]) == 0

    installed = settings(claude_dir)
    assert installed["model"] == "opus" and installed["hooks"]["Stop"] == OTHER["hooks"]["Stop"]
    groups = installed["hooks"]["UserPromptSubmit"]
    assert groups[0] == OTHER["hooks"]["UserPromptSubmit"][0]
    assert groups[1:] == [{"hooks": [{
        "type": "http", "url": "http://10.0.0.2:9000/hooks/claude-code?device=laptop", "timeout": 4,
        "headers": {"X-Claude-Code-Entrypoint": "$CLAUDE_CODE_ENTRYPOINT",
                    "X-Claude-Code-Remote": "$CLAUDE_CODE_REMOTE",
                    "X-Claude-Code-Host-Session": "$CLAUDE_CODE_HOST_SESSION_ID"},
        "allowedEnvVars": ["CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_REMOTE", "CLAUDE_CODE_HOST_SESSION_ID"]}]}]


def test_uninstall_leaves_the_settings_as_they_were(claude_dir):
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(OTHER))
    main(["install"])
    assert main(["uninstall"]) == 0
    assert settings(claude_dir) == OTHER


def test_install_creates_the_settings_file(claude_dir):
    assert main(["install"]) == 0
    assert len(settings(claude_dir)["hooks"]["UserPromptSubmit"]) == 1


def test_settings_that_are_not_json_are_left_alone(claude_dir):
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{ not json")
    assert main(["install"]) == 2
    assert (claude_dir / "settings.json").read_text() == "{ not json"
