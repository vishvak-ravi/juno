import pytest

from annotate.request import AnnotationFrame, AnnotationRequest
from annotate.sanitize import ALGORITHM, PATTERNS, VERSION, sanitize


def events(*key_lines, label="Frame 1"):
    """Event text laid out as napsack's ``Aggregation.to_prompt`` writes it."""
    actions = "\n\t".join(key_lines)
    return f"\n    {label} Event:\n    Cursor position: (250, 500)\n    Actions:\n    \t{actions}\n        "


def request(*frame_events):
    frames = [AnnotationFrame(label=f"Frame {number}", screenshot_path=f"{number}.png",
                              cursor=None, monitor=None, events=text)
              for number, text in enumerate(frame_events, start=1)]
    return AnnotationRequest(frames=frames, prompt="Describe.")


def keys(text):
    return "|".join(text)


def test_a_request_without_secrets_keeps_its_text_and_gains_the_attribution():
    original = request(events("Keys pressed: g|i|t|space|s|t|a|t|u|s|enter",
                              "Mouse clicked left at (250, 500)"),
                       events("No actions recorded."))
    assert original.sanitization is None

    cleaned = sanitize(original)

    assert [frame.events for frame in cleaned.frames] == [f.events for f in original.frames]
    assert cleaned.prompt == original.prompt
    assert cleaned.sanitization.algorithm == ALGORITHM
    assert cleaned.sanitization.version == VERSION
    assert cleaned.sanitization.text is True and cleaned.sanitization.images is False
    assert cleaned.sanitization.redactions == {name: 0 for name, _ in PATTERNS}
    assert original.sanitization is None  # the original is left alone


@pytest.mark.parametrize("name, secret", [
    ("email", "someone@example.org"),
    ("api_key", "sk-" + "a1B2" * 6),
    ("api_key", "AIza" + "x" * 35),
    ("api_key", "ghp_" + "a1" * 18),
    ("api_key", "AKIA" + "A1" * 8),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlMTIz"),
    ("card_number", "4111111111111111"),
    ("long_token", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"),
])
def test_a_typed_secret_is_replaced_and_counted(name, secret):
    cleaned = sanitize(request(events(f"Keys pressed: c|d|space|{keys(secret)}|enter")))

    text = str(cleaned)
    assert f"Keys pressed: c|d|space|[REDACTED:{name}]|enter" in text
    assert secret not in text and keys(secret) not in text
    assert cleaned.sanitization.redactions[name] == 1
    assert sum(cleaned.sanitization.redactions.values()) == 1


def test_shift_between_characters_does_not_hide_a_secret():
    typed = "s|k|-|shift|A|b|c|1|shift|D|e|f|2|g|h|i|3|j|k|l|4|m|n|o|5"
    cleaned = sanitize(request(events(f"Key(s) pressed: {typed}")))
    assert "Key(s) pressed: [REDACTED:api_key]" in str(cleaned)
    assert cleaned.sanitization.redactions["api_key"] == 1


def test_secrets_are_counted_across_frames_and_the_rest_is_kept():
    cleaned = sanitize(request(
        events(f"Keys pressed: t|o|space|{keys('a@b.example')}|space|a|n|d|space|"
               f"{keys('c@d.example')}", "Scrolled down"),
        events(f"Keys pressed: {keys('4111111111111111')}")))
    first, second = cleaned.frames
    assert "Keys pressed: t|o|space|[REDACTED:email]|space|a|n|d|space|[REDACTED:email]" in first.events
    assert "Scrolled down" in first.events and "Cursor position: (250, 500)" in first.events
    assert "Keys pressed: [REDACTED:card_number]" in second.events
    assert cleaned.sanitization.redactions["email"] == 2
    assert cleaned.sanitization.redactions["card_number"] == 1


@pytest.mark.parametrize("typed", [
    keys("1234567890123456"),  # sixteen digits that are not a card number
    keys("abcdefghijklmnopqrstuvwxyzabcdefghijkl"),  # long, but no digit
    keys("user@localhost"),
    "a|||b",  # the | key between two letters
])
def test_text_that_only_resembles_a_secret_is_kept(typed):
    line = f"Keys pressed: {typed}"
    cleaned = sanitize(request(events(line)))
    assert line in str(cleaned)
    assert sum(cleaned.sanitization.redactions.values()) == 0
