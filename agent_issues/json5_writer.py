"""JSON5 serialization helpers with multi-line string support."""

import json
import re


def dumps_json5(
    obj: object,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> str:
    """Serialize to JSON5 with multi-line strings and trailing commas.

    Strings containing newlines are split at \\n boundaries using JSON5 line continuations so each logical line appears on its own file line.
    String values break at sentence boundaries without a width limit.
    """
    text = json.dumps(
        obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii
    )
    text = _add_trailing_commas(text)
    text = _split_sentence_strings(text, ensure_ascii=ensure_ascii)
    text = _expand_multiline_strings(text)
    return text


def _split_sentence_strings(text: str, *, ensure_ascii: bool) -> str:
    """Insert continuations after sentence punctuation without changing values.

    Sentence detection is deliberately conservative: punctuation followed by spaces and a capital letter, optionally surrounded by closing/opening quotes.
    Existing newlines remain authoritative boundaries.
    """
    boundary = re.compile(r'''[.!?]["'”’)]* +(?=["'“‘(]*[A-Z])''')

    def split_value(match: re.Match) -> str:
        if text[match.end():].lstrip().startswith(":"):
            return match.group()  # Never split object keys.
        value = json.loads(match.group())
        chunks = []
        start = 0
        for sentence in boundary.finditer(value):
            end = sentence.end()
            chunks.append(json.dumps(value[start:end], ensure_ascii=ensure_ascii)[1:-1])
            start = end
        chunks.append(json.dumps(value[start:], ensure_ascii=ensure_ascii)[1:-1])
        return '"' + "\\\n".join(chunks) + '"'

    return re.sub(r'"(?:[^"\\]|\\.)*"', split_value, text)


def _add_trailing_commas(text: str) -> str:
    """Add trailing comma after the last element before } or ]."""
    return re.sub(r"([^\s,\[\{])\n(\s*[\]\}])", r"\1,\n\2", text)


def _expand_multiline_strings(text: str) -> str:
    r"""Expand \n escapes inside JSON strings into line continuations.

    Walks the text tracking string context so only \n inside strings (not \\n
    which is a literal backslash + n) gets expanded.
    """
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                if i + 1 < len(text):
                    next_ch = text[i + 1]
                    if next_ch == "n":
                        # \n escape -> \n + line continuation
                        result.append("\\n\\\n")
                        i += 2
                        continue
                    result.append(ch)
                    result.append(next_ch)
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        result.append(ch)
        i += 1
    return "".join(result)
