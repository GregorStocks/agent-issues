"""Shared PreToolUse hook policy for shell commands.

The public entry point is :func:`evaluate_hook_input`.  It accepts the JSON
payload provided by an agent hook, applies generic safety rules plus a small
declarative repo config, and returns a rejection message when the command should
be blocked.
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
        generated_paths = tuple(str(path) for path in data.get("generated_paths", ()))
        command_family_blocks = tuple(
            CommandFamilyBlock(
                command=str(item["command"]),
                message=str(item["message"]),
                subcommands={str(k): str(v) for k, v in item.get("subcommands", {}).items()},
            )
            for item in data.get("command_family_blocks", ())
        )
        binary_blocks = tuple(
            BinaryBlock(pattern=str(item["pattern"]), message=str(item["message"]))
            for item in data.get("binary_blocks", ())
        )
        return cls(
            branch_switch_signoff_env=str(
                data.get("branch_switch_signoff_env", cls.branch_switch_signoff_env)
            ),
            generated_paths=generated_paths,
            generated_command=str(data.get("generated_command", cls.generated_command)),
            command_family_blocks=command_family_blocks,
            binary_blocks=binary_blocks,
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


def _shell_tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _shell_segments(command: str) -> list[list[str]]:
    tokens = _shell_tokens(command)
    if not tokens:
        return []

    segments: list[list[str]] = []
    separators = {"&&", "||", ";", "|", "&"}
    start = 0
    for index, token in enumerate([*tokens, ";"]):
        if token in separators:
            segment = tokens[start:index]
            if segment:
                segments.append(segment)
            start = index + 1
    return segments


_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_-]*)\1")


def _heredoc_specs(command_line: str) -> list[tuple[str, bool]]:
    return [
        (match.group(2), bool(match.group(1)))
        for match in _HEREDOC_RE.finditer(command_line)
    ]


def _strip_heredocs(command: str) -> tuple[str, list[str]]:
    """Remove literal here-doc bodies and return bodies passed to shells."""

    lines = command.splitlines(keepends=True)
    output: list[str] = []
    shell_bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        tokens = _shell_tokens(line) or []
        specs = _heredoc_specs(line)
        if not specs:
            output.append(line)
            index += 1
            continue

        heredoc_index = min(
            tokens.index("<<") if "<<" in tokens else len(tokens),
            tokens.index("<<-") if "<<-" in tokens else len(tokens),
        )
        invocation = _unwrap_invocation(tokens[:heredoc_index])
        is_shell = invocation is not None and invocation.basename in {"sh", "bash", "zsh", "dash"}
        output.append(line)
        index += 1

        for delimiter, quoted in specs:
            body: list[str] = []
            while index < len(lines):
                body_line = lines[index]
                if body_line.strip() == delimiter:
                    index += 1
                    break
                body.append(body_line)
                index += 1
            if is_shell:
                shell_bodies.append("".join(body))
            elif not quoted:
                body_text = "".join(body)
                expansion_text = body_text.translate({ord("'"): None, ord('"'): None})
                shell_bodies.extend(_extract_subshells(expansion_text))
                shell_bodies.extend(_extract_backticks(expansion_text))
    return "".join(output), shell_bodies


def _extract_subshells(command: str) -> list[str]:
    bodies: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command) and not in_single:
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if in_single:
            index += 1
            continue

        starts_command_substitution = (
            char == "$"
            and index + 1 < len(command)
            and command[index + 1] == "("
            and not (index + 2 < len(command) and command[index + 2] == "(")
        )
        starts_process_substitution = (
            char in {"<", ">"}
            and index + 1 < len(command)
            and command[index + 1] == "("
        )
        if not starts_command_substitution and not starts_process_substitution:
            index += 1
            continue

        body_start = index + 2
        depth = 1
        cursor = body_start
        inner_single = False
        inner_double = False
        while cursor < len(command):
            inner = command[cursor]
            if inner == "\\" and cursor + 1 < len(command) and not inner_single:
                cursor += 2
                continue
            if inner == "'" and not inner_double:
                inner_single = not inner_single
                cursor += 1
                continue
            if inner == '"' and not inner_single:
                inner_double = not inner_double
                cursor += 1
                continue
            if inner_single or inner_double:
                cursor += 1
                continue
            if inner == "(":
                depth += 1
            elif inner == ")":
                depth -= 1
                if depth == 0:
                    bodies.append(command[body_start:cursor])
                    index = cursor + 1
                    break
            cursor += 1
        else:
            index += 1
    return bodies


def _extract_backticks(command: str) -> list[str]:
    bodies: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command) and not in_single:
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if char != "`" or in_single:
            index += 1
            continue

        body_start = index + 1
        cursor = body_start
        while cursor < len(command):
            if command[cursor] == "\\" and cursor + 1 < len(command):
                cursor += 2
                continue
            if command[cursor] == "`":
                bodies.append(command[body_start:cursor])
                index = cursor + 1
                break
            cursor += 1
        else:
            index += 1
    return bodies


def _is_env_assignment(token: str) -> bool:
    key, sep, _value = token.partition("=")
    return bool(sep and key and key.replace("_", "A").isalnum() and not key[0].isdigit())


def _skip_options(args: list[str], index: int, opts_with_arg: set[str]) -> int:
    while index < len(args):
        token = args[index]
        if token == "--":
            return index + 1
        if not token.startswith("-") or token == "-":
            return index
        option_name = token.split("=", 1)[0]
        index += 1
        if "=" not in token and option_name in opts_with_arg and index < len(args):
            index += 1
    return index


def _function_body_tokens(tokens: list[str]) -> list[str]:
    if len(tokens) >= 4 and tokens[1] == "()" and tokens[2] == "{":
        return tokens[3:]
    if len(tokens) >= 5 and tokens[0] == "function" and tokens[2] == "{":
        return tokens[3:]
    if (
        len(tokens) >= 6
        and tokens[0] == "function"
        and tokens[2] == "()"
        and tokens[3] == "{"
    ):
        return tokens[4:]
    return []


def _case_body_token_groups(tokens: list[str]) -> list[list[str]]:
    if not tokens or tokens[0] != "case" or ")" not in tokens:
        return []
    groups: list[list[str]] = []
    index = 0
    while ")" in tokens[index:]:
        start = tokens.index(")", index) + 1
        body: list[str] = []
        index = start
        while index < len(tokens):
            token = tokens[index]
            if token in {";;", ";&", ";;&", "esac"}:
                index += 1
                break
            body.append(token)
            index += 1
        if body:
            groups.append(body)
    return groups


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
    "-P",
    "--bare",
    "--glob-pathspecs",
    "--icase-pathspecs",
    "--literal-pathspecs",
    "--no-pager",
    "--no-replace-objects",
    "--noglob-pathspecs",
    "--paginate",
}

_UNRESOLVED_GIT_CONFIG_ENV_ALIAS = "__AGENT_ISSUES_UNRESOLVED_GIT_CONFIG_ENV_ALIAS__"


def _git_global_option_info(args: list[str], index: int) -> tuple[str, str | None, int] | None:
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
        info = _git_global_option_info(args, index)
        if info is None:
            break
        option, value, next_index = info
        if option == "-C" and value is not None:
            cwd = _clean_path(value, cwd=cwd)
        index = next_index
    return cwd


def _git_option_aliases(invocation: Invocation) -> tuple[dict[str, str], int]:
    aliases: dict[str, str] = {}
    args = list(invocation.args)
    index = 0
    while index < len(args):
        info = _git_global_option_info(args, index)
        if info is None:
            break
        option, config_value, next_index = info
        index = next_index
        if option not in {"-c", "--config-env"}:
            continue

        key, sep, value = config_value.partition("=") if config_value else ("", "", "")
        if not sep or not key.startswith("alias."):
            continue
        if option == "--config-env":
            value = invocation.env.get(value, _UNRESOLVED_GIT_CONFIG_ENV_ALIAS)
        aliases[key.removeprefix("alias.")] = value
    return aliases, index


def _git_inline_alias_payload(invocation: Invocation) -> str | None:
    if invocation.basename != "git":
        return None
    args = list(invocation.args)
    aliases, index = _git_option_aliases(invocation)
    if index >= len(args):
        return None
    subcommand = args[index]
    alias = aliases.get(subcommand)
    if alias is None:
        return None
    if alias == _UNRESOLVED_GIT_CONFIG_ENV_ALIAS:
        return "eval $AGENT_ISSUES_UNRESOLVED_GIT_CONFIG_ENV_ALIAS"
    rest = " ".join(shlex.quote(arg) for arg in args[index + 1 :])
    if alias.startswith("!"):
        return f"{alias[1:]} {rest}".strip()
    return f"git {alias} {rest}".strip()


def _trap_payload(invocation: Invocation) -> str | None:
    if invocation.basename != "trap":
        return None
    args = list(invocation.args)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token in {"-l", "-p"}:
            index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        break
    if index >= len(args):
        return None
    payload = args[index]
    if payload == "-":
        return None
    if len(args) == index + 1 and payload.upper() in {"0", "EXIT"}:
        return None
    return payload


_SHELL_CONTROL_KEYWORDS = {
    "!",
    "(",
    ")",
    "{",
    "}",
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "fi",
    "for",
    "if",
    "in",
    "select",
    "then",
    "until",
    "while",
}

_WRITE_REDIRECTS = {">", ">|", ">>", ">>|", "&>", "&>>", ">&", "<>"}
_ALL_REDIRECTS = _WRITE_REDIRECTS | {"<", "<<", "<<-", "<<<"}


def _redirection_targets(tokens: list[str]) -> tuple[str, ...]:
    targets: list[str] = []
    for index, token in enumerate(tokens):
        target_index: int | None = None
        if token in _WRITE_REDIRECTS:
            target_index = index + 1
        elif token.isdigit() and index + 1 < len(tokens) and tokens[index + 1] in _WRITE_REDIRECTS:
            target_index = index + 2
        if target_index is not None and target_index < len(tokens):
            targets.append(tokens[target_index])
    return tuple(targets)


def _skip_redirection(tokens: list[str], index: int) -> int:
    if tokens[index] in _ALL_REDIRECTS and index + 1 < len(tokens):
        return index + 2
    if (
        tokens[index].isdigit()
        and index + 1 < len(tokens)
        and tokens[index + 1] in _ALL_REDIRECTS
    ):
        return index + 3
    return index


def _unwrap_invocation(tokens: list[str]) -> Invocation | None:
    env: dict[str, str] = {}
    wrapper_cwd = "."
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
        if name in _SHELL_CONTROL_KEYWORDS:
            index += 1
            continue
        if name == "coproc":
            index += 1
            continue
        if name in {"time", "nohup", "command", "builtin"}:
            index += 1
            index = _skip_options(tokens, index, set())
            continue
        if name == "exec":
            index += 1
            index = _skip_options(tokens, index, {"-a"})
            continue
        if name == "sudo":
            index += 1
            index = _skip_options(
                tokens,
                index,
                {
                    "-C",
                    "-D",
                    "-R",
                    "-T",
                    "-g",
                    "-h",
                    "-p",
                    "-r",
                    "-t",
                    "-u",
                    "--chdir",
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
        if name == "nice":
            index += 1
            index = _skip_options(tokens, index, {"-n"})
            continue
        if name == "timeout":
            index += 1
            index = _skip_options(tokens, index, {"-s", "--signal", "-k", "--kill-after"})
            if index < len(tokens):
                index += 1  # timeout duration
            continue
        if name == "env":
            index += 1
            while index < len(tokens):
                if _is_env_assignment(tokens[index]):
                    key, value = tokens[index].split("=", 1)
                    env[key] = value
                    index += 1
                    continue
                if tokens[index] == "--":
                    index += 1
                    continue
                if tokens[index].startswith("-") and tokens[index] not in {"-", "--"}:
                    if tokens[index] in {"-u", "--unset"}:
                        index += 2
                    elif tokens[index] in {"-C", "--chdir"} and index + 1 < len(tokens):
                        wrapper_cwd = _clean_path(tokens[index + 1], cwd=wrapper_cwd)
                        index += 2
                    elif tokens[index].startswith("--chdir="):
                        wrapper_cwd = _clean_path(
                            tokens[index].split("=", 1)[1], cwd=wrapper_cwd
                        )
                        index += 1
                    elif tokens[index].startswith("-C") and len(tokens[index]) > 2:
                        wrapper_cwd = _clean_path(tokens[index][2:], cwd=wrapper_cwd)
                        index += 1
                    elif (
                        tokens[index] in {"-S", "--split-string"}
                        or tokens[index].startswith("-S")
                        or tokens[index].startswith("--split-string=")
                    ):
                        return None
                    else:
                        index += 1
                    continue
                break
            continue
        break

    if index >= len(tokens):
        return None
    return Invocation(
        env=env,
        executable=tokens[index],
        args=tuple(tokens[index + 1 :]),
        redirection_targets=redirection_targets,
        cwd=wrapper_cwd,
    )


def _shell_c_payload(invocation: Invocation) -> str | None:
    if invocation.basename not in {"sh", "bash", "zsh", "dash"}:
        return None
    args = list(invocation.args)
    for index, token in enumerate(args):
        if token in {"-c", "--command"} and index + 1 < len(args):
            return args[index + 1]
        if token.startswith("--command="):
            return token.split("=", 1)[1]
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            command_suffix = token[1:].split("c", 1)[1]
            if command_suffix:
                return command_suffix
            if index + 1 < len(args):
                return args[index + 1]
    return None


def _env_split_payload(tokens: list[str]) -> tuple[str, str] | None:
    for index, token in enumerate(tokens):
        if os.path.basename(token) != "env":
            continue
        rest = tokens[index + 1 :]
        payload_cwd = "."
        for arg_index, arg in enumerate(rest):
            if arg in {"-C", "--chdir"} and arg_index + 1 < len(rest):
                payload_cwd = _clean_path(rest[arg_index + 1], cwd=payload_cwd)
                continue
            if arg.startswith("--chdir="):
                payload_cwd = _clean_path(arg.split("=", 1)[1], cwd=payload_cwd)
                continue
            if arg.startswith("-C") and len(arg) > 2:
                payload_cwd = _clean_path(arg[2:], cwd=payload_cwd)
                continue
            if arg in {"-S", "--split-string"} and arg_index + 1 < len(rest):
                trailing = " ".join(shlex.quote(part) for part in rest[arg_index + 2 :])
                return f"{rest[arg_index + 1]} {trailing}".strip(), payload_cwd
            if arg.startswith("-S") and len(arg) > 2:
                trailing = " ".join(shlex.quote(part) for part in rest[arg_index + 1 :])
                return f"{arg[2:]} {trailing}".strip(), payload_cwd
            if arg.startswith("--split-string="):
                trailing = " ".join(shlex.quote(part) for part in rest[arg_index + 1 :])
                return f"{arg.split('=', 1)[1]} {trailing}".strip(), payload_cwd
    return None


def command_invocations(command: str, *, initial_cwd: str = ".") -> list[Invocation]:
    """Return executable invocations, following common transparent wrappers."""

    command, shell_heredocs = _strip_heredocs(command)
    invocations: list[Invocation] = []
    cwd = initial_cwd
    previous_cwd = initial_cwd
    for body in shell_heredocs:
        invocations.extend(command_invocations(body, initial_cwd=cwd))
    for body in _extract_subshells(command):
        invocations.extend(command_invocations(body, initial_cwd=cwd))
    for body in _extract_backticks(command):
        invocations.extend(command_invocations(body, initial_cwd=cwd))
    for tokens in _shell_segments(command):
        function_body = _function_body_tokens(tokens)
        if function_body:
            invocations.extend(command_invocations(" ".join(function_body), initial_cwd=cwd))

        for case_body in _case_body_token_groups(tokens):
            invocations.extend(command_invocations(" ".join(case_body), initial_cwd=cwd))

        env_payload = _env_split_payload(tokens)
        if env_payload:
            payload, payload_cwd = env_payload
            invocations.extend(
                command_invocations(payload, initial_cwd=_clean_path(payload_cwd, cwd=cwd))
            )

        invocation = _unwrap_invocation(tokens)
        if invocation is None:
            redirection_targets = _redirection_targets(tokens)
            if redirection_targets:
                invocations.append(
                    Invocation(
                        env={},
                        executable="",
                        args=(),
                        redirection_targets=redirection_targets,
                        cwd=cwd,
                    )
                )
            continue
        invocation_cwd = _clean_path(invocation.cwd, cwd=cwd)
        invocation = replace(invocation, cwd=invocation_cwd)
        if invocation.basename == "eval" and invocation.args:
            payload = " ".join(invocation.args)
            if "$" in payload:
                invocations.append(invocation)
            else:
                invocations.extend(command_invocations(payload, initial_cwd=invocation.cwd))
            continue

        payload = _shell_c_payload(invocation)
        if payload is not None:
            invocations.extend(command_invocations(payload, initial_cwd=invocation.cwd))
            continue
        invocations.append(invocation)
        if invocation.basename == "cd" and invocation.args:
            old_cwd = cwd
            if invocation.args[0] == "-":
                cwd = previous_cwd
            else:
                cwd = _clean_path(invocation.args[0], cwd=cwd)
            previous_cwd = old_cwd
    return invocations


def _coerce_timeout_ms(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _timeout_ms_from_transcript_lines(lines: list[str] | deque[str], tool_use_id: str) -> int | None:
    for line in reversed(lines):
        if tool_use_id not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = entry.get("payload", {})
        if payload.get("type") != "function_call" or payload.get("call_id") != tool_use_id:
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, str):
            continue
        try:
            decoded_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            continue
        return _coerce_timeout_ms(decoded_arguments.get("timeout_ms"))
    return None


def tool_timeout_ms(data: dict[str, Any]) -> int | None:
    tool_input = data.get("tool_input", {})
    if isinstance(tool_input, dict):
        for key in ("timeout_ms", "timeout"):
            direct_timeout = _coerce_timeout_ms(tool_input.get(key))
            if direct_timeout is not None:
                return direct_timeout

    transcript_path = data.get("transcript_path")
    tool_use_id = data.get("tool_use_id")
    if not isinstance(transcript_path, str) or not isinstance(tool_use_id, str):
        return None
    try:
        with Path(transcript_path).open(encoding="utf-8") as handle:
            return _timeout_ms_from_transcript_lines(deque(handle, maxlen=300), tool_use_id)
    except OSError:
        return None


def _git_subcommand(invocation: Invocation) -> tuple[str, list[str]] | None:
    if invocation.basename != "git":
        return None
    args = list(invocation.args)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        info = _git_global_option_info(args, index)
        if info is not None:
            _option, _value, index = info
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(args):
        return None
    return args[index], args[index + 1 :]


def _git_push_args(invocation: Invocation) -> list[str] | None:
    subcommand = _git_subcommand(invocation)
    if subcommand is None:
        return None
    name, rest = subcommand
    if name != "push":
        return None
    return rest


def _push_uses_force(args: list[str]) -> bool:
    for arg in args:
        if arg in {"--force", "--force-with-lease", "--force-if-includes"}:
            return True
        if arg.startswith("--force-with-lease="):
            return True
        if arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]:
            return True
    return False


_SWITCH_OPTS_WITH_ARG = {"-c", "-C", "--create", "--force-create", "--orphan"}
_CHECKOUT_OPTS_WITH_ARG = {"-b", "-B", "--orphan", "--pathspec-from-file"}


def _branch_target(args: list[str], opts_with_arg: set[str], *, checkout: bool) -> str | None:
    if checkout and "--" in args and args.index("--") < len(args) - 1:
        return None
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return None if checkout else (args[index + 1] if index + 1 < len(args) else None)
        if token in {"-d", "--detach"}:
            return args[index + 1] if index + 1 < len(args) else "HEAD"
        if (
            token.startswith("--create=")
            or token.startswith("--force-create=")
            or token.startswith("--orphan=")
        ):
            return token.split("=", 1)[1]
        if token.startswith(("-b", "-B")) and token not in {"-b", "-B"}:
            return token[2:]
        if not checkout and token.startswith(("-c", "-C")) and token not in {"-c", "-C"}:
            return token[2:]
        if token in opts_with_arg:
            if token in {"-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan"}:
                return args[index + 1] if index + 1 < len(args) else ""
            index += 2
            continue
        if token == "-":
            return token
        if token.startswith("-"):
            index += 1
            continue
        if checkout and index + 1 < len(args) and args[index + 1] == "--":
            return token
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
    args = list(invocation.args)
    chain: list[str] = []
    index = 0
    opts_with_arg = {"-R", "--repo", "--hostname"}
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            continue
        if token in opts_with_arg:
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


def _clean_path(path: str, *, cwd: str = ".") -> str:
    path = path.removeprefix(":(top)")
    path = path.removeprefix(":/")
    path = path.removeprefix("./")
    path_obj = Path(path)
    if path_obj.is_absolute():
        try:
            path = str(path_obj.relative_to(Path.cwd()))
        except ValueError:
            path = str(path_obj)
    elif cwd not in {"", "."}:
        path = posixpath.join(cwd, path)
    path = posixpath.normpath(path)
    return path.rstrip("/")


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
        if glob_prefix and _path_matches(
            glob_prefix, protected, include_ancestors=True
        ):
            return True
        return fnmatch.fnmatch(protected, path) or fnmatch.fnmatch(
            f"{protected}/__agent_issues_child__", path
        )
    if path == protected or path.startswith(f"{protected}/"):
        return True
    if include_ancestors:
        return path in {"", ".", "/"} or protected.startswith(f"{path}/")
    return False


def _binary_matches(executable: str, basename: str, pattern: str, *, cwd: str = ".") -> bool:
    normalized = _clean_path(executable, cwd=cwd)
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)


def _arg_targets_generated(
    arg: str,
    paths: tuple[str, ...],
    *,
    include_ancestors: bool = False,
    cwd: str = ".",
) -> bool:
    arg = arg.split("=", 1)[-1] if "=" in arg and not arg.startswith("-") else arg
    return any(
        _path_matches(arg, path, include_ancestors=include_ancestors, cwd=cwd)
        for path in paths
    )


def _generated_mutation(invocation: Invocation, config: HookConfig) -> bool:
    if not config.generated_paths:
        return False
    if invocation.basename in {"rm", "mv", "cp", "install", "touch", "truncate", "mkdir", "rmdir"}:
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=invocation.cwd,
            )
            for arg in invocation.args
        )
    if invocation.basename == "tee":
        return any(
            _arg_targets_generated(arg, config.generated_paths, cwd=invocation.cwd)
            for arg in invocation.args
            if not arg.startswith("-")
        )
    if invocation.basename == "sed" and any(arg.startswith("-i") for arg in invocation.args):
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=invocation.cwd,
            )
            for arg in invocation.args
        )
    if invocation.basename == "perl" and any(arg.startswith("-pi") for arg in invocation.args):
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
        if not pathspecs:
            return True
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=git_cwd,
            )
            for arg in pathspecs
        )
    if name == "reset" and "--hard" in rest:
        return True
    if name in {"rm", "mv"}:
        return any(
            _arg_targets_generated(
                arg,
                config.generated_paths,
                include_ancestors=True,
                cwd=git_cwd,
            )
            for arg in rest
        )
    return name in {"checkout", "restore"} and any(
        _arg_targets_generated(
            arg,
            config.generated_paths,
            include_ancestors=True,
            cwd=git_cwd,
        )
        for arg in rest
    )


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

        if invocation.basename == "shopt" and "expand_aliases" in invocation.args:
            return (
                "Do not enable shell alias expansion in hook-checked commands; "
                "aliases can hide commands from static policy checks."
            )

        if invocation.basename == "eval" and any("$" in arg for arg in invocation.args):
            return (
                "Do not use eval with unresolved shell expansions; the hook cannot "
                "verify the command that eval will execute."
            )

        trap_payload = _trap_payload(invocation)
        if trap_payload is not None:
            trap_message = rejection_message(
                trap_payload,
                config,
                timeout_ms=timeout_ms,
                dirty_generated_output=dirty_generated_output,
                cwd=invocation.cwd,
            )
            if trap_message is not None:
                return trap_message

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
                    f"Do not invoke internal Makefile target '{target}' directly. "
                    f"Use '{config.internal_make_targets[target]}' instead."
                )
            minimum = config.make_targets_requiring_timeout_ms.get(target)
            if minimum is not None and (timeout_ms is None or timeout_ms < minimum):
                return _short_timeout_message(f"make {target}", timeout_ms, minimum)

        for block in config.command_family_blocks:
            if invocation.basename != block.command:
                continue
            subcommand = invocation.args[0] if invocation.args else ""
            suggestion = block.subcommands.get(subcommand)
            if suggestion:
                return f"{block.message} Use '{suggestion}' instead."
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

    tool_name = data.get("tool_name")
    if tool_name is not None and tool_name != "Bash":
        return None
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None
    return rejection_message(command, config, timeout_ms=tool_timeout_ms(data))
