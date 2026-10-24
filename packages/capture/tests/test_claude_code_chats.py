"""Claude Code's transcripts read as conversations: what is a turn, what is Claude Code's own note."""

import json

from capture.chats.claude_code import default_projects_dir, read_conversations, read_transcript
from juno.conversations import CLAUDE_CODE

T0 = "2026-03-02T12:00:00.000Z"
T1 = "2026-03-02T12:00:05.000Z"
T2 = "2026-03-02T12:00:09.000Z"
T3 = "2026-03-02T12:01:00.000Z"


def record(kind, content, at, entrypoint="cli", **extra):
    message = {"role": kind, "content": content}
    return {"type": kind, "message": message, "timestamp": at, "cwd": "/home/me/proj",
            "sessionId": "ignored", "entrypoint": entrypoint, "isSidechain": False, **extra}


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in records) + "\n")


def test_a_transcript_becomes_turns_of_the_user_and_claude_without_claude_codes_own_notes(tmp_path):
    path = tmp_path / "projects" / "-home-me-proj" / "abc-123.jsonl"
    write(path, [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": T0},
        record("user", "<local-command-caveat>ran /model</local-command-caveat>", T0, promptId="p0"),
        record("user", "fix the failing test", T0, promptId="p1"),
        record("assistant", [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Looking."},
                             {"type": "tool_use", "name": "Bash", "input": {}}], T1),
        record("user", [{"type": "tool_result", "content": "ok"}], T1, promptId="p1"),
        record("assistant", [{"type": "text", "text": "Fixed it."}], T2),
        record("user", "[Request interrupted by user]", T2, promptId="p2"),
        record("user", "[Image: source: /tmp/x.png]", T2, promptId="p3"),
        record("user", "a subagent's prompt", T2, promptId="p4", isSidechain=True),
        record("user", "an old prompt with no id", T2),
        "not json at all",
        record("user", "thanks, now the docs", T3, promptId="p5", entrypoint="claude-desktop"),
    ])

    conversation = read_transcript(path, "laptop")

    assert conversation.harness == CLAUDE_CODE
    assert conversation.session_id == "abc-123"
    assert conversation.device_id == "laptop"
    assert conversation.cwd == "/home/me/proj"
    assert conversation.entrypoint == "cli"
    assert conversation.host_session_id is None
    assert [(t.role, t.text, t.id) for t in conversation.turns] == [
        ("user", "fix the failing test", "p1"),
        ("assistant", "Looking.\nFixed it.", None),
        ("user", "thanks, now the docs", "p5")]
    assert conversation.turns[0].time == 1772452800.0  # 2026-03-02T12:00:00Z
    assert conversation.turns[1].time == 1772452805.0


def test_a_transcript_of_an_origin_the_device_cannot_reopen_or_without_a_users_turn_is_none(tmp_path):
    sdk = tmp_path / "p" / "sdk.jsonl"
    write(sdk, [record("user", "print 1", T0, promptId="p1", entrypoint="sdk-cli"),
                record("assistant", "1", T1, entrypoint="sdk-cli")])
    assert read_transcript(sdk, "laptop") is None

    notes = tmp_path / "p" / "notes.jsonl"
    write(notes, [record("user", "<command-name>/clear</command-name>", T0, promptId="p1"),
                  record("assistant", "Cleared.", T1)])
    assert read_transcript(notes, "laptop") is None


def test_every_project_is_read_oldest_conversation_first_and_other_files_are_left_alone(tmp_path):
    projects = tmp_path / "projects"
    write(projects / "-home-me-a" / "later.jsonl", [record("user", "second", T3, promptId="p1")])
    write(projects / "-home-me-b" / "earlier.jsonl", [record("user", "first", T0, promptId="p1")])
    write(projects / "-home-me-b" / "notes.txt", ["nothing"])
    (projects / "-home-me-b" / "earlier").mkdir()

    conversations = read_conversations(projects, "laptop")

    assert [c.session_id for c in conversations] == ["earlier", "later"]


def test_the_default_source_follows_claude_codes_config_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert default_projects_dir() == tmp_path / "cc" / "projects"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert default_projects_dir().parts[-2:] == (".claude", "projects")
