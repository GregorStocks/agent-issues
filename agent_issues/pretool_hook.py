"""Shared PreToolUse hook policy for ordinary shell commands.

This hook is a codebase-convention guardrail, not a sandbox.  It recognizes the
common command shapes agents use by accident and returns actionable guidance for
the idiomatic workflow.  It intentionally does not try to prove that arbitrary
shell can never bypass policy.
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import shlex
import subprocess
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agent_issues.json5_utils import loads_json5

DEFAULT_CONFIG_PATH = ".agent-issues/pretool-hook.json5"
DEFAULT_AGENT_SUBMIT_TIMEOUT_MS = 70 * 60 * 1000


@dataclass(frozen=True)
class BinaryBlock:
    """A repo-specific executable block."""

    pattern: str
    message: str


@dataclass(frozen=True)
class CommandFamilyBlock:
    """A command-family block, optionally with subcommand-specific guidance."""

    command: str
    message: str
    subcommands: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HookConfig:
    """Declarative repo configuration for shared hook rules."""

    branch_switch_signoff_env: str = "AGENT_BRANCH_SWITCH_SIGNOFF"
    generated_paths: tuple[str, ...] = ()
    generated_command: str = "the generator target"
    command_family_blocks: tuple[CommandFamilyBlock, ...] = ()
    binary_blocks: tuple[BinaryBlock, ...] = ()
    internal_make_targets: dict[str, str] = field(default_factory=dict)
    make_targets_requiring_timeout_ms: dict[str, int] = field(default_factory=dict)
    minimum_agent_submit_timeout_ms: int = DEFAULT_AGENT_SUBMIT_TIMEOUT_MS
    github_issue_guidance: str = "local JSON5 issue files in issues/"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HookConfig":
        return cls(
            branch_switch_signoff_env=str(
                data.get("branch_switch_signoff_env", cls.branch_switch_signoff_env)
            ),
            generated_paths=tuple(str(path) for path in data.get("generated_paths", ())),
            generated_command=str(data.get("generated_command", cls.generated_command)),
            command_family_blocks=tuple(
                CommandFamilyBlock(
                    command=str(item["command"]),
                    message=str(item["message"]),
                    subcommands={
                        str(k): str(v) for k, v in item.get("subcommands", {}).items()
                    },
                )
                for item in data.get("command_family_blocks", ())
            ),
            binary_blocks=tuple(
                BinaryBlock(pattern=str(item["pattern"]), message=str(item["message"]))
                for item in data.get("binary_blocks", ())
            ),
            internal_make_targets={
                str(k): str(v) for k, v in data.get("internal_make_targets", {}).items()
            },
            make_targets_requiring_timeout_ms={
                str(k): int(v)
                for k, v in data.get("make_targets_requiring_timeout_ms", {}).items()
            },
            minimum_agent_submit_timeout_ms=int(
                data.get("minimum_agent_submit_timeout_ms", DEFAULT_AGENT_SUBMIT_TIMEOUT_MS)
            ),
            github_issue_guidance=str(
                data.get("github_issue_guidance", cls.github_issue_guidance)
            ),
        )


@dataclass(frozen=True)
class Invocation:
    env: dict[str, str]
    executable: str
    args: tuple[str, ...]
    redirection_targets: tuple[str, ...] = ()
    cwd: str = "."

    @property
    def basename(self) -> str:
        return os.path.basename(self.executable)


def load_config(path: Path | str | None = None) -> HookConfig:
    """Load hook config from JSON5. Missing config means generic rules only."""

    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return HookConfig()
    data = loads_json5(config_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: expected an object")
    return HookConfig.from_dict(data)


def _strip_comments(command: str) -> str:
    """Drop shell comments while preserving word-internal # characters."""

    output: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command) and not in_single:
            output.append(command[index : index + 2])
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        previous = output[-1][-1] if output else ""
        starts_comment = not previous or previous.isspace() or previous in ";|&("
        if char == "#" and not in_single and not in_double and starts_comment:
            while index < len(command) and command[index] != "\n":
                index += 1
            continue
        output.append(";" if char == "\n" and not in_single and not in_double else char)
        index += 1
    return "".join(output)


