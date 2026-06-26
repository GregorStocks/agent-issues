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
                    "arguments": json.dumps({"timeout_ms": 12345}),
                }
            }
        )
        + "\n"
    )
    data = {"transcript_path": str(transcript), "tool_use_id": "call_1", "tool_input": {}}
    assert pretool_hook.tool_timeout_ms(data) == 12345


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
