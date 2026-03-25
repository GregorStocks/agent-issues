"""JSON5 serialization helpers with multi-line string support."""

import json
import re


def dumps_json5(
    obj: object,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    wrap_width: int = 80,
) -> str:
    """Serialize to JSON5 with multi-line strings and trailing commas.

    Strings containing newlines are split at \\n boundaries using JSON5 line
    continuations so each logical line appears on its own file line.  Long
    string values are word-wrapped using line continuations to stay within
    *wrap_width* columns (set to 0 to disable wrapping).
    """
    text = json.dumps(
        obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii
    )
    text = _add_trailing_commas(text)
    text = _expand_multiline_strings(text)
    if wrap_width:
        text = _wrap_long_lines(text, wrap_width)
    return text


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


# ---------------------------------------------------------------------------
# Word-wrapping long string values
# ---------------------------------------------------------------------------

# Key-value pair whose value is a complete single-line string:
#   <indent>"<key>": "<value>"[,]
_KV_STRING_RE = re.compile(
    r'^(\s*"(?:[^"\\]|\\.)*":\s*")'  # prefix – indent + key + ': "'
    r"((?:[^\"\\]|\\.)*)"  # content – string body
    r'("(?:,?)\s*)$',  # suffix – closing quote [+ comma]
)

# Continuation line ending with \n\ (multiline-expanded paragraph boundary).
_CONT_NL_RE = re.compile(
    r"^()"  # empty prefix
    r"(.*)"  # content (greedy)
    r"(\\n\\)$",  # suffix – \n\
)

# Last continuation line closing the string: content followed by "[,].
_CONT_END_RE = re.compile(
    r"^()"  # empty prefix
    r'((?:[^"\\]|\\.)*)'  # content (no unescaped quotes)
    r'("(?:,?)\s*)$',  # suffix – "[,]
)


def _wrap_long_lines(text: str, width: int) -> str:
    """Wrap long string-value lines using JSON5 line continuations."""
    lines = text.split("\n")
    result: list[str] = []
    in_string = False

    for line in lines:
        if len(line) <= width:
            result.append(line)
        elif in_string:
            result.extend(_wrap_continuation(line, width))
        else:
            result.extend(_wrap_kv_string(line, width))

        # A line ending with \ means the string continues on the next line.
        last = result[-1] if result else ""
        in_string = last.endswith("\\")

    return "\n".join(result)


def _wrap_kv_string(line: str, width: int) -> list[str]:
    """Wrap a key-value line whose value is a long string."""
    m = _KV_STRING_RE.match(line)
    if not m:
        return [line]
    return _wrap_matched(m, width)


def _wrap_continuation(line: str, width: int) -> list[str]:
    """Wrap a continuation line inside a multiline string."""
    m = _CONT_NL_RE.match(line)
    if not m:
        m = _CONT_END_RE.match(line)
    if not m:
        return [line]
    return _wrap_matched(m, width)


def _wrap_matched(m: re.Match, width: int) -> list[str]:
    """Given a regex match with (prefix, content, suffix), word-wrap content."""
    prefix = m.group(1)
    content = m.group(2)
    suffix = m.group(3)

    chunks = _split_at_spaces(content, width, len(prefix), len(suffix))
    if len(chunks) <= 1:
        return [prefix + content + suffix]

    out = [prefix + chunks[0] + "\\"]
    for chunk in chunks[1:-1]:
        out.append(chunk + "\\")
    out.append(chunks[-1] + suffix)
    return out


def _split_at_spaces(
    text: str, width: int, prefix_len: int, suffix_len: int
) -> list[str]:
    """Split *text* at word boundaries so every output line fits in *width*.

    The first chunk shares a line with *prefix_len* leading characters.
    The last chunk shares a line with *suffix_len* trailing characters.
    Every non-last line carries a trailing ``\\`` (1 char) for the JSON5 line
    continuation.
    """
    # Budget for the first chunk: prefix + chunk + '\'
    first_avail = max(width - prefix_len - 1, 10)
    # Subsequent chunks start at column 0: chunk + '\'
    cont_avail = max(width - 1, 10)

    chunks: list[str] = []
    remaining = text
    avail = first_avail

    while remaining:
        # Would the remaining text fit as the final chunk?
        last_len = len(remaining) + suffix_len
        if not chunks:
            last_len += prefix_len
        if last_len <= width:
            chunks.append(remaining)
            break

        # Find the rightmost space within budget.
        pos = remaining.rfind(" ", 0, avail)
        if pos <= 0:
            # No space in budget – take the next space after budget.
            pos = remaining.find(" ", avail)
            if pos <= 0:
                # No space at all – cannot wrap further.
                chunks.append(remaining)
                break

        chunks.append(remaining[: pos + 1])  # include trailing space
        remaining = remaining[pos + 1 :]
        avail = cont_avail

    return chunks