def _shell_tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(_strip_comments(command), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _shell_segments(command: str) -> list[tuple[list[str], str]]:
    tokens = _shell_tokens(command)
    if not tokens:
        return []
    segments: list[tuple[list[str], str]] = []
    separators = {";", "&&", "||", "|", "&"}
    start = 0
    for index, token in enumerate([*tokens, ";"]):
        if token in separators:
            segment = tokens[start:index]
            if segment:
                segments.append((segment, token))
            start = index + 1
    return segments


def _is_env_assignment(token: str) -> bool:
    key, sep, _value = token.partition("=")
    return bool(sep and key and key.replace("_", "A").isalnum() and not key[0].isdigit())


def _skip_option(args: list[str], index: int, opts_with_arg: set[str]) -> int:
    token = args[index]
    if token == "--":
        return index + 1
    option_name = token.split("=", 1)[0]
    if token.startswith("-") and token != "-":
        index += 1
        if "=" not in token and option_name in opts_with_arg and index < len(args):
            index += 1
    return index


def _skip_redirection(tokens: list[str], index: int) -> int:
    token = tokens[index]
    if token in {">", ">>", ">|", "<", "2>", "2>>", "1>", "1>>", "&>", ">&"}:
        return min(index + 2, len(tokens))
    if re.fullmatch(r"\d*(?:>>?|<)&?", token):
        return min(index + 2, len(tokens))
    return index


def _redirection_targets(tokens: list[str]) -> tuple[str, ...]:
    targets: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_index = _skip_redirection(tokens, index)
        if next_index != index:
            if token != "<" and next_index - 1 < len(tokens):
                targets.append(tokens[next_index - 1])
            index = next_index
            continue
        index += 1
    return tuple(targets)


def _unwrap_invocation(tokens: list[str]) -> Invocation | None:
    env: dict[str, str] = {}
    cwd = "."
    redirection_targets = _redirection_targets(tokens)
    index = 0

    while index < len(tokens):
        next_index = _skip_redirection(tokens, index)
        if next_index != index:
            index = next_index
            continue

        token = tokens[index]
        if _is_env_assignment(token):
            key, value = token.split("=", 1)
            env[key] = value
            index += 1
            continue

        name = os.path.basename(token)
        if name in {"time", "nohup", "command", "builtin"}:
            index += 1
            continue
        if name == "sudo":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if token in {"-D", "--chdir"} and index + 1 < len(tokens):
                    cwd = _clean_path(tokens[index + 1], cwd=cwd)
                    index += 2
                    continue
                if token.startswith("--chdir="):
                    cwd = _clean_path(token.split("=", 1)[1], cwd=cwd)
                    index += 1
                    continue
                if token.startswith("-D") and len(token) > 2:
                    cwd = _clean_path(token[2:], cwd=cwd)
                    index += 1
                    continue
                if token.startswith("-"):
                    index = _skip_option(
                        tokens,
                        index,
                        {
                            "-C",
                            "-R",
                            "-T",
                            "-g",
                            "-h",
                            "-p",
                            "-r",
                            "-t",
                            "-u",
                            "--chroot",
                            "--command-timeout",
                            "--group",
                            "--host",
                            "--prompt",
                            "--role",
                            "--type",
                            "--user",
                        },
                    )
                    continue
                break
            continue
        if name == "nice":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index = _skip_option(tokens, index, {"-n", "--adjustment"})
            continue
        if name == "timeout":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index = _skip_option(tokens, index, {"-s", "--signal", "-k", "--kill-after"})
            if index < len(tokens):
                index += 1
            continue
        if name == "env":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if _is_env_assignment(token):
                    key, value = token.split("=", 1)
                    env[key] = value
                    index += 1
                    continue
                if token in {"-C", "--chdir"} and index + 1 < len(tokens):
                    cwd = _clean_path(tokens[index + 1], cwd=cwd)
                    index += 2
                    continue
                if token.startswith("--chdir="):
                    cwd = _clean_path(token.split("=", 1)[1], cwd=cwd)
                    index += 1
                    continue
                if token.startswith("-S") and len(token) > 2:
                    return Invocation(env, "sh", ("-c", token[2:]), cwd=cwd)
                if token in {"-S", "--split-string"} and index + 1 < len(tokens):
                    return Invocation(env, "sh", ("-c", tokens[index + 1]), cwd=cwd)
                if token.startswith("--split-string="):
                    return Invocation(
                        env, "sh", ("-c", token.split("=", 1)[1]), cwd=cwd
                    )
                if token.startswith("-"):
                    index += 1
                    continue
                break
            continue
        break

    if index >= len(tokens):
        return None
    return Invocation(env, tokens[index], tuple(tokens[index + 1 :]), redirection_targets, cwd)


def _shell_c_payload(invocation: Invocation) -> str | None:
    if invocation.basename not in {"sh", "bash", "zsh", "dash"}:
        return None
    args = list(invocation.args)
    for index, arg in enumerate(args):
        if arg in {"-c", "--command"} and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--command="):
            return arg.split("=", 1)[1]
        if arg.startswith("-") and "c" in arg[1:]:
            suffix = arg[1:].split("c", 1)[1]
            return suffix or (args[index + 1] if index + 1 < len(args) else None)
    return None


def command_invocations(command: str, *, initial_cwd: str = ".") -> list[Invocation]:
    """Return ordinary executable invocations from a simple shell command."""

    invocations: list[Invocation] = []
    cwd = initial_cwd
    previous_cwd = initial_cwd
    for tokens, separator in _shell_segments(command):
        invocation = _unwrap_invocation(tokens)
        if invocation is None:
            if targets := _redirection_targets(tokens):
                invocations.append(Invocation({}, "", (), targets, cwd))
            continue

        invocation = replace(invocation, cwd=_clean_path(invocation.cwd, cwd=cwd))
        payload = _shell_c_payload(invocation)
        if payload is not None:
            invocations.extend(command_invocations(payload, initial_cwd=invocation.cwd))
            continue

        invocations.append(invocation)
        if invocation.basename == "cd" and invocation.args and separator in {";", "&&"}:
            target = _cd_target(invocation.args)
            if target is not None:
                old_cwd = cwd
                cwd = previous_cwd if target == "-" else _clean_path(target, cwd=cwd)
                previous_cwd = old_cwd
    return invocations


def _coerce_timeout_ms(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _timeout_field_ms(mapping: dict[str, Any]) -> int | None:
    # Codex names the Bash/Shell timeout parameter "timeout_ms"; Claude Code
    # names it "timeout" (also milliseconds). Accept either so the guardrail
    # works across both harnesses.
    for key in ("timeout_ms", "timeout"):
        if (coerced := _coerce_timeout_ms(mapping.get(key))) is not None:
            return coerced
    return None


def tool_timeout_ms(data: dict[str, Any], command: str | None = None) -> int | None:
    tool_input = data.get("tool_input", {})
    if isinstance(tool_input, dict) and (coerced := _timeout_field_ms(tool_input)) is not None:
        return coerced

    transcript_path = data.get("transcript_path")
    tool_use_id = data.get("tool_use_id")
    if not isinstance(transcript_path, str) or not isinstance(tool_use_id, str):
        return None
    try:
        with Path(transcript_path).open(encoding="utf-8") as handle:
            return _timeout_ms_from_transcript_lines(
                deque(handle, maxlen=300),
                tool_use_id,
                command=command,
            )
    except OSError:
        return None


def _timeout_ms_from_transcript_lines(
    lines: deque[str],
    tool_use_id: str,
    *,
    command: str | None = None,
) -> int | None:
    parsed_events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload", event)
        if isinstance(payload, dict):
            parsed_events.append(payload)

    for payload in reversed(parsed_events):
        if payload.get("call_id") != tool_use_id:
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, str):
            continue
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return _timeout_field_ms(parsed)

    if command is None:
        return None
    return _code_mode_shell_timeout_ms(parsed_events, command)


def _code_mode_shell_timeout_ms(
    payloads: list[dict[str, Any]], command: str
) -> int | None:
    completed_call_ids = {
        payload.get("call_id")
        for payload in payloads
        if payload.get("type") == "custom_tool_call_output"
        and isinstance(payload.get("call_id"), str)
    }

    for payload in reversed(payloads):
        if (
            payload.get("type") != "custom_tool_call"
            or payload.get("name") != "exec"
            or payload.get("call_id") in completed_call_ids
        ):
            continue
        source = payload.get("input")
        if not isinstance(source, str):
            continue
        matching_calls = [
            call for call in _code_mode_shell_calls(source) if call.get("command") == command
        ]
        if len(matching_calls) != 1:
            return None
        return _timeout_field_ms(matching_calls[0])
    return None


def _code_mode_shell_calls(source: str) -> list[dict[str, Any]]:
    marker = "tools.shell_command("
    calls: list[dict[str, Any]] = []
    search_from = 0

    while (marker_at := source.find(marker, search_from)) != -1:
        argument_at = marker_at + len(marker)
        while argument_at < len(source) and source[argument_at].isspace():
            argument_at += 1
        decoded = _decode_code_mode_object(source, argument_at)
        if decoded is None:
            search_from = argument_at
            continue
        parsed, after_argument = decoded
        while after_argument < len(source) and source[after_argument].isspace():
            after_argument += 1
        if after_argument < len(source) and source[after_argument] == ")":
            calls.append(parsed)
        search_from = max(after_argument, argument_at + 1)

    return calls


def _decode_code_mode_object(
    source: str, object_at: int
) -> tuple[dict[str, Any], int] | None:
    if object_at >= len(source) or source[object_at] != "{":
        return None

    closing: list[str] = []
    in_string = False
    escaped = False
    object_end: int | None = None
    for index in range(object_at, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            closing.append("}" if character == "{" else "]")
        elif character in "}]":
            if not closing or closing.pop() != character:
                return None
            if not closing:
                object_end = index + 1
                break

    if object_end is None:
        return None
    literal = _quote_js_identifier_keys(source[object_at:object_end])
    try:
        parsed = json.loads(literal)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed, object_end


def _quote_js_identifier_keys(literal: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(literal):
        character = literal[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        if character.isalpha() or character in "_$":
            token_end = index + 1
            while token_end < len(literal) and (
                literal[token_end].isalnum() or literal[token_end] in "_$"
            ):
                token_end += 1
            colon_at = token_end
            while colon_at < len(literal) and literal[colon_at].isspace():
                colon_at += 1
            previous = next((char for char in reversed(result) if not char.isspace()), None)
            token = literal[index:token_end]
            is_key = previous in {"{", ","} and literal[colon_at : colon_at + 1] == ":"
            result.append(f'"{token}"' if is_key else token)
            index = token_end
            continue
        result.append(character)
        index += 1
    return "".join(result)


_GIT_GLOBAL_OPTS_WITH_ARG = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--work-tree",
}
_GIT_GLOBAL_OPTS_NO_ARG = {
    "-p",
    "-P",
    "--bare",
    "--no-pager",
    "--paginate",
}


def _git_global_option(args: list[str], index: int) -> tuple[str, str | None, int] | None:
    token = args[index]
    if token in _GIT_GLOBAL_OPTS_WITH_ARG and index + 1 < len(args):
        return token, args[index + 1], index + 2
    for option in _GIT_GLOBAL_OPTS_WITH_ARG:
        if token.startswith(f"{option}="):
            return option, token.split("=", 1)[1], index + 1
    if token.startswith("-c") and token != "-c":
        return "-c", token[2:], index + 1
    if token in _GIT_GLOBAL_OPTS_NO_ARG:
        return token, None, index + 1
    return None


def _git_cwd(invocation: Invocation) -> str:
    if invocation.basename != "git":
        return invocation.cwd
    cwd = invocation.cwd
    args = list(invocation.args)
    index = 0
    while index < len(args):
        info = _git_global_option(args, index)
        if info is None:
            break
        option, value, index = info
        if option == "-C" and value is not None:
            cwd = _clean_path(value, cwd=cwd)
    return cwd


def _git_subcommand(invocation: Invocation) -> tuple[str, list[str]] | None:
    if invocation.basename.startswith("git-") and invocation.basename != "git":
        return invocation.basename.removeprefix("git-"), list(invocation.args)
    if invocation.basename != "git":
        return None
    args = list(invocation.args)
    index = 0
    while index < len(args):
        info = _git_global_option(args, index)
        if info is None:
            break
        _option, _value, index = info
    if index >= len(args):
        return None
    return args[index], args[index + 1 :]


def _git_inline_alias_payload(invocation: Invocation) -> str | None:
    if invocation.basename != "git":
        return None
    args = list(invocation.args)
    aliases: dict[str, str] = {}
    index = 0
    while index < len(args):
        info = _git_global_option(args, index)
        if info is None:
            break
        option, value, index = info
        if option != "-c" or not value:
            continue
        key, sep, alias = value.partition("=")
        if sep and key.lower().startswith("alias."):
            aliases[key.split(".", 1)[1]] = alias
    if index >= len(args):
        return None
    alias = aliases.get(args[index])
    if alias is None:
        return None
    rest = " ".join(shlex.quote(arg) for arg in args[index + 1 :])
    if alias.startswith("!"):
        return f"{alias[1:]} {rest}".strip()
    return f"git {alias} {rest}".strip()


def _git_push_args(invocation: Invocation) -> list[str] | None:
    if invocation.basename in {"git-push", "git-send-pack"}:
        return list(invocation.args)
    subcommand = _git_subcommand(invocation)
    if subcommand is None:
        return None
    name, rest = subcommand
    return rest if name in {"push", "send-pack"} else None


def _push_uses_force(args: list[str]) -> bool:
    return any(
        arg in {"--force", "--force-with-lease", "--force-if-includes"}
        or arg.startswith("--force-with-lease=")
        or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:])
        for arg in args
    )


_SWITCH_OPTS_WITH_ARG = {"-c", "-C", "--create", "--force-create", "--orphan"}
_CHECKOUT_OPTS_WITH_ARG = {"-b", "-B", "--orphan", "--pathspec-from-file"}


def _branch_target(args: list[str], opts_with_arg: set[str], *, checkout: bool) -> str | None:
    if checkout and "--" in args and args.index("--") < len(args) - 1:
        return None
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return None
        if token in {"-d", "--detach"}:
            return args[index + 1] if index + 1 < len(args) else "HEAD"
        if token in opts_with_arg:
            return args[index + 1] if index + 1 < len(args) else ""
        if any(token.startswith(f"{opt}=") for opt in opts_with_arg if opt.startswith("--")):
            return token.split("=", 1)[1]
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def _branch_switch_target(invocation: Invocation) -> str | None:
    subcommand = _git_subcommand(invocation)
    if subcommand is None:
        return None
    name, rest = subcommand
    if name == "switch":
        return _branch_target(rest, _SWITCH_OPTS_WITH_ARG, checkout=False)
    if name == "checkout":
        return _branch_target(rest, _CHECKOUT_OPTS_WITH_ARG, checkout=True)
    return None


def _gh_chain(invocation: Invocation) -> list[str]:
    if invocation.basename != "gh":
        return []
    chain: list[str] = []
    args = list(invocation.args)
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-R", "--repo", "--hostname"}:
            index += 2
            continue
        if token.startswith("--repo=") or token.startswith("--hostname="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        chain.append(token)
        index += 1
    return chain


_MAKE_OPTS_WITH_ARG = {
    "-f",
    "--file",
    "--makefile",
    "-C",
    "--directory",
    "-I",
    "--include-dir",
    "-W",
    "--what-if",
    "--new-file",
    "--assume-new",
    "-o",
    "--old-file",
    "--assume-old",
    "--eval",
}


def _make_targets(invocation: Invocation) -> list[str]:
    if invocation.basename != "make":
        return []
    targets: list[str] = []
    args = list(invocation.args)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            targets.extend(arg for arg in args[index + 1 :] if "=" not in arg)
            break
        if token in _MAKE_OPTS_WITH_ARG:
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        if "=" not in token:
            targets.append(token)
        index += 1
    return targets


def _cd_target(args: tuple[str, ...]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return args[index + 1] if index + 1 < len(args) else None
        if arg in {"-L", "-P"}:
            index += 1
            continue
        return arg
    return None


def _clean_path(path: str, *, cwd: str = ".") -> str:
    path = path.removeprefix(":/").removeprefix("./")
    if path.startswith(":(top)"):
        path = path.removeprefix(":(top)")
    path_obj = Path(path)
    if path_obj.is_absolute():
        try:
            path = str(path_obj.resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            path = str(path_obj)
    elif cwd not in {"", "."}:
        path = posixpath.join(cwd, path)
    return posixpath.normpath(path).rstrip("/")


def _path_matches(
    path: str,
    protected: str,
    *,
    include_ancestors: bool = False,
    cwd: str = ".",
) -> bool:
    path = _clean_path(path, cwd=cwd)
    protected = _clean_path(protected)
    if any(char in path for char in "*?["):
        glob_prefix = re.split(r"[*?[]", path, maxsplit=1)[0].rstrip("/")
        return bool(glob_prefix and _path_matches(glob_prefix, protected, include_ancestors=True))
    if path == protected or path.startswith(f"{protected}/"):
        return True
    return include_ancestors and (
        path in {"", "."} or protected.startswith(f"{path}/")
    )


def _arg_targets_generated(
    arg: str,
    paths: tuple[str, ...],
    *,
    include_ancestors: bool = False,
    cwd: str = ".",
) -> bool:
    return any(
        _path_matches(arg, path, include_ancestors=include_ancestors, cwd=cwd)
        for path in paths
    )


def _generated_mutation(invocation: Invocation, config: HookConfig) -> bool:
    if not config.generated_paths:
        return False
    if invocation.basename in {"cp", "install", "ln"}:
        operands = [arg for arg in invocation.args if not arg.startswith("-")]
        if not operands:
            return False
        return _arg_targets_generated(
            operands[-1],
            config.generated_paths,
            include_ancestors=True,
            cwd=invocation.cwd,
        )
    if invocation.basename in {"rm", "mv", "touch", "truncate", "mkdir", "rmdir"}:
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=invocation.cwd,
            )
            for arg in invocation.args
            if not arg.startswith("-")
        )
    if invocation.basename == "tee":
        return any(
            _arg_targets_generated(arg, config.generated_paths, cwd=invocation.cwd)
            for arg in invocation.args
            if not arg.startswith("-")
        )
    if invocation.basename == "sed" and any(
        arg.startswith("-i") or arg == "--in-place" or arg.startswith("--in-place=")
        for arg in invocation.args
    ):
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=invocation.cwd,
            )
            for arg in invocation.args
        )
    if invocation.basename == "perl" and any(
        arg == "-i" or arg.startswith("-i") or (arg.startswith("-") and "i" in arg[1:])
        for arg in invocation.args
    ):
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=invocation.cwd,
            )
            for arg in invocation.args
        )

    subcommand = _git_subcommand(invocation)
    if subcommand is None:
        return False
    name, rest = subcommand
    git_cwd = _git_cwd(invocation)
    if name == "clean":
        pathspecs = [arg for arg in rest if not arg.startswith("-")]
        return not pathspecs or any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=git_cwd,
            )
            for arg in pathspecs
        )
    if name == "reset" and any(arg in {"--hard", "--merge", "--keep"} for arg in rest):
        return True
    if name == "apply":
        return True
    if name in {"rm", "mv", "checkout", "restore"}:
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=git_cwd,
            )
            for arg in rest
        )
    return False


