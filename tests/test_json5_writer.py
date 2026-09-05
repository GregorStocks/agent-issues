"""Tests for sentence formatting and JSON5 value preservation."""

import pyjson5
import pytest

from agent_issues.json5_writer import dumps_json5


def _roundtrip(obj, **kwargs):
    """Serialize with dumps_json5 then parse back and return both text + data."""
    text = dumps_json5(obj, **kwargs)
    parsed = pyjson5.loads(text)
    return text, parsed


def test_short_strings_unchanged():
    obj = {"title": "Short", "desc": "Also short"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert '"title": "Short",' in text
    assert '"desc": "Also short",' in text


def test_trailing_commas():
    text = dumps_json5({"a": 1})
    assert "1," in text


def test_multiline_expansion():
    obj = {"msg": "line1\nline2"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "\\n\\\n" in text  # \n followed by line continuation


def test_long_description_breaks_between_sentences():
    long_desc = (
        "When users try to log in with SSO credentials and their account "
        "has been deactivated, the system shows a generic error message "
        "instead of a specific deactivation notice. This makes it "
        "impossible for users to understand why they cannot log in."
    )
    obj = {"description": long_desc}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    first, second = long_desc.split(" This")
    assert first + " \\\nThis" + second in text


def test_long_value_preserved():
    """The formatted output must parse back to the identical string."""
    long_val = "word " * 100  # ~500 chars
    obj = {"key": long_val.strip()}
    text, parsed = _roundtrip(obj)
    assert parsed == obj


def test_multiline_string_keeps_long_paragraph():
    """Expand explicit newlines while keeping each sentence on one source line."""
    para1 = "Short intro."
    para2 = "word " * 40  # ~200 chars
    obj = {"description": f"{para1}\n{para2.strip()}"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert para1 + "\\n\\\n" + para2.strip() in text


def test_multiline_string_keeps_long_first_paragraph():
    """Preserve a long first paragraph and its explicit newline."""
    para1 = "word " * 40  # ~200 chars – long first paragraph
    para2 = "Short second paragraph."
    obj = {"description": f"{para1.strip()}\n{para2}"}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert para1.strip() + "\\n\\\n" + para2 in text


def test_long_string_without_spaces_preserved():
    """Preserve a long string without spaces."""
    long_val = "x" * 200
    obj = {"key": long_val}
    text, parsed = _roundtrip(obj)
    assert parsed == obj  # value preserved even though line is long


def test_escape_sequences_preserved():
    """Escape sequences in strings must not be broken by formatting."""
    val = 'She said \\"hello\\" and then ' + "word " * 30
    obj = {"key": val.strip()}
    text, parsed = _roundtrip(obj)
    assert parsed == obj


def test_formatting_idempotent():
    """Formatting already-formatted output should not change it."""
    long_val = "word " * 80
    obj = {"key": long_val.strip()}
    text1 = dumps_json5(obj)
    parsed1 = pyjson5.loads(text1)
    text2 = dumps_json5(parsed1)
    assert text1 == text2


def test_sentence_breaks_preserve_escapes_whitespace_and_paragraphs():
    obj = {
        "A key. With sentences.": (
            'First sentence.  "Quoted sentence!" Next one? Yes.\n\n'
            'A path C:\\notes and literal \\n. Final sentence.'
        ),
        "items": ["One sentence. Another sentence.", "Version 1.2 stays intact."],
    }
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert '"A key. With sentences.":' in text
    assert 'First sentence.  \\\n' in text
    assert 'sentence!\\" \\\nNext one? \\\nYes.' in text
    assert 'One sentence. \\\nAnother sentence.' in text
    assert "Version 1.2 stays intact." in text
    assert "\\n\\\n\\n\\\n" in text
    assert dumps_json5(parsed) == text


def test_default_keeps_long_sentence_on_one_line():
    sentence = "A sentence with " + "many words " * 40 + "ends here."
    text, parsed = _roundtrip({"description": sentence})
    assert parsed == {"description": sentence}
    assert sentence in text


@pytest.mark.parametrize(
    "sentence",
    [
        "Use the U.S. Army mirror.",
        "Ask Dr. Smith for help.",
        "Ask J. R. Smith for help.",
        "Try another platform, e.g. Linux.",
        "See Fig. A for details.",
        "See the Dept. Chair today.",
        "Ask Capt. Smith for help.",
        "Read the Acme Corp. Report today.",
    ],
)
def test_abbreviations_and_initials_stay_with_their_sentence(sentence):
    obj = {"description": sentence + " Another sentence."}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert sentence + " \\\nAnother sentence." in text
    assert dumps_json5(parsed) == text


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("next_sentence", ["Écrire ensuite.", "Überprüfen.", "Проверить."])
def test_unicode_sentence_starts(next_sentence, ensure_ascii):
    obj = {"description": "First sentence. " + next_sentence}
    text, parsed = _roundtrip(obj, ensure_ascii=ensure_ascii)
    assert parsed == obj
    assert "First sentence. \\\n" in text
    assert dumps_json5(parsed, ensure_ascii=ensure_ascii) == text


@pytest.mark.parametrize(
    "marked_sentence",
    [
        "**Next sentence.**",
        "*Next sentence.*",
        "_Next sentence._",
        "__Next sentence.__",
        "~~Next sentence.~~",
        "`Next sentence.`",
        "[Next sentence](https://example.com).",
        "![Next sentence](image.png).",
    ],
)
def test_markdown_sentence_boundaries(marked_sentence):
    obj = {"description": "First sentence. " + marked_sentence + " Last sentence."}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "First sentence. \\\n" + marked_sentence + " \\\nLast sentence." in text
    assert dumps_json5(parsed) == text


@pytest.mark.parametrize(
    "code",
    [
        "`First sentence. Second sentence.`",
        "``First ` sentence. Second sentence.``",
        r"\```First sentence. Second sentence.``",
        "```text\nFirst sentence. Second sentence.\n```",
        "~~~~text\nFirst sentence. Second sentence.\n~~~~",
        "```text\nFirst sentence. Second sentence.",
        "    First sentence. Second sentence.\n",
        "- ```text\n  First sentence. Second sentence.\n  ```",
        "> ```text\n> First sentence. Second sentence.\n> ```",
        "| First sentence. Second sentence. | value |",
        "Header | Other\n--- | ---\nFirst sentence. Second sentence. | value",
        ">     First sentence. Second sentence.",
        "> First sentence. Second sentence. | Other\n> --- | ---\n> Value | Other",
        "```text\n    ```\nFirst sentence. Second sentence.\n```",
        "```text\n- ```\nFirst sentence. Second sentence.\n```",
        "```text\n> ```\nFirst sentence. Second sentence.\n```",
        "> ```text\n> - ```\n> First sentence. Second sentence.\n> ```",
    ],
)
def test_code_source_lines_preserved(code):
    obj = {"description": "Intro sentence. More prose.\n\n" + code}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "Intro sentence. \\\nMore prose." in text
    assert "sentence. Second sentence." in text
    assert dumps_json5(parsed) == text


def test_escaped_backticks_do_not_suppress_sentence_breaks():
    obj = {"description": r"Literal \` marker. Next sentence. Another \` marker."}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "marker. \\\nNext sentence. \\\nAnother" in text
    assert dumps_json5(parsed) == text


@pytest.mark.parametrize(
    "description",
    [
        "```foo`bar\nFirst sentence. Second sentence.",
        "> ```\n> Code\n\n> First sentence. Second sentence.",
    ],
)
def test_prose_outside_valid_fences_is_split(description):
    obj = {"description": description}
    text, parsed = _roundtrip(obj)
    assert parsed == obj
    assert "First sentence. \\\nSecond sentence." in text
    assert dumps_json5(parsed) == text
