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
        r'''[.!?]["'”’)\]*_`~]* +(?=["'“‘(\[*_`~!]*(?P<next>[^\W\d_]))'''
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
        code_ranges = iter(_markdown_literal_ranges(value))
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


def _markdown_literal_ranges(value: str) -> list[tuple[int, int]]:
    """Locate code and table rows whose source lines must survive."""
    blocks: list[tuple[int, int]] = []
    fence = None
    fence_container = re.compile("")
    fence_start = 0
    offset = 0
    lines = value.splitlines(keepends=True)
    container = re.compile(r"^\s*(?:> ?|[-+*] +|\d+[.)] +)")
    table_separator = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
    contents = []
    containers = []
    for line in lines:
        content = line.rstrip("\r\n")
        prefix_patterns = []
        while prefix := container.match(content):
            prefix_patterns.append(
                r" {0,3}> ?" if prefix[0].lstrip().startswith(">")
                else " " * len(prefix[0])
            )
            content = content[prefix.end():]
        contents.append(content)
        containers.append(re.compile("^" + "".join(prefix_patterns)))
    in_table = False
    for index, line in enumerate(lines):
        content = contents[index]
        if fence is not None:
            raw_content = line.rstrip("\r\n")
            prefix = fence_container.match(raw_content)
            if prefix:
                content = raw_content[prefix.end():]
            elif not raw_content.strip() and ">" not in fence_container.pattern:
                content = raw_content
            else:
                blocks.append((fence_start, offset))
                fence = None
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        table_row = "|" in content and (
            in_table
            or content.lstrip().startswith("|")
            or (index + 1 < len(contents) and table_separator.match(contents[index + 1]))
        )
        in_table = bool(table_row)
        if fence is not None:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                blocks.append((fence_start, offset + len(line)))
                fence = None
        elif marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = marker[1]
            fence_start = offset
            fence_container = containers[index]
        elif table_row or content.startswith(("    ", "\t")):
            blocks.append((offset, offset + len(line)))
        offset += len(line)
    if fence is not None:
        blocks.append((fence_start, len(value)))

    # Match equal-length backtick runs only outside the block ranges.
    ranges: list[tuple[int, int]] = []
    offset = 0
    for start, end in blocks:
        ranges.extend(_inline_code_ranges(value, offset, start))
        ranges.append((start, end))
        offset = end
    ranges.extend(_inline_code_ranges(value, offset, len(value)))
    return ranges


def _inline_code_ranges(value: str, start: int, end: int) -> list[tuple[int, int]]:
    """Match code delimiters, ignoring backslash-escaped opening backticks."""
    runs = list(re.finditer(r"`+", value[start:end]))
    openings = []
    for run in runs:
        position = start + run.start()
        backslashes = 0
        while position - backslashes > start and value[position - backslashes - 1] == "\\":
            backslashes += 1
        escaped = backslashes % 2
        openings.append((position + escaped, len(run[0]) - escaped))
    next_by_length: dict[int, int] = {}
    closing: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        length = openings[index][1]
        if length in next_by_length:
            closing[index] = next_by_length[length]
        next_by_length[len(runs[index][0])] = index
    ranges = []
    index = 0
    while index < len(runs):
        position = openings[index][0]
        if index not in closing:
            index += 1
            continue
        last = closing[index]
        ranges.append((position, start + runs[last].end()))
        index = last + 1
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
