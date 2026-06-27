"""Tests for the installer shell script."""

from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

from agent_issues.cli import init_repo


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
if [ "$1" = "tool" ] && [ "$2" = "install" ]; then
    exit 0
fi
if [ "$1" = "tool" ] && [ "$2" = "dir" ] && [ "$3" = "--bin" ]; then
    echo "$HOME/.local/bin"
    exit 0
fi
echo "unexpected uv command: $*" >&2
exit 64
"""
    )
    uv.chmod(0o755)


def _run_install(
    repo_root: Path,
    home: Path,
    bin_dir: Path,
    *,
    codex_home: Path | None = None,
) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    else:
        env.pop("CODEX_HOME", None)

    subprocess.run(
        ["bash", str(repo_root / "install.sh")],
        check=True,
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )


def _run_repo_install(
    repo_root: Path,
    target_repo: Path,
    home: Path,
    bin_dir: Path,
    *,
    codex_home: Path | None = None,
) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    else:
        env.pop("CODEX_HOME", None)

    subprocess.run(
        ["bash", str(repo_root / "install-repo.sh"), str(target_repo)],
        check=True,
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )


def test_install_links_agent_issues_skill_without_agent_guidance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    _run_install(repo_root, home, bin_dir)

    assert (home / ".claude/skills/agent-issues").is_symlink()
    assert (home / ".codex/skills/agent-issues").is_symlink()
    assert not (home / ".claude/CLAUDE.md").exists()
    assert not (home / ".codex/AGENTS.md").exists()


def test_install_does_not_rewrite_existing_global_agent_content(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    claude_file = home / ".claude/CLAUDE.md"
    codex_file = home / ".codex/AGENTS.md"
    claude_file.parent.mkdir(parents=True)
    codex_file.parent.mkdir(parents=True)
    claude_file.write_text("Existing Claude instructions.\n")
    codex_file.write_text("Existing Codex instructions.\n")

    _run_install(repo_root, home, bin_dir)
    _run_install(repo_root, home, bin_dir)

    assert claude_file.read_text() == "Existing Claude instructions.\n"
    assert codex_file.read_text() == "Existing Codex instructions.\n"
    assert (home / ".claude/skills/agent-issues").is_symlink()
    assert (home / ".codex/skills/agent-issues").is_symlink()


def test_install_respects_custom_codex_home(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    _run_install(repo_root, home, bin_dir, codex_home=codex_home)

    assert (codex_home / "skills/agent-issues").is_symlink()
    assert not (codex_home / "AGENTS.md").exists()
    assert not (home / ".codex").exists()


def test_repo_install_adds_repo_guidance_and_global_skills(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target_repo = tmp_path / "project"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    target_repo.mkdir()
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    _run_repo_install(repo_root, target_repo, home, bin_dir)

    agent_issues_line = (
        "Use the `agent-issues` skill for this repository's local issue "
        "tracking, branch, and PR workflow."
    )

    assert (target_repo / "AGENTS.md").read_text() == f"{agent_issues_line}\n"
    assert (target_repo / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (target_repo / "issues/.gitignore").read_text() == ""
    hook_config_text = (target_repo / ".agent-issues/pretool-hook.json5").read_text()
    assert init_repo.JSON5_GENERATED_BEGIN in hook_config_text
    hook_script = target_repo / ".claude/hooks/agent-issues-pretool-hook.sh"
    assert hook_script.exists()
    assert os.access(hook_script, os.X_OK)
    hook_text = hook_script.read_text()
    assert "CLAUDE_PROJECT_DIR" in hook_text
    assert "Local pre-tool hook failed" in hook_text
    assert "agent-pretool-hook is not installed" in hook_text
    assert 'uv tool dir --bin' in hook_text
    settings = json.loads((target_repo / ".claude/settings.local.json").read_text())
    assert settings["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": 'cd "${CLAUDE_PROJECT_DIR:-.}" && .claude/hooks/agent-issues-pretool-hook.sh',
                }
            ],
        }
    ]
    assert (home / ".claude/skills/agent-issues").is_symlink()
    assert (home / ".codex/skills/agent-issues").is_symlink()
    assert not (home / ".claude/CLAUDE.md").exists()
    assert not (home / ".codex/AGENTS.md").exists()


def test_repo_install_preserves_existing_repo_guidance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target_repo = tmp_path / "project"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    target_repo.mkdir()
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    agents_file = target_repo / "AGENTS.md"
    claude_file = target_repo / "CLAUDE.md"
    hook_config = target_repo / ".agent-issues/pretool-hook.json5"
    hook_script = target_repo / ".claude/hooks/agent-issues-pretool-hook.sh"
    settings_file = target_repo / ".claude/settings.local.json"
    agents_file.write_text("Project-specific instructions.\n")
    claude_file.write_text("Existing Claude instructions.\n")
    hook_config.parent.mkdir(parents=True)
    hook_script.parent.mkdir(parents=True)
    hook_config.write_text("{generated_paths: ['custom/generated']}\n")
    hook_script.write_text("#!/bin/sh\ncustom hook\n")
    settings_file.write_text(
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

    _run_repo_install(repo_root, target_repo, home, bin_dir)
    _run_repo_install(repo_root, target_repo, home, bin_dir)

    agent_issues_line = (
        "Use the `agent-issues` skill for this repository's local issue "
        "tracking, branch, and PR workflow."
    )

    agents_text = agents_file.read_text()
    claude_text = claude_file.read_text()

    assert agents_text == f"Project-specific instructions.\n\n{agent_issues_line}\n"
    assert claude_text == "Existing Claude instructions.\n\n@AGENTS.md\n"
    assert agents_text.count(agent_issues_line) == 1
    assert claude_text.count("@AGENTS.md") == 1
    assert hook_config.read_text() == "{generated_paths: ['custom/generated']}\n"
    assert hook_script.read_text() == "#!/bin/sh\ncustom hook\n"
    settings = json.loads(settings_file.read_text())
    pretool_hooks = settings["hooks"]["PreToolUse"]
    assert pretool_hooks.count(
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": 'cd "${CLAUDE_PROJECT_DIR:-.}" && .claude/hooks/agent-issues-pretool-hook.sh',
                }
            ],
        }
    ) == 1
    assert pretool_hooks[0]["matcher"] == "Write"
