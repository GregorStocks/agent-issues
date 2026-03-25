"""Tests for agent_issues.json5_writer – especially word-wrapping."""

import pyjson5

from agent_issues.json5_writer import dumps_json5


def _roundtrip(obj, **kwargs):
    """Serialize with dumps_json5 then parse back and return both text + data."""
    text = dumps_json5(obj, **kwargs)
    parsed = pyjson5.loads(text)
    return text, parsed


# ---- basic behaviour (pre-existing) --------------------------------------


def test_short_strings_unchanged():
    obj = {"title": "Short", "desc": "Also short"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    for line in text.split("\n"):
        assert len(line) <= 80


def test_trailing_commas():
    text = dumps_json5({"a": 1})
    assert "1," in text


def test_multiline_expansion():
    obj = {"msg": "line1\nline2"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "\\n\\\n" in text  # \n followed by line continuation


# ---- word-wrapping -------------------------------------------------------


def test_long_string_gets_wrapped():
    long_desc = (
        "When users try to log in with SSO credentials and their account "
        "has been deactivated, the system shows a generic error message "
        "instead of a specific deactivation notice. This makes it "
        "impossible for users to understand why they cannot log in."
    )
    obj = {"description": long_desc}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    for line in text.split("\n"):
        assert len(line) <= 80, f"Line too long ({len(line)}): {line!r}"


def test_wrap_preserves_value():
    """The wrapped output must parse back to the identical string."""
    long_val = "word " * 100  # ~500 chars
    obj = {"key": long_val.strip()}
    text, parsed = _roundtrip(obj)
    assert parsed == obj


def test_wrap_disabled_when_zero():
    long_val = "word " * 50
    obj = {"key": long_val.strip()}
    text = dumps_json5(obj, wrap_width=0)
    # Should be a single long line for the value.
    value_lines = [l for l in text.split("\n") if "word" in l]
    assert len(value_lines) == 1


def test_wrap_with_multiline_string():
    """A string with \\n AND long paragraphs should expand newlines AND wrap."""
    para1 = "Short intro."
    para2 = "word " * 40  # ~200 chars
    obj = {"description": f"{para1}\n{para2.strip()}"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    for line in text.split("\n"):
        assert len(line) <= 80, f"Line too long ({len(line)}): {line!r}"


def test_no_wrap_when_no_spaces():
    """A long string without spaces cannot be wrapped – must survive."""
    long_val = "x" * 200
    obj = {"key": long_val}
    text, parsed = _roundtrip(obj)
    assert parsed == obj  # value preserved even though line is long


def test_wrap_respects_custom_width():
    long_val = "word " * 50
    obj = {"key": long_val.strip()}
    text, parsed = _roundtrip(obj, wrap_width=60)
    assert parsed == obj
    for line in text.split("\n"):
        assert len(line) <= 60, f"Line too long ({len(line)}): {line!r}"


def test_wrap_with_escape_sequences():
    """Escape sequences in strings must not be broken by wrapping."""
    val = 'She said \\"hello\\" and then ' + "word " * 30
    obj = {"key": val.strip()}
    text, parsed = _roundtrip(obj)
    assert parsed == obj


def test_wrap_idempotent():
    """Wrapping already-wrapped output should not change it."""
    long_val = "word " * 80
    obj = {"key": long_val.strip()}
    text1 = dumps_json5(obj)
    parsed1 = pyjson5.loads(text1)
    text2 = dumps_json5(parsed1)
    assert text1 == text2
