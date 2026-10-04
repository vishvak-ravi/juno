import pytest

from capture.sources.wayland.evdev_keys import PRESS, RELEASE, REPEAT, KeyTranslator, button_name


def tap(translator, *names):
    """Press then release each key; return the names of the presses."""
    out = []
    for name in names:
        out.append(translator.feed(name, PRESS))
        translator.feed(name, RELEASE)
    return [name for name, _ in out]


def test_ctrl_p_then_ssh():
    t = KeyTranslator()
    events = [
        t.feed("KEY_LEFTCTRL", PRESS), t.feed("KEY_P", PRESS),
        t.feed("KEY_P", RELEASE), t.feed("KEY_LEFTCTRL", RELEASE),
        t.feed("KEY_S", PRESS), t.feed("KEY_S", RELEASE),
        t.feed("KEY_S", PRESS), t.feed("KEY_S", RELEASE),
        t.feed("KEY_H", PRESS), t.feed("KEY_H", RELEASE),
    ]
    assert events == [
        ("Key.ctrl", True), ("p", True), ("p", False), ("Key.ctrl", False),
        ("s", True), ("s", False), ("s", True), ("s", False),
        ("h", True), ("h", False),
    ]


def test_shift_changes_the_character_and_release_restores_it():
    t = KeyTranslator()
    t.feed("KEY_LEFTSHIFT", PRESS)
    assert tap(t, "KEY_A", "KEY_1", "KEY_SLASH", "KEY_APOSTROPHE") == ["A", "!", "?", '"']
    t.feed("KEY_LEFTSHIFT", RELEASE)
    assert tap(t, "KEY_A", "KEY_1", "KEY_SLASH") == ["a", "1", "/"]


def test_either_shift_counts_until_both_are_up():
    t = KeyTranslator()
    t.feed("KEY_LEFTSHIFT", PRESS)
    t.feed("KEY_RIGHTSHIFT", PRESS)
    t.feed("KEY_LEFTSHIFT", RELEASE)
    assert tap(t, "KEY_A") == ["A"]
    t.feed("KEY_RIGHTSHIFT", RELEASE)
    assert tap(t, "KEY_A") == ["a"]


def test_caps_lock_affects_letters_only_and_shift_inverts_it():
    t = KeyTranslator()
    assert t.feed("KEY_CAPSLOCK", PRESS) == ("Key.caps_lock", True)
    t.feed("KEY_CAPSLOCK", RELEASE)
    assert tap(t, "KEY_A", "KEY_1") == ["A", "1"]
    t.feed("KEY_LEFTSHIFT", PRESS)
    assert tap(t, "KEY_A", "KEY_1") == ["a", "!"]


def test_initial_caps_lock_state():
    assert tap(KeyTranslator(caps_lock=True), "KEY_Q") == ["Q"]


@pytest.mark.parametrize("name, expected", [
    ("KEY_ENTER", "Key.enter"), ("KEY_KPENTER", "Key.enter"), ("KEY_SPACE", "Key.space"),
    ("KEY_TAB", "Key.tab"), ("KEY_BACKSPACE", "Key.backspace"), ("KEY_ESC", "Key.esc"),
    ("KEY_LEFTMETA", "Key.cmd"), ("KEY_RIGHTCTRL", "Key.ctrl_r"),
    ("KEY_RIGHTALT", "Key.alt_r"), ("KEY_LEFTALT", "Key.alt"),
    ("KEY_PAGEUP", "Key.page_up"), ("KEY_F5", "Key.f5"), ("KEY_UP", "Key.up"),
    ("KEY_KP7", "7"), ("KEY_KPPLUS", "+"),
])
def test_named_keys(name, expected):
    assert KeyTranslator().feed(name, PRESS) == (expected, True)


def test_held_key_repeats_as_presses_but_held_modifier_does_not():
    t = KeyTranslator()
    assert t.feed("KEY_A", REPEAT) == ("a", True)
    assert t.feed("KEY_BACKSPACE", REPEAT) == ("Key.backspace", True)
    assert t.feed("KEY_LEFTCTRL", REPEAT) is None
    assert t.feed("KEY_LEFTSHIFT", REPEAT) is None


def test_shift_auto_repeat_does_not_clear_shift():
    t = KeyTranslator()
    t.feed("KEY_LEFTSHIFT", PRESS)
    t.feed("KEY_LEFTSHIFT", REPEAT)
    assert tap(t, "KEY_A") == ["A"]


def test_evdev_alias_lists_are_accepted():
    # python-evdev reports some codes as several names.
    assert KeyTranslator().feed(["KEY_MIN_INTERESTING", "KEY_MUTE"], PRESS) == \
        ("Key.media_volume_mute", True)


def test_unknown_key_is_reported_by_code():
    assert KeyTranslator().feed("KEY_PROG1", PRESS, code=148) == ("<148>", True)
    assert KeyTranslator().feed("KEY_PROG1", RELEASE) == ("<KEY_PROG1>", False)


def test_mouse_buttons_are_not_keys():
    assert KeyTranslator().feed(["BTN_LEFT", "BTN_MOUSE"], PRESS) is None
    assert button_name(["BTN_LEFT", "BTN_MOUSE"]) == "Button.left"
    assert button_name("BTN_RIGHT") == "Button.right"
    assert button_name("BTN_MIDDLE") == "Button.middle"
    assert button_name("KEY_A") is None