def _has_dirty_generated_output(paths: tuple[str, ...]) -> bool:
    if not paths:
        return False
    result = subprocess.run(
        ["git", "status", "--short", "--", *paths],
        check=False,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _short_timeout_message(command_name: str, timeout_ms: int | None, minimum_ms: int) -> str:
    minimum_minutes = minimum_ms // 60_000
    if timeout_ms is None:
        return (
            f"Do not run `{command_name}` without an explicit tool timeout. "
            f"Use at least {minimum_minutes} minutes so the command can finish "
            "or print its own actionable timeout guidance."
        )
    timeout_minutes = timeout_ms / 60_000
    return (
        f"Do not run `{command_name}` with a tool timeout of only "
        f"{timeout_minutes:.1f} minutes. Use at least {minimum_minutes} minutes "
        "so the command can finish or print its own actionable timeout guidance."
    )


def _raw_pr_message(force: bool = False) -> str:
    if force:
        return (
            "Do not force-push with raw 'git push'. Use "
            "'agent-submit --force --title \"...\" --body \"...\"' so the forced "
            "push, PR metadata updates, and CI watching happen together."
        )
    return (
        "Do not publish PR updates with raw 'git push' or 'gh pr create/edit'. Use "
        "'agent-submit --title \"...\" --body \"...\"' so push, PR metadata updates, "
        "and CI watching happen together."
    )


def _binary_matches(executable: str, basename: str, pattern: str, *, cwd: str = ".") -> bool:
    normalized = _clean_path(executable, cwd=cwd)
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)


