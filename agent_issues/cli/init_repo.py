"""Bootstrap minimal agent-issues scaffolding in a consumer repository."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


AGENT_ISSUES_LINE = (
    "Use the `agent-issues` skill for this repository's local issue tracking, "
    "branch, and PR workflow."
)
CLAUDE_AGENTS_INCLUDE = "@AGENTS.md"
GENERATED_LABEL = "agent-issues generated"
GENERATED_BEGIN = "# BEGIN agent-issues generated"
GENERATED_END = "# END agent-issues generated"
JSON5_GENERATED_BEGIN = "// BEGIN agent-issues generated"
JSON5_GENERATED_END = "// END agent-issues generated"
MAKEFILE_CANDIDATES = ("GNUmakefile", "makefile", "Makefile")

HOOK_ENTRY = {
    "matcher": "Bash",
    "hooks": [
        {
            "type": "command",
            "command": 'cd "${CLAUDE_PROJECT_DIR:-.}" && .claude/hooks/agent-issues-pretool-hook.sh',
        }
    ],
}

HOOK_CONFIG = f"""{JSON5_GENERATED_BEGIN}
{{
  // Repo-specific settings for the shared agent-issues PreToolUse hook. This
  // hook is a convention guardrail for good-faith agents, not a security
  // sandbox.
  branch_switch_signoff_env: "AGENT_BRANCH_SWITCH_SIGNOFF",
  generated_paths: [],
  generated_command: "the generator target",
  github_issue_guidance: "local JSON5 issue files in issues/",
  command_family_blocks: [],
  binary_blocks: [],
  internal_make_targets: {{}},
  make_targets_requiring_timeout_ms: {{}},
  minimum_agent_submit_timeout_ms: 4200000,
}}
{JSON5_GENERATED_END}
"""

SUBMIT_HOOK_CONFIG = f"""{JSON5_GENERATED_BEGIN}
{{
  // Commands run by agent-submit after basic clean-tree preflight and before
  // pushing. Use prepare for repo-owned validation, regeneration, and committing
  // intentional generated outputs.
  prepare: [],

  // Commands run after the branch is pushed and the PR is created or updated,
  // before the CI/review watcher starts. Use after_publish for statuses or
  // signoff steps that need the final pushed SHA.
  after_publish: [],
}}
{JSON5_GENERATED_END}
"""

HOOK_SCRIPT = f"""#!/bin/sh
{GENERATED_BEGIN}
set -eu

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat > "$tmp"

project_dir="${{CLAUDE_PROJECT_DIR:-$(pwd)}}"
cd "$project_dir"

local_hook="$project_dir/.claude/hooks/pretool-local.sh"
if [ -x "$local_hook" ]; then
    set +e
    "$local_hook" < "$tmp"
    local_status="$?"
    set -e
    if [ "$local_status" -ne 0 ]; then
        echo "Local pre-tool hook failed with status $local_status; blocking command." >&2
        exit 2
    fi
fi

hook_bin="$(command -v agent-pretool-hook || true)"
if [ -z "$hook_bin" ]; then
    uv_bin_dir="$(uv tool dir --bin 2>/dev/null || true)"
    hook_bin="$uv_bin_dir/agent-pretool-hook"
fi
if [ ! -x "$hook_bin" ]; then
    echo "agent-pretool-hook is not installed or not executable; blocking command." >&2
    exit 2
fi

"$hook_bin" --config "$project_dir/.agent-issues/pretool-hook.json5" < "$tmp"
{GENERATED_END}
"""

LOCAL_SKILL_NOTE = f"""---
name: solve-issue-local
description: Repo-specific notes for the global solve-issue workflow.
---

{GENERATED_BEGIN}
# Local Solve-Issue Notes

Keep this file narrow. List only repo-specific validation commands,
generated-artifact rules, dependency policy, and review steps that are not
already covered by the global `solve-issue` and `submit-pr` skills.

## Validation

- Add this repository's test, lint, and formatting commands here.
- If `.agent-issues/submit-hooks.json5` owns validation or generated artifact
  commits, say that agents should rely on `agent-submit` for those steps.
{GENERATED_END}
"""

CREATE_PR_LOCAL_SKILL_NOTE = f"""---
name: create-pr-local
description: Repo-specific notes for the global create-pr workflow.
---

