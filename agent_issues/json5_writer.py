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
    boundary = re.compile(
        r'''[.!?]["'”’)\]*_`~]* +(?=["'“‘(\[*_`~]*(?P<next>[^\W\d_]))'''
    )
    key_suffix = re.compile(r"\s*:")
    abbreviation = re.compile(
        r"\b(?:[^\W\d_]|mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|fig|no|vol|inc|ltd|"
        r"dept|univ|assn|corp|co|approx|est|ref|refs|eq|eqs|figs|vols|pp|"
        r"rev|hon|gov|sen|rep|gen|col|maj|capt|lt|sgt|supt|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.$",
        re.IGNORECASE,
    )

    def split_value(match: re.Match) -> str:
        if key_suffix.match(text, match.end()):
            return match.group()  # Never split object keys.
        value = json.loads(match.group())
        code_ranges = iter(_markdown_code_ranges(value))
        code_range = next(code_ranges, None)
        chunks = []
        start = 0
        for sentence in boundary.finditer(value):
            while code_range is not None and code_range[1] <= sentence.start():
                code_range = next(code_ranges, None)
            if (
                code_range is not None
                and code_range[0] <= sentence.start()
                and sentence.end() <= code_range[1]
            ):
                continue
            if not sentence.group("next").isupper():
                continue
            # A single initial also covers the final letter of U.S. or e.g.
            punctuation_end = sentence.start() + 1
            if abbreviation.search(
                value, max(0, punctuation_end - 16), punctuation_end
            ):
                continue
            end = sentence.end()
            chunks.append(json.dumps(value[start:end], ensure_ascii=ensure_ascii)[1:-1])
            start = end
        chunks.append(json.dumps(value[start:], ensure_ascii=ensure_ascii)[1:-1])
        return '"' + "\\\n".join(chunks) + '"'

    return re.sub(r'"(?:[^"\\]|\\.)*"', split_value, text)


def _markdown_code_ranges(value: str) -> list[tuple[int, int]]:
    """Locate fenced, indented, and inline code whose source lines must survive."""
    blocks: list[tuple[int, int]] = []
    fence = None
    fence_start = 0
    offset = 0
    for line in value.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is not None:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                blocks.append((fence_start, offset + len(line)))
                fence = None
        elif marker:
            fence = marker[1]
            fence_start = offset
        elif line.startswith(("    ", "\t")):
            blocks.append((offset, offset + len(line)))
        offset += len(line)
    if fence is not None:
        blocks.append((fence_start, len(value)))

    # Match equal-length backtick runs only outside the block ranges.
    inline = re.compile(r"(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)")
    ranges: list[tuple[int, int]] = []
    offset = 0
    for start, end in blocks:
        ranges.extend(match.span() for match in inline.finditer(value, offset, start))
        ranges.append((start, end))
        offset = end
    ranges.extend(match.span() for match in inline.finditer(value, offset))
    return ranges


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