def rejection_message(
    command: str,
    config: HookConfig | None = None,
    *,
    timeout_ms: int | None = None,
    dirty_generated_output: bool | None = None,
    cwd: str = ".",
) -> str | None:
    """Return a user-facing rejection message, or ``None`` to allow."""

    config = config or HookConfig()
    invocations = command_invocations(command, initial_cwd=cwd)
    if dirty_generated_output is None and any(_git_push_args(inv) is not None for inv in invocations):
        dirty_generated_output = _has_dirty_generated_output(config.generated_paths)
    dirty_generated_output = bool(dirty_generated_output)

    for invocation in invocations:
        if invocation.basename in {"pkill", "killall"}:
            return (
                "Do not use pkill/killall; other agents may share this machine. "
                "Only kill processes by specific PID after verifying the PID."
            )

        alias_payload = _git_inline_alias_payload(invocation)
        if alias_payload is not None:
            alias_message = rejection_message(
                alias_payload,
                config,
                timeout_ms=timeout_ms,
                dirty_generated_output=dirty_generated_output,
                cwd=_git_cwd(invocation),
            )
            if alias_message is not None:
                return alias_message

        chain = _gh_chain(invocation)
        if chain and chain[0] == "issue":
            return f"Do not use GitHub Issues. This project tracks issues with {config.github_issue_guidance}."
        if len(chain) >= 2 and chain[0] == "pr" and chain[1] in {"create", "edit"}:
            return _raw_pr_message()

        push_args = _git_push_args(invocation)
        if push_args is not None:
            if dirty_generated_output:
                joined_paths = ", ".join(config.generated_paths)
                return (
                    f"Refusing to publish while generated output is modified: {joined_paths}. "
                    f"Keep the full result of `{config.generated_command}` together: stage it, "
                    "commit it, and only then publish."
                )
            return _raw_pr_message(force=_push_uses_force(push_args))

        branch_target = _branch_switch_target(invocation)
        if branch_target is not None:
            signoff = invocation.env.get(config.branch_switch_signoff_env)
            if signoff != branch_target:
                return (
                    "Refusing branch-changing git command without explicit user signoff. "
                    "After the user approves switching to that specific branch, re-run the "
                    f"command with {config.branch_switch_signoff_env}={branch_target} "
                    "immediately before git."
                )

        if config.generated_paths and any(
            _arg_targets_generated(target, config.generated_paths, cwd=invocation.cwd)
            for target in invocation.redirection_targets
        ):
            return (
                "Do not redirect shell output into generated output paths. "
                f"The only supported way to update them is `{config.generated_command}`."
            )

        if _generated_mutation(invocation, config):
            return (
                "Do not manually mutate generated output paths. "
                f"The only supported way to update them is `{config.generated_command}`."
            )

        if invocation.basename == "agent-submit" and (
            timeout_ms is None or timeout_ms < config.minimum_agent_submit_timeout_ms
        ):
            return _short_timeout_message(
                "agent-submit", timeout_ms, config.minimum_agent_submit_timeout_ms
            )

        for target in _make_targets(invocation):
            if target in config.internal_make_targets:
                return (
                    f"Do not run internal make target '{target}' directly. "
                    f"Use `{config.internal_make_targets[target]}`."
                )
            if target in config.make_targets_requiring_timeout_ms:
                minimum_ms = config.make_targets_requiring_timeout_ms[target]
                if timeout_ms is None or timeout_ms < minimum_ms:
                    return _short_timeout_message(f"make {target}", timeout_ms, minimum_ms)

        for block in config.command_family_blocks:
            if invocation.basename != block.command:
                continue
            replacement = None
            for arg in invocation.args:
                if not arg.startswith("-") and arg in block.subcommands:
                    replacement = block.subcommands[arg]
                    break
            if replacement is not None:
                return f"{block.message} Use `{replacement}` instead."
            return block.message

        for block in config.binary_blocks:
            if _binary_matches(
                invocation.executable,
                invocation.basename,
                block.pattern,
                cwd=invocation.cwd,
            ):
                return block.message
    return None


def evaluate_hook_input(data: dict[str, Any], config: HookConfig | None = None) -> str | None:
    """Evaluate a PreToolUse payload. Non-Bash tools are allowed."""

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    tool_name = data.get("tool_name")
    if isinstance(tool_name, str) and tool_name not in {"Bash", "Shell"}:
        return None
    return rejection_message(command, config, timeout_ms=tool_timeout_ms(data, command))
