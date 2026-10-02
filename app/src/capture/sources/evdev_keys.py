"""Translate Linux evdev key codes into the names pynput reports.

evdev reports which physical key moved (``KEY_1``), not what it types. Tada
logs what pynput gives it: the typed character (``"1"``, or ``"!"`` with
shift), or ``"Key.<name>"`` for everything else. This module does that
translation, so events read from ``/dev/input`` log the same text Tada would.

Pure Python on code *names*, so it needs neither the evdev package nor a
device. US layout only; another layout needs xkbcommon instead of these tables.
"""

from __future__ import annotations

from typing import Iterable, Optional, Union

# evdev ``value`` of an EV_KEY event
RELEASE, PRESS, REPEAT = 0, 1, 2

_LETTERS = "abcdefghijklmnopqrstuvwxyz"

# name -> (plain, shifted)
_CHARS: dict[str, tuple[str, str]] = {
    **{f"KEY_{c.upper()}": (c, c.upper()) for c in _LETTERS},
    "KEY_1": ("1", "!"), "KEY_2": ("2", "@"), "KEY_3": ("3", "#"),
    "KEY_4": ("4", "$"), "KEY_5": ("5", "%"), "KEY_6": ("6", "^"),
    "KEY_7": ("7", "&"), "KEY_8": ("8", "*"), "KEY_9": ("9", "("),
    "KEY_0": ("0", ")"),
    "KEY_MINUS": ("-", "_"), "KEY_EQUAL": ("=", "+"),
    "KEY_LEFTBRACE": ("[", "{"), "KEY_RIGHTBRACE": ("]", "}"),
    "KEY_BACKSLASH": ("\\", "|"), "KEY_SEMICOLON": (";", ":"),
    "KEY_APOSTROPHE": ("'", '"'), "KEY_GRAVE": ("`", "~"),
    "KEY_COMMA": (",", "<"), "KEY_DOT": (".", ">"), "KEY_SLASH": ("/", "?"),
    **{f"KEY_KP{d}": (str(d), str(d)) for d in range(10)},
    "KEY_KPDOT": (".", "."), "KEY_KPSLASH": ("/", "/"), "KEY_KPASTERISK": ("*", "*"),
    "KEY_KPMINUS": ("-", "-"), "KEY_KPPLUS": ("+", "+"),
}

_MODIFIERS: dict[str, str] = {
    "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl_r",
    "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift_r",
    "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt_r",
    "KEY_LEFTMETA": "cmd", "KEY_RIGHTMETA": "cmd_r",
    "KEY_CAPSLOCK": "caps_lock", "KEY_NUMLOCK": "num_lock",
    "KEY_SCROLLLOCK": "scroll_lock",
}

_SPECIAL: dict[str, str] = {
    **_MODIFIERS,
    "KEY_ENTER": "enter", "KEY_KPENTER": "enter", "KEY_SPACE": "space",
    "KEY_TAB": "tab", "KEY_BACKSPACE": "backspace", "KEY_ESC": "esc",
    "KEY_DELETE": "delete", "KEY_INSERT": "insert",
    "KEY_HOME": "home", "KEY_END": "end",
    "KEY_PAGEUP": "page_up", "KEY_PAGEDOWN": "page_down",
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
    "KEY_SYSRQ": "print_screen", "KEY_PAUSE": "pause", "KEY_COMPOSE": "menu",
    "KEY_MUTE": "media_volume_mute", "KEY_VOLUMEUP": "media_volume_up",
    "KEY_VOLUMEDOWN": "media_volume_down", "KEY_PLAYPAUSE": "media_play_pause",
    "KEY_NEXTSONG": "media_next", "KEY_PREVIOUSSONG": "media_previous",
    **{f"KEY_F{n}": f"f{n}" for n in range(1, 21)},
}

BUTTONS: dict[str, str] = {
    "BTN_LEFT": "Button.left", "BTN_RIGHT": "Button.right",
    "BTN_MIDDLE": "Button.middle",
    "BTN_SIDE": "Button.button8", "BTN_EXTRA": "Button.button9",
}

_SHIFTS = {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"}


def _pick(names: Union[str, Iterable[str]], table) -> Optional[str]:
    """evdev gives one name or several aliases for a code; take the one we know."""
    if isinstance(names, str):
        return names if names in table else None
    return next((n for n in names if n in table), None)


def button_name(names: Union[str, Iterable[str]]) -> Optional[str]:
    """pynput button name for a ``BTN_*`` code, or None if it is not a mouse button."""
    known = _pick(names, BUTTONS)
    return BUTTONS[known] if known else None


class KeyTranslator:
    """Stateful, because the typed character depends on shift and caps lock."""

    def __init__(self, caps_lock: bool = False):
        self._shifts_down: set[str] = set()
        self._caps_lock = caps_lock

    def feed(self, names: Union[str, Iterable[str]], value: int,
             code: Optional[int] = None) -> Optional[tuple[str, bool]]:
        """Translate one EV_KEY event.

        Returns ``(pynput_name, pressed)``, or None when pynput would report
        nothing: a held modifier auto-repeating, or a mouse button (see
        ``button_name``). A held ordinary key repeats as presses, as under X.
        """
        if _pick(names, BUTTONS):
            return None
        pressed = value != RELEASE

        special = _pick(names, _SPECIAL)
        if special:
            if special in _MODIFIERS and value == REPEAT:
                return None
            if special in _SHIFTS:
                (self._shifts_down.add if pressed else self._shifts_down.discard)(special)
            elif special == "KEY_CAPSLOCK" and value == PRESS:
                self._caps_lock = not self._caps_lock
            return f"Key.{_SPECIAL[special]}", pressed

        char = _pick(names, _CHARS)
        if char:
            plain, shifted = _CHARS[char]
            shift = bool(self._shifts_down)
            if plain in _LETTERS and plain != shifted:
                shift ^= self._caps_lock  # caps lock affects letters only
            return (shifted if shift else plain), pressed

        # pynput prints a key it has no name for as its numeric code.
        if code is None:
            label = names if isinstance(names, str) else next(iter(names), "?")
            return f"<{label}>", pressed
        return f"<{code}>", pressed
