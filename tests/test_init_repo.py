"""Tests for agent_issues.cli.init_repo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_issues import pretool_hook
from agent_issues.cli import agent_issues, init_repo


def _args(tmp_path: Path, **overrides: bool) -> argparse.Namespace:
    values = {
        "repo_root": str(tmp_path),
        "dry_run": False,
        "all": False,
        "agents": False,
        "claude": False,
        "hook": False,
        "local_skill_notes": False,
        "makefile_snippet": False,
        "ci_snippet": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_default_creates_empty_issues_gitignore(tmp_path: Path) -> None:
    actions = init_repo.run_init(_args(tmp_path))

    assert actions == ["create issues/.gitignore"]
    assert (tmp_path / "issues/.gitignore").read_text() == ""

    actions = init_repo.run_init(_args(tmp_path))
    assert actions == ["skip issues/.gitignore: already present"]


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    actions = init_repo.run_init(_args(tmp_path, dry_run=True, agents=True, claude=True))

    assert actions == [
        "would create issues/.gitignore",
        "would create AGENTS.md",
        "would create CLAUDE.md",
    ]
    assert not (tmp_path / "issues/.gitignore").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_agents_and_claude_guidance_preserve_existing_content(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Project notes.\n")
    (tmp_path / "CLAUDE.md").write_text("Claude notes.\n")

    init_repo.run_init(_args(tmp_path, agents=True, claude=True))
    init_repo.run_init(_args(tmp_path, agents=True, claude=True))

    assert (tmp_path / "AGENTS.md").read_text() == (
        "Project notes.\n\n"
        "Use the `agent-issues` skill for this repository's local issue tracking, "
        "branch, and PR workflow.\n"
    )
    assert (tmp_path / "CLAUDE.md").read_text() == "Claude notes.\n\n@AGENTS.md\n"


def test_hook_scaffolding_merges_claude_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".claude/settings.local.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"type": "command", "command": "custom-write-hook"}],
                        }
                    ]
                }
            }
        )
        + "\n"
    )

    init_repo.run_init(_args(tmp_path, hook=True))
    init_repo.run_init(_args(tmp_path, hook=True))

    hook_config = tmp_path / ".agent-issues/pretool-hook.json5"
    hook_script = tmp_path / ".claude/hooks/agent-issues-pretool-hook.sh"
    assert init_repo.JSON5_GENERATED_BEGIN in hook_config.read_text()
    assert pretool_hook.load_config(hook_config).minimum_agent_submit_timeout_ms == 4200000
    assert init_repo.GENERATED_BEGIN in hook_script.read_text()
    assert os.access(hook_script, os.X_OK)

    data = json.loads(settings.read_text())
    pretool = data["hooks"]["PreToolUse"]
    assert pretool[0]["matcher"] == "Write"
    assert pretool.count(init_repo.HOOK_ENTRY) == 1


def test_generated_files_do_not_overwrite_custom_content(tmp_path: Path) -> None:
    hook_config = tmp_path / ".agent-issues/pretool-hook.json5"
    skill_note = tmp_path / ".agents/skills/solve-issue-local/SKILL.md"
    hook_config.parent.mkdir(parents=True)
    skill_note.parent.mkdir(parents=True)
    hook_config.write_text("{generated_paths: ['custom/generated']}\n")
    skill_note.write_text("Custom local skill notes.\n")

    actions = init_repo.run_init(_args(tmp_path, hook=True, local_skill_notes=True))

    assert "skip .agent-issues/pretool-hook.json5: custom content exists" in actions
    assert "skip .agents/skills/solve-issue-local/SKILL.md: custom content exists" in actions
    assert hook_config.read_text() == "{generated_paths: ['custom/generated']}\n"
    assert skill_note.read_text() == "Custom local skill notes.\n"


def test_generated_files_do_not_overwrite_diverged_generated_content(tmp_path: Path) -> None:
    skill_note = tmp_path / ".agents/skills/solve-issue-local/SKILL.md"
    skill_note.parent.mkdir(parents=True)
    skill_note.write_text(
        init_repo.LOCAL_SKILL_NOTE.replace(
            "- Add this repository's test, lint, and formatting commands here.",
            "- uv run pytest",
        )
    )

    actions = init_repo.run_init(_args(tmp_path, local_skill_notes=True))

    assert "skip .agents/skills/solve-issue-local/SKILL.md: generated content differs" in actions
    assert "- uv run pytest" in skill_note.read_text()


def test_optional_makefile_and_ci_snippets_are_generated(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("test:\n\tpytest\n")

    init_repo.run_init(_args(tmp_path, makefile_snippet=True, ci_snippet=True))

    makefile_text = makefile.read_text()
    assert "test:\n\tpytest\n" in makefile_text
    assert "issue-fmt:" in makefile_text
    ci_snippet = tmp_path / ".agent-issues/snippets/agent-issues-ci.yml"
    assert init_repo.GENERATED_BEGIN in ci_snippet.read_text()
    assert "issue-lint" in ci_snippet.read_text()


def test_makefile_snippet_uses_gnu_make_preferred_file(tmp_path: Path) -> None:
    gnu_makefile = tmp_path / "GNUmakefile"
    gnu_makefile.write_text("test:\n\tpytest\n")

    actions = init_repo.run_init(_args(tmp_path, makefile_snippet=True))

    assert "update GNUmakefile" in actions
    assert "issue-fmt:" in gnu_makefile.read_text()
    assert not (tmp_path / "Makefile").exists()


def test_makefile_snippet_skips_custom_issue_targets(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("issue-fmt:\n\tcustom-format\n")

    actions = init_repo.run_init(_args(tmp_path, makefile_snippet=True))

    assert "skip Makefile: custom issue target exists" in actions
    assert makefile.read_text() == "issue-fmt:\n\tcustom-format\n"


def test_makefile_snippet_skips_combined_custom_issue_targets(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("issue-fmt issue-lint:\n\tcustom-check\n")

    actions = init_repo.run_init(_args(tmp_path, makefile_snippet=True))

    assert "skip Makefile: custom issue target exists" in actions
    assert makefile.read_text() == "issue-fmt issue-lint:\n\tcustom-check\n"


def test_makefile_snippet_ignores_recipe_lines_that_mention_issue_targets(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("help:\n\t@echo issue-fmt: format issues\n")

    actions = init_repo.run_init(_args(tmp_path, makefile_snippet=True))

    assert "update Makefile" in actions
    makefile_text = makefile.read_text()
    assert "@echo issue-fmt: format issues" in makefile_text
    assert "\nissue-fmt:\n\tuv run issue-fmt\n" in makefile_text


def test_makefile_snippet_skips_diverged_generated_block(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(init_repo.MAKEFILE_BLOCK.replace("uv run issue-fmt", "custom-format"))

    actions = init_repo.run_init(_args(tmp_path, makefile_snippet=True))

    assert "skip Makefile: generated block differs" in actions
    assert "custom-format" in makefile.read_text()


def test_umbrella_init_dispatches_to_init_command(tmp_path: Path) -> None:
    agent_issues.main(["init", "--agents", str(tmp_path)])

    assert (tmp_path / "issues/.gitignore").exists()
    assert "agent-issues" in (tmp_path / "AGENTS.md").read_text()