{GENERATED_BEGIN}
# Local Create-PR Notes

Keep this file narrow. List only repo-specific validation commands,
generated-artifact rules, dependency policy, pre-PR checks, and project
constraints that are not already covered by the global `create-pr` and
`submit-pr` skills.

## Validation

- Add this repository's test, lint, and formatting commands here.
- If `.agent-issues/submit-hooks.json5` owns validation or generated artifact
  commits, say that agents should rely on `agent-submit` for those steps.
{GENERATED_END}
"""

MAKEFILE_BLOCK = f"""{GENERATED_BEGIN}
.PHONY: issue-fmt issue-lint

issue-fmt:
\tissue-fmt

issue-lint:
\tissue-lint
{GENERATED_END}
"""

CI_SNIPPET = f"""{GENERATED_BEGIN}
name: agent-issues

on:
  pull_request:

jobs:
  issue-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install agent-issues
      - run: issue-lint
{GENERATED_END}
"""


class Bootstrapper:
    def __init__(self, root: Path, *, dry_run: bool = False) -> None:
        self.root = root
        self.dry_run = dry_run
        self.actions: list[str] = []

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def _record(self, action: str, path: Path, detail: str = "") -> None:
        suffix = f": {detail}" if detail else ""
        self.actions.append(f"{action} {self._relative(path)}{suffix}")

    def ensure_empty_file(self, path: Path) -> None:
        if path.exists():
            if path.read_text() == "":
                self._record("skip", path, "already present")
            else:
                self._record("skip", path, "custom content exists")
            return
        self._record("would create" if self.dry_run else "create", path)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def ensure_generated_file(self, path: Path, content: str, *, mode: int | None = None) -> None:
        if path.exists():
            current = path.read_text()
            if current == content:
                self._record("skip", path, "already up to date")
                if mode is not None and not self.dry_run:
                    path.chmod(mode)
                return
            detail = (
                "generated content differs"
                if GENERATED_LABEL in current
                else "custom content exists"
            )
            self._record("skip", path, detail)
            return
        else:
            action = "would create" if self.dry_run else "create"

        self._record(action, path)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            if mode is not None:
                path.chmod(mode)

    def ensure_line(self, path: Path, line: str) -> None:
        if path.exists():
            current = path.read_text()
            if line in current.splitlines():
                self._record("skip", path, "line already present")
                return
            prefix = "\n" if current and not current.endswith("\n\n") else ""
            content = f"{current}{prefix}{line}\n"
            action = "would update" if self.dry_run else "update"
        else:
            content = f"{line}\n"
            action = "would create" if self.dry_run else "create"

        self._record(action, path)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def ensure_makefile_block(self) -> None:
        path = preferred_makefile_path(self.root)
        if path.exists():
            current = path.read_text()
            if MAKEFILE_BLOCK.strip() in current:
                self._record("skip", path, "block already present")
                return
            if GENERATED_BEGIN in current and GENERATED_END in current:
                before, rest = current.split(GENERATED_BEGIN, 1)
                block, after = rest.split(GENERATED_END, 1)
                existing_block = f"{GENERATED_BEGIN}{block}{GENERATED_END}"
                if existing_block.strip() != MAKEFILE_BLOCK.strip():
                    self._record("skip", path, "generated block differs")
                    return
                content = f"{before}{MAKEFILE_BLOCK.rstrip()}{after}"
            else:
                self._record("skip", path, "custom content exists")
                return
            action = "would update" if self.dry_run else "update"
        else:
            content = MAKEFILE_BLOCK
            action = "would create" if self.dry_run else "create"

        self._record(action, path)
        if not self.dry_run:
            path.write_text(content)

    def ensure_claude_settings_hook(self) -> None:
        path = self.root / ".claude/settings.local.json"
        if path.exists() and path.read_text().strip():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                raise SystemExit(f"{self._relative(path)}: invalid JSON")
            if not isinstance(data, dict):
                raise SystemExit(f"{self._relative(path)}: expected JSON object")
        else:
            data = {}

        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise SystemExit(f"{self._relative(path)}: expected hooks object")
        pretool = hooks.setdefault("PreToolUse", [])
        if not isinstance(pretool, list):
            raise SystemExit(f"{self._relative(path)}: expected PreToolUse list")
        if HOOK_ENTRY in pretool:
            self._record("skip", path, "hook already present")
            return
        pretool.append(HOOK_ENTRY)
        if self.dry_run:
            action = "would update" if path.exists() else "would create"
        else:
            action = "update" if path.exists() else "create"
        self._record(action, path)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2) + "\n")


def preferred_makefile_path(root: Path) -> Path:
    existing_names = {path.name for path in root.iterdir()}
    for name in MAKEFILE_CANDIDATES:
        if name in existing_names:
            return root / name
    return root / "Makefile"


def run_init(args: argparse.Namespace) -> list[str]:
    root = Path(args.repo_root).resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"repository root does not exist: {root}")

    bootstrapper = Bootstrapper(root, dry_run=args.dry_run)
    bootstrapper.ensure_empty_file(root / "issues/.gitignore")

    if args.all or args.agents:
        bootstrapper.ensure_line(root / "AGENTS.md", AGENT_ISSUES_LINE)
    if args.all or args.claude:
        bootstrapper.ensure_line(root / "CLAUDE.md", CLAUDE_AGENTS_INCLUDE)
    if args.all or args.hook:
        bootstrapper.ensure_generated_file(root / ".agent-issues/pretool-hook.json5", HOOK_CONFIG)
        bootstrapper.ensure_generated_file(
            root / ".claude/hooks/agent-issues-pretool-hook.sh",
            HOOK_SCRIPT,
            mode=0o755,
        )
        bootstrapper.ensure_claude_settings_hook()
    if args.all or args.submit_hooks:
        bootstrapper.ensure_generated_file(
            root / ".agent-issues/submit-hooks.json5",
            SUBMIT_HOOK_CONFIG,
        )
    if args.all or args.local_skill_notes:
        bootstrapper.ensure_generated_file(
            root / ".agents/skills/solve-issue-local/SKILL.md",
            LOCAL_SKILL_NOTE,
        )
        bootstrapper.ensure_generated_file(
            root / ".agents/skills/create-pr-local/SKILL.md",
            CREATE_PR_LOCAL_SKILL_NOTE,
        )
        bootstrapper.ensure_generated_file(
            root / ".claude/skills/solve-issue-local/SKILL.md",
            LOCAL_SKILL_NOTE,
        )
        bootstrapper.ensure_generated_file(
            root / ".claude/skills/create-pr-local/SKILL.md",
            CREATE_PR_LOCAL_SKILL_NOTE,
        )
    if args.all or args.makefile_snippet:
        bootstrapper.ensure_makefile_block()
    if args.all or args.ci_snippet:
        bootstrapper.ensure_generated_file(
            root / ".agent-issues/snippets/agent-issues-ci.yml",
            CI_SNIPPET,
        )

    return bootstrapper.actions


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap minimal agent-issues scaffolding in a repository.",
    )
    parser.add_argument("repo_root", nargs="?", default=".", help="repository root")
    parser.add_argument("--dry-run", action="store_true", help="print actions without writing")
    parser.add_argument("--all", action="store_true", help="enable all optional scaffolding")
    parser.add_argument("--agents", action="store_true", help="add AGENTS.md guidance")
    parser.add_argument("--claude", action="store_true", help="add CLAUDE.md AGENTS include")
    parser.add_argument("--hook", action="store_true", help="add shared PreToolUse hook files")
    parser.add_argument(
        "--submit-hooks",
        action="store_true",
        help="add an agent-submit hook config template",
    )
    parser.add_argument(
        "--local-skill-notes",
        action="store_true",
        help="add local solve-issue note templates for Codex and Claude",
    )
    parser.add_argument(
        "--makefile-snippet",
        action="store_true",
        help="add issue-fmt and issue-lint Makefile targets",
    )
    parser.add_argument(
        "--ci-snippet",
        action="store_true",
        help="add an inactive CI workflow snippet under .agent-issues/snippets",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    for action in run_init(args):
        print(action)


if __name__ == "__main__":
    main()
