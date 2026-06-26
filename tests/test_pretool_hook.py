"""Tests for the shared PreToolUse hook runner."""

from __future__ import annotations

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


def test_blocks_raw_git_push() -> None:
    message = rejection_message("git push origin HEAD", _config(), dirty_generated_output=False)
    assert message is not None
    assert "agent-submit" in message


def test_blocks_direct_git_push_helper() -> None:
    assert "agent-submit" in (
        rejection_message("git-push origin HEAD", _config(), dirty_generated_output=False)
        or ""
    )
    assert "agent-submit" in (
        rejection_message(
            "/usr/lib/git-core/git-push --force origin HEAD",
            _config(),
            dirty_generated_output=False,
        )
        or ""
    )


def test_blocks_gh_pr_edit_and_gh_issue_with_flags() -> None:
    assert "agent-submit" in (
        rejection_message("gh -R owner/repo pr edit 12 --title T", _config()) or ""
    )
    assert "GitHub Issues" in (
        rejection_message("gh --repo owner/repo issue create --title Bug", _config()) or ""
    )


def test_blocks_kill_by_name_under_wrappers() -> None:
    message = rejection_message("sudo pkill python", _config())
    assert message is not None
    assert "pkill/killall" in message


def test_blocks_branch_switch_without_matching_signoff() -> None:
    message = rejection_message("git switch feature", _config())
    assert message is not None
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in message


def test_allows_branch_switch_with_matching_signoff() -> None:
    assert (
        rejection_message(
            "PROJECT_BRANCH_SWITCH_SIGNOFF=feature git switch feature",
            _config(),
        )
        is None
    )


def test_blocks_generated_output_mutation() -> None:
    message = rejection_message("sed -i s/a/b/ data/generated/file.txt", _config())
    assert message is not None
    assert "generated output" in message


def test_blocks_git_push_when_generated_output_is_dirty() -> None:
    message = rejection_message(
        "git push origin HEAD",
        _config(),
        dirty_generated_output=True,
    )
    assert message is not None
    assert "data/generated" in message


def test_blocks_short_agent_submit_timeout() -> None:
    message = rejection_message("agent-submit --title T --body B", _config(), timeout_ms=60_000)
    assert message is not None
    assert "70 minutes" in message


def test_blocks_make_targets_requiring_long_timeout() -> None:
    message = rejection_message("make generate", _config(), timeout_ms=None)
    assert message is not None
    assert "make generate" in message


def test_blocks_internal_make_target() -> None:
    message = rejection_message("make _generate", _config(), timeout_ms=70 * 60 * 1000)
    assert message is not None
    assert "make generate" in message


def test_blocks_configured_command_family() -> None:
    message = rejection_message("env RUSTFLAGS=-Dwarnings cargo test", _config())
    assert message == "Do not run cargo directly. Use 'make test' instead."


def test_blocks_configured_binary_pattern() -> None:
    message = rejection_message("./target/debug/project-cli --help", _config())
    assert message == "Do not run project-cli directly. Use make test."


def test_recurses_into_shell_c_payload() -> None:
    message = rejection_message("bash -c 'git push origin HEAD'", _config())
    assert message is not None
    assert "agent-submit" in message


def test_blocks_unresolved_shell_c_payload() -> None:
    assert "shell -c with unresolved shell expansions" in (
        rejection_message("cmd='git push origin HEAD'; bash -c \"$cmd\"", _config())
        or ""
    )


def test_blocks_stdin_fed_shells() -> None:
    assert "pipe unresolved stdin into a shell" in (
        rejection_message("printf 'git push origin HEAD\\n' | sh", _config()) or ""
    )
    assert "pipe unresolved stdin into a shell" in (
        rejection_message("sh < <(printf 'git push origin HEAD\\n')", _config()) or ""
    )


def test_recurses_into_shell_here_string_payload() -> None:
    assert "agent-submit" in (
        rejection_message("bash <<< 'git push origin HEAD'", _config()) or ""
    )


