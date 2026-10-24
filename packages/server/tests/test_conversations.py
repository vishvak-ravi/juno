"""The stored conversations: one file per thread, listed by device and time."""

import pytest

from juno.conversations import CLAUDE_CODE, Conversation, Turn
from server.conversations import FileConversations


def conversation(device_id, session_id, times, harness=CLAUDE_CODE):
    turns = [Turn(role="user" if n % 2 == 0 else "assistant", text=f"turn {n}", time=at)
             for n, at in enumerate(times)]
    return Conversation(harness=harness, session_id=session_id, device_id=device_id, cwd="/w",
                        entrypoint="cli", turns=turns)


def test_a_saved_conversation_is_read_back_and_a_second_save_replaces_it(tmp_path):
    store = FileConversations(tmp_path)
    store.save(conversation("laptop", "s1", [10.0, 20.0]))
    store.save(conversation("laptop", "s1", [10.0, 20.0, 30.0]))

    stored = store.get("laptop", CLAUDE_CODE, "s1")
    assert [t.text for t in stored.turns] == ["turn 0", "turn 1", "turn 2"]
    assert store.get("laptop", CLAUDE_CODE, "s2") is None
    assert (tmp_path / "laptop" / "conversations" / CLAUDE_CODE / "s1.json").is_file()


def test_all_lists_by_first_turn_filtered_by_device_and_by_a_turn_in_the_range(tmp_path):
    store = FileConversations(tmp_path)
    store.save(conversation("laptop", "late", [300.0, 400.0]))
    store.save(conversation("laptop", "early", [100.0, 200.0]))
    store.save(conversation("phone", "other", [150.0]))
    (tmp_path / "laptop" / "conversations" / CLAUDE_CODE / "broken.json").write_text("{")

    assert [c.session_id for c in store.all()] == ["early", "other", "late"]
    assert [c.session_id for c in store.all(device_id="laptop")] == ["early", "late"]
    assert [c.session_id for c in store.all(since=250.0)] == ["late"]
    assert [c.session_id for c in store.all(until=120.0)] == ["early"]
    assert [c.session_id for c in store.all(since=180.0, until=320.0)] == ["early", "late"]


def test_names_that_would_leave_the_conversations_directory_are_refused(tmp_path):
    store = FileConversations(tmp_path)
    with pytest.raises(ValueError):
        store.get("laptop", CLAUDE_CODE, "../secrets")
    with pytest.raises(ValueError):
        store.save(conversation("Lap-top", "s1", [1.0]))
    with pytest.raises(ValueError):
        store.save(conversation("laptop", "s1", [1.0], harness="a/b"))
