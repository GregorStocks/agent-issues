"""Tests for the shared PreToolUse hook runner."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues import pretool_hook
from agent_issues.cli import agent_pretool_hook
from agent_issues.pretool_hook import BinaryBlock, CommandFamilyBlock, HookConfig, rejection_message


def _config() -> HookConfig:
    return HookConfig(
        branch_switch_signoff_env="PROJECT_BRANCH_SWITCH_SIGNOFF",
        generated_paths=("data/generated",),
        generated_command="make generate",
        command_family_blocks=(
            CommandFamilyBlock(
                command="cargo",
                message="Do not run cargo directly.",
                subcommands={"test": "make test"},
            ),
        ),
        binary_blocks=(
            BinaryBlock(
                pattern="target/*/project-cli",
                message="Do not run project-cli directly. Use make test.",
            ),
        ),
        internal_make_targets={"_generate": "make generate"},
        make_targets_requiring_timeout_ms={"generate": 70 * 60 * 1000},
        github_issue_guidance="local JSON5 issue files in issues/",
    )


def test_blocks_raw_publish_paths() -> None:
    assert "agent-submit" in (
        rejection_message("git push origin HEAD", _config(), dirty_generated_output=False)
        or ""
    )
    assert "agent-submit" in (
        rejection_message("git-push origin HEAD", _config(), dirty_generated_output=False)
        or ""
    )
    assert "agent-submit" in (
        rejection_message("git send-pack repo HEAD:refs/heads/main", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("gh pr edit 12 --title T", _config()) or ""
    )


def test_reports_force_push_guidance() -> None:
    assert "agent-submit --force" in (
        rejection_message("git push --force origin HEAD", _config()) or ""
    )


def test_blocks_push_when_generated_output_is_dirty() -> None:
    message = rejection_message(
        "git push origin HEAD",
        _config(),
        dirty_generated_output=True,
    )
    assert message is not None
    assert "data/generated" in message


def test_blocks_github_issue_commands() -> None:
    assert "GitHub Issues" in (
        rejection_message("gh issue create --title Bug", _config()) or ""
    )


def test_blocks_kill_by_name() -> None:
    assert "pkill/killall" in (rejection_message("sudo pkill python", _config()) or "")


def test_branch_switch_requires_signoff() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in (
        rejection_message("git switch feature", _config()) or ""
    )
    assert (
        rejection_message(
            "PROJECT_BRANCH_SWITCH_SIGNOFF=feature git switch feature",
            _config(),
        )
        is None
    )


def test_generated_paths_are_updated_only_by_generator() -> None:
    assert "generated output" in (
        rejection_message("rm data/generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("sed -i s/a/b/ data/generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("git restore data/generated/file.txt", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message("printf x > data/generated/file.txt", _config()) or ""
    )


def test_generated_paths_track_common_cwd_wrappers() -> None:
    assert "generated output" in (
        rejection_message("cd data && rm generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("env -C data rm generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("sudo -D data rm generated/file.txt", _config()) or ""
    )


def test_allows_generated_reads() -> None:
    assert rejection_message("cp data/generated/file.txt /tmp/file.txt", _config()) is None


def test_recurses_into_simple_shell_c_payload() -> None:
    assert "agent-submit" in (
        rejection_message("bash -c 'git push origin HEAD'", _config()) or ""
    )


def test_inline_git_aliases_are_inspected() -> None:
    assert "agent-submit" in (
        rejection_message("git -c alias.p='push origin HEAD' p", _config()) or ""
    )


def test_common_wrappers_do_not_hide_commands() -> None:
    assert "agent-submit" in (
        rejection_message("nice --adjustment 10 git push origin HEAD", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("timeout 10 git push origin HEAD", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("env FOO=bar git push origin HEAD", _config()) or ""
    )


def test_agent_submit_timeout_guidance() -> None:
    message = rejection_message("agent-submit --title T --body B", _config(), timeout_ms=60_000)
    assert message is not None
    assert "70 minutes" in message


def test_make_target_guidance() -> None:
    assert "make generate" in (
        rejection_message("make generate", _config(), timeout_ms=None) or ""
    )
    assert "make generate" in (
        rejection_message("make _generate", _config(), timeout_ms=70 * 60 * 1000) or ""
    )


def test_repo_specific_command_and_binary_blocks() -> None:
    assert (
        rejection_message("env RUSTFLAGS=-Dwarnings cargo test", _config())
        == "Do not run cargo directly. Use `make test` instead."
    )
    assert (
        rejection_message("./target/debug/project-cli --help", _config())
        == "Do not run project-cli directly. Use make test."
    )


def test_extracts_timeout_from_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "payload": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "shell_command",
                    "arguments": json.dumps({"timeout_ms": 12345}),
                }
            }
        )
        + "\n"
    )
    data = {"transcript_path": str(transcript), "tool_use_id": "call_1", "tool_input": {}}
    assert pretool_hook.tool_timeout_ms(data) == 12345


def test_extracts_nested_shell_timeout_from_in_flight_code_mode_call(
    tmp_path: Path,
) -> None:
    command = "agent-submit --title T --body B"
    source = (
        'const result = await tools.shell_command({command: '
        + json.dumps(command)
        + ', timeout_ms: 4500000, workdir: "/repo"}); text(result)'
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call_outer",
                    "name": "exec",
                    "input": source,
                }
            }
        )
        + "\n"
    )
    data = {
        "transcript_path": str(transcript),
        "tool_use_id": "exec-inner-id-not-in-transcript",
        "tool_input": {"command": command},
    }

    assert pretool_hook.tool_timeout_ms(data, command) == 4_500_000


def test_code_mode_timeout_matches_exact_nested_command(tmp_path: Path) -> None:
    wanted_command = "agent-submit --title T --body B"
    other_call = json.dumps({"command": "make test", "timeout_ms": 600_000})
    wanted_call = json.dumps({"command": wanted_command, "timeout_ms": 4_500_000})
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call_outer",
                    "name": "exec",
                    "input": (
                        "const results = await Promise.all(["
                        f"tools.shell_command({other_call}),"
                        f"tools.shell_command({wanted_call})"
                        "]);"
                    ),
                }
            }
        )
        + "\n"
    )
    data = {
        "transcript_path": str(transcript),
        "tool_use_id": "exec-inner",
        "tool_input": {},
    }

    assert pretool_hook.tool_timeout_ms(data, wanted_command) == 4_500_000
    assert pretool_hook.tool_timeout_ms(data, "missing") is None


@pytest.mark.parametrize(
    "source",
    [
        'const args = {"command": "agent-submit", "timeout_ms": 4500000}; '
        "tools.shell_command(args)",
        "tools.shell_command({command: 'agent-submit', timeout_ms: 4500000})",
        'tools.shell_command({command: "agent-submit", timeout_ms: 4500000,})',
        'tools.shell_command({"command": "agent-submit", "timeout_ms": 4500000}); '
        'tools.shell_command({"command": "agent-submit", "timeout_ms": 4500000})',
    ],
)
def test_code_mode_timeout_fails_closed_for_unstructured_or_ambiguous_calls(
    tmp_path: Path, source: str
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call_outer",
                    "name": "exec",
                    "input": source,
                }
            }
        )
        + "\n"
    )
    data = {
        "transcript_path": str(transcript),
        "tool_use_id": "exec-inner",
        "tool_input": {},
    }

    assert pretool_hook.tool_timeout_ms(data, "agent-submit") is None


def test_code_mode_timeout_ignores_completed_outer_calls(tmp_path: Path) -> None:
    command = "agent-submit --title T --body B"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "payload": {
                            "type": "custom_tool_call",
                            "call_id": "call_old",
                            "name": "exec",
                            "input": "tools.shell_command("
                            + json.dumps({"command": command, "timeout_ms": 4_500_000})
                            + ")",
                        }
                    }
                ),
                json.dumps(
                    {
                        "payload": {
                            "type": "custom_tool_call_output",
                            "call_id": "call_old",
                            "output": "done",
                        }
                    }
                ),
            ]
        )
        + "\n"
    )
    data = {
        "transcript_path": str(transcript),
        "tool_use_id": "exec-inner",
        "tool_input": {},
    }

    assert pretool_hook.tool_timeout_ms(data, command) is None


def test_evaluate_ignores_non_bash_tools() -> None:
    assert (
        pretool_hook.evaluate_hook_input(
            {"tool_name": "Read", "tool_input": {"command": "git push"}},
            _config(),
        )
        is None
    )


def test_load_config_reads_json5(tmp_path: Path) -> None:
    path = tmp_path / "hook.json5"
    path.write_text(
        """
{
  branch_switch_signoff_env: "X_SIGNOFF",
  generated_paths: ["out/generated"],
  command_family_blocks: [
    {command: "rustfmt", message: "Use make fmt."},
  ],
}
"""
    )
    config = pretool_hook.load_config(path)
    assert config.branch_switch_signoff_env == "X_SIGNOFF"
    assert config.generated_paths == ("out/generated",)
    assert config.command_family_blocks[0].command == "rustfmt"


def test_cli_blocks_with_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "hook.json5"
    config.write_text("{minimum_agent_submit_timeout_ms: 4200000}\n")
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "agent-submit --title T --body B"},
        }
    )
    with patch.object(sys, "argv", ["agent-pretool-hook", "--config", str(config)]):
        with patch("sys.stdin", new=io.StringIO(payload)):
            with pytest.raises(SystemExit) as exc:
                agent_pretool_hook.main()
    assert exc.value.code == 2
    assert "agent-submit" in capsys.readouterr().err


def test_cli_allows_non_bash(tmp_path: Path) -> None:
    config = tmp_path / "hook.json5"
    config.write_text("{}\n")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"command": "git push"}})
    with patch.object(sys, "argv", ["agent-pretool-hook", "--config", str(config)]):
        with patch("sys.stdin", new=io.StringIO(payload)):
            agent_pretool_hook.main()


def test_dirty_generated_status_is_checked_only_for_push() -> None:
    config = _config()
    with patch(
        "agent_issues.pretool_hook.subprocess.run",
        return_value=CompletedProcess(args=[], returncode=0, stdout=" M data/generated/x\n"),
    ) as run_mock:
        message = rejection_message("git push origin HEAD", config)
    assert "generated output" in (message or "")
    assert run_mock.call_args.args[0][:4] == ["git", "status", "--short", "--"]


SUBMIT_COMMAND = "agent-submit --title T --body B"


def _transcript_hook(
    tmp_path: Path, *payloads: object, call_id: str = "exec-inner"
) -> dict:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps({"type": "response_item", "payload": item}) for item in payloads
        )
        + "\n"
    )
    return {
        "tool_name": "Shell",
        "tool_input": {"command": SUBMIT_COMMAND},
        "tool_use_id": call_id,
        "transcript_path": str(transcript),
    }


def _outer_call(source: str, call_id: str = "outer") -> dict:
    return {
        "type": "custom_tool_call",
        "name": "exec",
        "call_id": call_id,
        "input": source,
    }


@pytest.mark.parametrize("name", ["exec_command", "functions.exec_command"])
@pytest.mark.parametrize("yield_ms", [None, 1000, 30000])
def test_native_persistent_session_allows_submit(
    name: str, yield_ms: int | None
) -> None:
    arguments = {"cmd": SUBMIT_COMMAND}
    if yield_ms is not None:
        arguments["yield_time_ms"] = yield_ms
    data = {"tool_name": name, "tool_input": arguments}
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
    assert pretool_hook.tool_execution(data).persistent
    assert pretool_hook.tool_timeout_ms(data) is None


@pytest.mark.parametrize(
    "name,field",
    [("Bash", "timeout"), ("Shell", "timeout_ms"), ("shell_command", "timeout_ms")],
)
@pytest.mark.parametrize(
    "timeout,allowed", [(None, False), (60000, False), (4200000, True)]
)
def test_bounded_tools_keep_minimum_timeout(
    name: str, field: str, timeout: int | None, allowed: bool
) -> None:
    data = {
        "tool_name": name,
        "tool_input": {
            "command": SUBMIT_COMMAND,
            field: timeout,
            "yield_time_ms": 1000,
        },
    }
    message = pretool_hook.evaluate_hook_input(data, _config())
    assert (message is None) == allowed
    if not allowed:
        assert "70 minutes" in message
    assert not pretool_hook.tool_execution(data).persistent


@pytest.mark.parametrize("name", ["exec_command", "functions.exec_command"])
def test_persistent_session_from_matching_function_call(
    tmp_path: Path, name: str
) -> None:
    data = _transcript_hook(
        tmp_path,
        {
            "type": "function_call",
            "name": name,
            "call_id": "native",
            "arguments": json.dumps({"cmd": SUBMIT_COMMAND, "yield_time_ms": 1000}),
        },
        call_id="native",
    )
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
    assert pretool_hook.tool_timeout_ms(data) is None


@pytest.mark.parametrize("outer_name", ["exec", "functions.exec"])
def test_persistent_session_nested_in_code_mode(
    tmp_path: Path, outer_name: str
) -> None:
    # This reproduces the invocation that originally blocked submission.
    payload = _outer_call(
        "const result = await tools.exec_command({cmd: "
        + json.dumps(SUBMIT_COMMAND)
        + ", yield_time_ms: 1000}); text(result)"
    )
    payload["name"] = outer_name
    data = _transcript_hook(tmp_path, payload)
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
    assert pretool_hook.tool_timeout_ms(data) is None


def test_nested_session_matches_only_the_requested_command(tmp_path: Path) -> None:
    source = (
        'await tools.shell_command({command: "make test", timeout_ms: 60000});'
        "text(await tools.exec_command({cmd: "
        + json.dumps(SUBMIT_COMMAND)
        + ", yield_time_ms: 1000}));"
    )
    data = _transcript_hook(tmp_path, _outer_call(source))
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
    data["tool_input"]["command"] = "agent-submit --title other"
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


@pytest.mark.parametrize("field", ["timeout", "timeout_ms"])
@pytest.mark.parametrize("timeout", [60000, 4200000])
def test_persistent_tools_do_not_accept_fabricated_timeouts(
    tmp_path: Path, field: str, timeout: int
) -> None:
    arguments = {"cmd": SUBMIT_COMMAND, field: timeout}
    data = {"tool_name": "exec_command", "tool_input": arguments}
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None
    data = _transcript_hook(
        tmp_path, _outer_call("await tools.exec_command(" + json.dumps(arguments) + ")")
    )
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None
    assert pretool_hook.tool_timeout_ms(data) is None


@pytest.mark.parametrize("name", ["unknown_exec", "my_exec_command", "shell_command"])
def test_unrecognized_or_bounded_transcript_tools_cannot_claim_persistence(
    tmp_path: Path, name: str
) -> None:
    data = _transcript_hook(
        tmp_path,
        {
            "type": "function_call",
            "name": name,
            "call_id": "native",
            "arguments": json.dumps(
                {"cmd": SUBMIT_COMMAND, "yield_time_ms": 1000, "persistent": True}
            ),
        },
        call_id="native",
    )
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None
    assert not pretool_hook.tool_execution(data).persistent


@pytest.mark.parametrize(
    "source",
    [
        'tools.exec_command({cmd: "make test", yield_time_ms: 1000})',
        'tools.unknown_exec({cmd: "agent-submit --title T --body B", timeout_ms: 4200000})',
        'tools.exec_command({cmd: "agent-submit --title T --body B"}); tools.exec_command({cmd: "agent-submit --title T --body B"})',
        'tools.exec_command({cmd: "agent-submit --title T --body B"}); tools.shell_command({command: "agent-submit --title T --body B"})',
        'const args = {cmd: "agent-submit --title T --body B"}; tools.exec_command(args)',
        '// tools.exec_command({cmd: "agent-submit --title T --body B"})',
        '/* tools.exec_command({cmd: "agent-submit --title T --body B"}) */',
        """text('tools.exec_command({cmd: "agent-submit --title T --body B"})')""",
        'text(`tools.exec_command({cmd: "agent-submit --title T --body B"})`)',
        '/tools.exec_command({cmd: "agent-submit --title T --body B"})/',
        'other.tools.exec_command({cmd: "agent-submit --title T --body B"})',
        'mytools.exec_command({cmd: "agent-submit --title T --body B"})',
        'tools.exec_command({cmd: "make test", command: "agent-submit --title T --body B"})',
        'tools.exec_command({command: "agent-submit --title T --body B"})',
        "tools.shell_command("
        + json.dumps(
            {
                "command": "echo 'tools.exec_command({cmd: \"agent-submit --title T --body B\"})'"
            }
        )
        + ")",
    ],
)
def test_nested_session_matching_fails_closed(tmp_path: Path, source: str) -> None:
    data = _transcript_hook(tmp_path, _outer_call(source))
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


def test_comments_and_strings_do_not_make_a_real_call_ambiguous(tmp_path: Path) -> None:
    real_call = "tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})"
    data = _transcript_hook(
        tmp_path,
        _outer_call(
            "// "
            + real_call
            + "\n/* "
            + real_call
            + " */\ntext("
            + json.dumps(real_call)
            + "); await "
            + real_call
        ),
    )
    assert pretool_hook.evaluate_hook_input(data, _config()) is None


@pytest.mark.parametrize(
    "case",
    [
        "completed",
        "unrelated_latest",
        "multiple_active",
        "wrong_id",
        "wrong_type",
        "wrong_command",
        "message",
    ],
)
def test_session_metadata_must_identify_the_current_call(
    tmp_path: Path, case: str
) -> None:
    source = "tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})"
    payloads = [_outer_call(source)]
    call_id = "exec-inner"
    if case == "completed":
        payloads.append(
            {"type": "custom_tool_call_output", "call_id": "outer", "output": "done"}
        )
    elif case == "unrelated_latest":
        payloads.extend(
            [
                {
                    "type": "custom_tool_call_output",
                    "call_id": "outer",
                    "output": "done",
                },
                _outer_call('tools.exec_command({cmd: "make test"})', "new"),
            ]
        )
    elif case == "multiple_active":
        payloads.append(_outer_call(source, "second"))
    elif case in {"wrong_id", "wrong_type", "wrong_command"}:
        payloads = [
            {
                "type": "message" if case == "wrong_type" else "function_call",
                "name": "exec_command",
                "call_id": "native",
                "arguments": json.dumps(
                    {"cmd": "make test" if case == "wrong_command" else SUBMIT_COMMAND}
                ),
            }
        ]
        call_id = "different" if case == "wrong_id" else "native"
    elif case == "message":
        payloads = [{"type": "message", "role": "user", "content": source}]
    data = _transcript_hook(tmp_path, *payloads, call_id=call_id)
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


def test_matching_direct_call_takes_precedence_over_unrelated_outer(
    tmp_path: Path,
) -> None:
    data = _transcript_hook(
        tmp_path,
        _outer_call("tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})"),
        {
            "type": "function_call",
            "name": "shell_command",
            "call_id": "native",
            "arguments": json.dumps({"command": SUBMIT_COMMAND, "timeout_ms": 60000}),
        },
        call_id="native",
    )
    assert "70 minutes" in pretool_hook.evaluate_hook_input(data, _config())


def test_input_short_timeout_is_not_overridden_by_transcript(tmp_path: Path) -> None:
    data = _transcript_hook(
        tmp_path,
        _outer_call(
            "tools.shell_command({command: "
            + json.dumps(SUBMIT_COMMAND)
            + ", timeout_ms: 4200000})"
        ),
    )
    data["tool_input"]["timeout_ms"] = 60000
    assert "only 1.0 minutes" in pretool_hook.evaluate_hook_input(data, _config())


@pytest.mark.parametrize(
    "command,expected",
    [
        ("agent-submit; pkill python", "pkill/killall"),
        ("gh pr edit 12 --title T", "agent-submit"),
        ("make generate", "70 minutes"),
        ("make _generate", "internal make target"),
    ],
)
def test_persistent_sessions_preserve_other_guardrails(
    command: str, expected: str
) -> None:
    message = pretool_hook.evaluate_hook_input(
        {"tool_name": "exec_command", "tool_input": {"cmd": command}}, _config()
    )
    assert expected in message


def test_persistent_session_is_preserved_through_git_alias_recursion() -> None:
    assert (
        pretool_hook.evaluate_hook_input(
            {
                "tool_name": "exec_command",
                "tool_input": {
                    "cmd": "git -c 'alias.submit=!agent-submit --title T --body B' submit"
                },
            },
            _config(),
        )
        is None
    )


@pytest.mark.parametrize("timeout", [None, 4200000])
def test_unknown_native_cmd_tool_fails_closed(timeout: int | None) -> None:
    data = {
        "tool_name": "unknown_exec",
        "tool_input": {
            "cmd": SUBMIT_COMMAND,
            "yield_time_ms": 1000,
            "timeout_ms": timeout,
        },
    }
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


def test_outer_call_requires_a_harness_call_id(tmp_path: Path) -> None:
    outer = _outer_call("tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})")
    del outer["call_id"]
    data = _transcript_hook(tmp_path, outer)
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


def test_persistent_metadata_does_not_override_input_timeout(tmp_path: Path) -> None:
    data = _transcript_hook(
        tmp_path,
        _outer_call("tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})"),
    )
    data["tool_input"]["timeout_ms"] = 60000
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


@pytest.mark.parametrize(
    "timeout,allowed", [(None, False), (60000, False), (4200000, True)]
)
def test_claude_transcript_preserves_native_timeout(
    tmp_path: Path, timeout: int | None, allowed: bool
) -> None:
    arguments = {"command": SUBMIT_COMMAND}
    if timeout is not None:
        arguments["timeout"] = timeout
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": arguments,
                        }
                    ],
                },
            }
        )
        + "\n"
    )
    data = {
        "tool_name": "Bash",
        "tool_input": arguments,
        "tool_use_id": "toolu_1",
        "transcript_path": str(transcript),
    }
    assert (pretool_hook.evaluate_hook_input(data, _config()) is None) == allowed
    assert not pretool_hook.tool_execution(data).persistent


def test_non_tool_event_cannot_supply_persistent_metadata(tmp_path: Path) -> None:
    data = _transcript_hook(tmp_path)
    Path(data["transcript_path"]).write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": _outer_call(
                    "tools.exec_command({cmd: " + json.dumps(SUBMIT_COMMAND) + "})"
                ),
            }
        )
        + "\n"
    )
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


def test_native_exec_cannot_hide_its_cmd_behind_an_extra_command_field() -> None:
    data = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": SUBMIT_COMMAND, "command": "make test"},
    }
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


@pytest.mark.parametrize("name,field", [("Bash", "timeout"), ("Shell", "timeout_ms")])
@pytest.mark.parametrize(
    "source",
    [
        "const note = `template`; tools.shell_command(args)",
        "const matcher = /value/; tools.shell_command(args)",
        "tools.shell_command(args)",
    ],
)
def test_valid_direct_timeout_survives_unsupported_transcript_syntax(
    tmp_path: Path, name: str, field: str, source: str
) -> None:
    data = _transcript_hook(tmp_path, _outer_call(source))
    data["tool_name"] = name
    data["tool_input"][field] = 4200000
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
    assert pretool_hook.tool_timeout_ms(data) == 4200000
    assert not pretool_hook.tool_execution(data).persistent


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize(
    "wrapper,allowed",
    [
        ("timeout 1", False),
        ("timeout 4199s", False),
        ("timeout 70m", True),
        ("timeout 1.5h", True),
        ("timeout .5h", False),
        ("timeout 1d", True),
        ("timeout 0", True),
        ("timeout unknown", False),
        ("env FOO=bar timeout --signal=KILL 1", False),
        ("timeout -k 10 --signal TERM 1", False),
        ("timeout 2h timeout 1", False),
        ("timeout 1 timeout 2h", False),
        ("timeout 1 timeout 0", False),
    ],
)
def test_submit_respects_shell_timeout_wrappers(
    persistent: bool, wrapper: str, allowed: bool
) -> None:
    command = wrapper + " " + SUBMIT_COMMAND
    data = (
        {"tool_name": "exec_command", "tool_input": {"cmd": command}}
        if persistent
        else {
            "tool_name": "Bash",
            "tool_input": {"command": command, "timeout": 4200000},
        }
    )
    message = pretool_hook.evaluate_hook_input(data, _config())
    assert (message is None) == allowed
    if not allowed:
        assert "70 minutes" in message


@pytest.mark.parametrize(
    "command",
    [
        "timeout 1 sh -c 'agent-submit --title T --body B'",
        "sh -c 'timeout 1 agent-submit --title T --body B'",
        "timeout 1 env -S 'agent-submit --title T --body B'",
        "timeout 1 env '-Sagent-submit --title T --body B'",
        "timeout 1 env --split-string='agent-submit --title T --body B'",
        "timeout 1 git -c 'alias.submit=!agent-submit --title T --body B' submit",
        "git -c 'alias.submit=!timeout 1 agent-submit --title T --body B' submit",
    ],
)
def test_shell_deadline_survives_nested_shells_and_aliases(command: str) -> None:
    data = {"tool_name": "exec_command", "tool_input": {"cmd": command}}
    assert "70 minutes" in pretool_hook.evaluate_hook_input(data, _config())


def test_shell_timeout_is_scoped_to_its_command() -> None:
    data = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "timeout 1 make test; " + SUBMIT_COMMAND},
    }
    assert pretool_hook.evaluate_hook_input(data, _config()) is None


@pytest.mark.parametrize(
    "command",
    [
        "timeout 70m agent-submit --title T --body B",
        "timeout 70m git -c 'alias.submit=!agent-submit --title T --body B' submit",
    ],
)
def test_shell_timeout_does_not_establish_unknown_tool_lifetime(command: str) -> None:
    data = {"tool_name": "Shell", "tool_input": {"command": command}}
    assert pretool_hook.evaluate_hook_input(data, _config()) is not None


@pytest.mark.parametrize(
    "command",
    [
        SUBMIT_COMMAND + " &",
        "nohup " + SUBMIT_COMMAND + " &",
        "sh -c '" + SUBMIT_COMMAND + " &'",
        "sh -c '" + SUBMIT_COMMAND + "' &",
        "env -S '" + SUBMIT_COMMAND + "' &",
        "git -c 'alias.submit=!" + SUBMIT_COMMAND + " &' submit",
        "git -c 'alias.submit=!" + SUBMIT_COMMAND + "' submit &",
        SUBMIT_COMMAND + " && echo done &",
        SUBMIT_COMMAND + " || echo failed &",
        SUBMIT_COMMAND + " | cat &",
        SUBMIT_COMMAND + " & wait $!",
    ],
)
def test_persistent_submit_must_remain_foreground(command: str) -> None:
    data = {"tool_name": "exec_command", "tool_input": {"cmd": command}}
    assert "foreground" in pretool_hook.evaluate_hook_input(data, _config())


@pytest.mark.parametrize(
    "command",
    [
        "make test & " + SUBMIT_COMMAND,
        "make test && echo done & " + SUBMIT_COMMAND,
        SUBMIT_COMMAND + "; echo done &",
        SUBMIT_COMMAND + " --body 'an & in a quoted string'",
    ],
)
def test_background_detection_is_scoped_to_the_shell_list(command: str) -> None:
    data = {"tool_name": "exec_command", "tool_input": {"cmd": command}}
    assert pretool_hook.evaluate_hook_input(data, _config()) is None