def test_blocks_unresolved_policy_relevant_expansions() -> None:
    assert "unresolved shell expansions" in (
        rejection_message("g=git; \"$g\" push origin HEAD", _config()) or ""
    )
    assert "unresolved shell expansions" in (
        rejection_message("sub=push; git \"$sub\" origin HEAD", _config()) or ""
    )
    assert "unresolved shell expansions" in (
        rejection_message("`printf git` push origin HEAD", _config()) or ""
    )
    assert rejection_message("echo \"$HOME\"", _config()) is None


def test_recurses_into_shell_c_option_clusters() -> None:
    assert "agent-submit" in (
        rejection_message("bash -lc 'git push origin HEAD'", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("sh -ec 'pkill python'", _config()) or "")


def test_blocks_previous_branch_shorthand() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=-" in (
        rejection_message("git switch -", _config()) or ""
    )
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=-" in (
        rejection_message("git checkout -", _config()) or ""
    )


def test_inspects_commands_after_shell_control_keywords() -> None:
    assert "agent-submit" in (
        rejection_message("if true; then git push origin HEAD; fi", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("{ git push origin HEAD; }", _config()) or ""
    )
    assert "pkill/killall" in (
        rejection_message("while true; do pkill python; done", _config()) or ""
    )


def test_coproc_is_unwrapped_before_policy_checks() -> None:
    assert "agent-submit" in (
        rejection_message("coproc git push origin HEAD", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("coproc pkill python", _config()) or "")


def test_skips_leading_redirections_before_executable() -> None:
    assert "agent-submit" in (
        rejection_message(">/tmp/out git push origin HEAD", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("2>/tmp/e pkill python", _config()) or "")


def test_exec_a_name_operand_does_not_hide_command() -> None:
    assert "agent-submit" in (
        rejection_message("exec -a spoof git push origin HEAD", _config()) or ""
    )


def test_sudo_operand_options_do_not_hide_command() -> None:
    assert "agent-submit" in (
        rejection_message("sudo -D /tmp git push origin HEAD", _config()) or ""
    )
    assert "pkill/killall" in (
        rejection_message("sudo --chdir /tmp pkill python", _config()) or ""
    )


def test_env_attached_split_string_is_inspected() -> None:
    assert "agent-submit" in (
        rejection_message("env -S'git push origin HEAD'", _config()) or ""
    )
    assert "pkill/killall" in (
        rejection_message("env --split-string='pkill python'", _config()) or ""
    )


def test_env_split_string_keeps_trailing_command() -> None:
    assert "agent-submit" in (
        rejection_message("env -S 'VAR=x' git push origin HEAD", _config()) or ""
    )


def test_inspects_command_and_process_substitutions() -> None:
    assert "agent-submit" in (
        rejection_message("echo $(git push origin HEAD)", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("cat <(pkill python)", _config()) or "")


def test_inspects_legacy_backtick_substitutions() -> None:
    assert "agent-submit" in (
        rejection_message("echo `git push origin HEAD`", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("echo `pkill python`", _config()) or "")


def test_inspects_trap_handlers() -> None:
    assert "agent-submit" in (
        rejection_message("trap 'git push origin HEAD' EXIT", _config()) or ""
    )
    assert "pkill/killall" in (rejection_message("trap 'pkill python' 0", _config()) or "")


def test_eval_with_unresolved_expansion_is_blocked() -> None:
    assert "eval with unresolved shell expansions" in (
        rejection_message("cmd='git push origin HEAD'; eval \"$cmd\"", _config()) or ""
    )


def test_inspects_shell_function_bodies() -> None:
    assert "agent-submit" in (
        rejection_message("f(){ git push origin HEAD; }; f", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("function f { git push origin HEAD; }; f", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("function f() { git push origin HEAD; }; f", _config()) or ""
    )


def test_inspects_case_arm_bodies() -> None:
    assert "agent-submit" in (
        rejection_message("case x in x) git push origin HEAD;; esac", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("case y in x) :;; y) git push origin HEAD;; esac", _config())
        or ""
    )


def test_blocks_shell_alias_expansion() -> None:
    assert "alias expansion" in (
        rejection_message("shopt -s expand_aliases\nalias p='git push origin HEAD'\np", _config())
        or ""
    )


def test_git_switch_guess_does_not_hide_branch_target() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in (
        rejection_message("git switch --guess feature", _config()) or ""
    )


def test_git_detach_requires_signoff() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=HEAD" in (
        rejection_message("git switch --detach", _config()) or ""
    )


def test_git_switch_orphan_requires_signoff() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=newbranch" in (
        rejection_message("git switch --orphan=newbranch", _config()) or ""
    )


def test_attached_branch_create_options_require_signoff() -> None:
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in (
        rejection_message("git switch -cfeature", _config()) or ""
    )
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in (
        rejection_message("git switch --create=feature", _config()) or ""
    )
    assert "PROJECT_BRANCH_SWITCH_SIGNOFF=feature" in (
        rejection_message("git checkout -bfeature", _config()) or ""
    )


def test_checkout_path_restore_does_not_require_branch_signoff() -> None:
    assert rejection_message("git checkout HEAD -- file.txt", _config()) is None


def test_blocks_shell_redirection_into_generated_paths() -> None:
    assert "redirect shell output" in (
        rejection_message("printf x > data/generated/file.txt", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message("printf x 2>data/generated/error.txt", _config()) or ""
    )


def test_blocks_ampersand_write_redirection_into_generated_paths() -> None:
    assert "redirect shell output" in (
        rejection_message("printf x >& data/generated/file.txt", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message(">&/tmp/out git push origin HEAD", _config()) or ""
    )


def test_blocks_redirection_only_writes_into_generated_paths() -> None:
    assert "redirect shell output" in (
        rejection_message("> data/generated/file.txt", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message("exec > data/generated/file.txt", _config()) or ""
    )


def test_blocks_generated_path_ancestor_mutations() -> None:
    assert "generated output" in (rejection_message("rm -rf data", _config()) or "")
    assert "generated output" in (rejection_message("git restore .", _config()) or "")
    assert "generated output" in (rejection_message("git checkout -- .", _config()) or "")


def test_failed_cd_branch_does_not_change_tracked_cwd() -> None:
    assert "generated output" in (
        rejection_message("cd /definitely-missing || rm data/generated/file.txt", _config())
        or ""
    )


def test_blocks_pathless_forced_checkout_as_tree_mutation() -> None:
    assert "generated output" in (rejection_message("git checkout -f", _config()) or "")
    assert "generated output" in (
        rejection_message("git checkout --force", _config()) or ""
    )
    assert rejection_message("git checkout -f -- file.txt", _config()) is None


def test_blocks_worktree_reset_modes_as_tree_mutations() -> None:
    assert "generated output" in (
        rejection_message("git reset --merge HEAD~1", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("git reset --keep HEAD~1", _config()) or ""
    )


def test_blocks_pathless_git_clean_as_tree_mutation() -> None:
    assert "generated output" in (rejection_message("git clean -fdx", _config()) or "")


def test_blocks_generated_path_glob_mutations() -> None:
    assert "generated output" in (rejection_message("rm -rf data/*", _config()) or "")
    assert "generated output" in (
        rejection_message("git checkout -- data/*", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("rm -rf data/*/file.txt", _config()) or ""
    )


def test_generated_path_matching_handles_absolute_paths() -> None:
    generated_file = Path.cwd() / "data/generated/file.txt"
    assert "generated output" in (
        rejection_message(f"rm {generated_file}", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message(f"printf x > {generated_file}", _config()) or ""
    )


def test_generated_path_matching_collapses_dot_segments() -> None:
    assert "generated output" in (
        rejection_message("rm data/../data/generated/file.txt", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message("printf x > data/../data/generated/file.txt", _config()) or ""
    )


def test_generated_path_matching_tracks_cd_segments() -> None:
    assert "generated output" in (
        rejection_message("cd data && rm generated/file.txt", _config()) or ""
    )
    assert "redirect shell output" in (
        rejection_message("cd data; printf x > generated/file.txt", _config()) or ""
    )


def test_substitution_uses_cwd_after_cd() -> None:
    assert "generated output" in (
        rejection_message("cd data && echo $(rm generated/file.txt)", _config()) or ""
    )


def test_subshell_cd_does_not_change_parent_cwd() -> None:
    assert "generated output" in (
        rejection_message("(cd data); rm data/generated/file.txt", _config()) or ""
    )


def test_generated_path_matching_resolves_cd_dash() -> None:
    assert "generated output" in (
        rejection_message(
            "cd data && cd - && rm data/generated/file.txt",
            _config(),
        )
        or ""
    )


def test_generated_path_matching_tracks_env_chdir() -> None:
    assert "generated output" in (
        rejection_message("env -C data rm generated/file.txt", _config()) or ""
    )


def test_generated_path_matching_tracks_sudo_chdir() -> None:
    assert "generated output" in (
        rejection_message("sudo -D data rm generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("sudo --chdir=data rm generated/file.txt", _config()) or ""
    )


def test_generated_path_matching_tracks_env_chdir_with_split_string() -> None:
    assert "generated output" in (
        rejection_message("env -C data -S 'rm generated/file.txt'", _config()) or ""
    )


def test_generated_path_matching_tracks_git_dash_c() -> None:
    assert "generated output" in (
        rejection_message("git -C data restore generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("git --no-pager -C data restore generated/file.txt", _config())
        or ""
    )


def test_inline_git_aliases_are_inspected() -> None:
    assert "agent-submit" in (
        rejection_message("git -c alias.p='push origin HEAD' p", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("git -c alias.p='!git push origin HEAD' p", _config()) or ""
    )
    assert "agent-submit" in (
        rejection_message("git --no-pager -c alias.p='push origin HEAD' p", _config())
        or ""
    )
    assert "agent-submit" in (
        rejection_message("git -p -c alias.p='push origin HEAD' p", _config()) or ""
    )


def test_blocks_git_config_alias_writes() -> None:
    assert "Git aliases" in (
        rejection_message("git config alias.p 'push origin HEAD'; git p", _config()) or ""
    )


def test_git_config_env_option_is_skipped_before_subcommand() -> None:
    assert "agent-submit" in (
        rejection_message("FOO=bar git --config-env foo.bar=FOO push origin HEAD", _config())
        or ""
    )


def test_git_config_env_aliases_are_inspected() -> None:
    assert "agent-submit" in (
        rejection_message(
            "GITALIAS='push origin HEAD' git --config-env=alias.p=GITALIAS p",
            _config(),
        )
        or ""
    )


def test_unresolved_git_config_env_alias_is_blocked() -> None:
    assert "eval with unresolved shell expansions" in (
        rejection_message(
            "export GITALIAS='push origin HEAD'; git --config-env=alias.p=GITALIAS p",
            _config(),
        )
        or ""
    )


def test_git_alias_payload_preserves_cwd() -> None:
    assert "generated output" in (
        rejection_message(
            "cd data && git -c alias.r='restore generated/file.txt' r",
            _config(),
        )
        or ""
    )
    assert "generated output" in (
        rejection_message(
            "git -C data -c alias.r='restore generated/file.txt' r",
            _config(),
        )
        or ""
    )


def test_blocks_git_hard_reset_as_tree_mutation() -> None:
    assert "generated output" in (
        rejection_message("git reset --hard", _config()) or ""
    )


def test_blocks_git_rm_and_mv_generated_paths() -> None:
    assert "generated output" in (
        rejection_message("git rm data/generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("git mv data/generated/file.txt elsewhere", _config()) or ""
    )


def test_blocks_git_top_level_pathspec_generated_paths() -> None:
    assert "generated output" in (
        rejection_message("git restore :/data/generated/file.txt", _config()) or ""
    )


def test_blocks_git_pathspec_from_file_generated_restores() -> None:
    assert "generated output" in (
        rejection_message("git restore --pathspec-from-file=/tmp/paths", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("git checkout --pathspec-from-file /tmp/paths", _config()) or ""
    )


def test_blocks_git_apply_when_generated_paths_are_configured() -> None:
    assert "generated output" in (
        rejection_message(
            "git apply --include=data/generated/file.txt /tmp/patch.diff",
            _config(),
        )
        or ""
    )


def test_blocks_tee_writes_to_generated_paths() -> None:
    assert "generated output" in (
        rejection_message("printf x | tee data/generated/file.txt", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("tee -a data/generated/file.txt", _config()) or ""
    )


def test_blocks_link_writes_to_generated_paths() -> None:
    assert "generated output" in (
        rejection_message("ln -s ../source data/generated/link", _config()) or ""
    )
    assert "generated output" in (
        rejection_message("ln -f source data/generated/file.txt", _config()) or ""
    )


def test_blocks_target_directory_writes_to_generated_paths() -> None:
    assert "generated output" in (
        rejection_message("cp --target-directory=data/generated source.txt", _config())
        or ""
    )
    assert "generated output" in (
        rejection_message("install -t data/generated source.txt", _config()) or ""
    )


def test_allows_copy_and_link_reads_from_generated_paths() -> None:
    assert (
        rejection_message("cp data/generated/file.txt /tmp/file.txt", _config())
        is None
    )
    assert (
        rejection_message("install data/generated/file.txt /tmp/file.txt", _config())
        is None
    )
    assert (
        rejection_message("ln data/generated/file.txt /tmp/link.txt", _config())
        is None
    )


def test_blocks_expanded_redirection_targets() -> None:
    assert "unresolved shell expansion targets" in (
        rejection_message('printf x > "$(printf data/generated/file.txt)"', _config())
        or ""
    )


def test_binary_block_matching_handles_absolute_paths() -> None:
    binary = Path.cwd() / "target/debug/project-cli"
    assert (
        rejection_message(str(binary), _config())
        == "Do not run project-cli directly. Use make test."
    )


def test_binary_block_matching_uses_invocation_cwd() -> None:
    assert (
        rejection_message("cd target/debug && ./project-cli --help", _config())
        == "Do not run project-cli directly. Use make test."
    )


def test_heredoc_literal_body_is_not_parsed_as_command() -> None:
    command = "cat <<'EOF' > /tmp/message\n" "git push origin HEAD\n" "EOF\n"
    assert rejection_message(command, _config()) is None


def test_quoted_heredoc_marker_does_not_hide_following_command() -> None:
    command = "echo '<<EOF'\n" "git push origin HEAD\n"
    assert "agent-submit" in (rejection_message(command, _config()) or "")


def test_unquoted_heredoc_expansions_are_inspected() -> None:
    command = "cat <<EOF > /tmp/message\n" "$(git push origin HEAD)\n" "EOF\n"
    assert "agent-submit" in (rejection_message(command, _config()) or "")


def test_unquoted_heredoc_expansions_ignore_body_quotes() -> None:
    command = "cat <<EOF > /tmp/message\n" "'$(git push origin HEAD)'\n" "EOF\n"
    assert "agent-submit" in (rejection_message(command, _config()) or "")


def test_quoted_heredoc_expansions_are_literal() -> None:
    command = "cat <<'EOF' > /tmp/message\n" "$(git push origin HEAD)\n" "EOF\n"
    assert rejection_message(command, _config()) is None


def test_shell_heredoc_body_is_parsed_as_command() -> None:
    command = "bash <<'EOF'\n" "git push origin HEAD\n" "EOF\n"
    assert "agent-submit" in (rejection_message(command, _config()) or "")


def test_shell_heredoc_after_list_operator_is_parsed_as_command() -> None:
    command = "true && bash <<'EOF'\n" "git push origin HEAD\n" "EOF\n"
    assert "agent-submit" in (rejection_message(command, _config()) or "")


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
        with patch("sys.stdin", new=__import__("io").StringIO(payload)):
            with pytest.raises(SystemExit) as exc:
                agent_pretool_hook.main()
    assert exc.value.code == 2
    assert "agent-submit" in capsys.readouterr().err


def test_cli_allows_non_bash(tmp_path: Path) -> None:
    config = tmp_path / "hook.json5"
    config.write_text("{}\n")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"command": "git push"}})
    with patch.object(sys, "argv", ["agent-pretool-hook", "--config", str(config)]):
        with patch("sys.stdin", new=__import__("io").StringIO(payload)):
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
