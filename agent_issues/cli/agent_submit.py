"""Run submit hooks, push HEAD, upsert the PR, and watch CI/review outcomes."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

from agent_issues.cli import issue_watch_pr
from agent_issues.json5_utils import loads_json5

EXIT_PREFLIGHT = 10
ESCAPED_BACKTICK_CODE_SPAN = re.compile(r"\\`[^\n`]+\\`")
SUBMIT_HOOK_CONFIG = Path(".agent-issues/submit-hooks.json5")
SUBMIT_HOOK_PHASES = ("prepare", "after_publish")


@dataclass(frozen=True)
class SubmitHooks:
    root: Path
    config_path: Path | None = None
    prepare: tuple[str, ...] = ()
    after_publish: tuple[str, ...] = ()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def validate_pr_body_markdown(body: str, allow_escaped_backticks: bool = False) -> int:
    """Reject likely shell-escaped inline-code spans unless explicitly allowed."""
    if allow_escaped_backticks or not ESCAPED_BACKTICK_CODE_SPAN.search(body):
        return 0

    print(
        "agent-submit: refusing to push — PR body contains escaped inline-code markers. "
        "Pass Markdown code spans unescaped, usually with a single-quoted heredoc. "
        "If the escaped backticks are intentional literal text, rerun with "
        "--allow-escaped-backticks.",
        flush=True,
    )
    return EXIT_PREFLIGHT


def find_submit_hook_config(start: Path | None = None) -> Path | None:
    """Find the nearest repo-local submit hook config from start or cwd."""
    directory = (start or Path.cwd()).resolve()
    if directory.is_file():
        directory = directory.parent

    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / SUBMIT_HOOK_CONFIG
        if candidate.exists():
            return candidate
        if (candidate_dir / ".git").exists():
            break
    return None


def _load_hook_command_list(data: object, key: str, path: Path) -> tuple[str, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise SystemExit(f"{path}: {key} must be a list of shell command strings")

    commands: list[str] = []
    for index, item in enumerate(data):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(
                f"{path}: {key}[{index}] must be a non-empty shell command string"
            )
        commands.append(item)
    return tuple(commands)


def load_submit_hooks(start: Path | None = None) -> SubmitHooks:
    path = find_submit_hook_config(start)
    if path is None:
        return SubmitHooks(root=(start or Path.cwd()).resolve())

    data = loads_json5(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON5 object")

    unknown_keys = sorted(set(data) - set(SUBMIT_HOOK_PHASES))
    if unknown_keys:
        keys = ", ".join(unknown_keys)
        raise SystemExit(f"{path}: unknown submit hook key(s): {keys}")

    return SubmitHooks(
        root=path.parent.parent,
        config_path=path,
        prepare=_load_hook_command_list(data.get("prepare"), "prepare", path),
        after_publish=_load_hook_command_list(
            data.get("after_publish"), "after_publish", path
        ),
    )


def _head_sha() -> str:
    result = _run(["git", "rev-parse", "HEAD"])
    assert result.returncode == 0, f"git rev-parse HEAD failed: {result.stderr}"
    sha = result.stdout.strip()
    assert sha, "Expected non-empty HEAD SHA"
    return sha


def _ensure_clean_worktree(reason: str) -> int:
    status = _run(["git", "status", "--porcelain"])
    assert status.returncode == 0, f"git status failed: {status.stderr}"
    if not status.stdout.strip():
        return 0

    print(
        f"agent-submit: refusing to continue — {reason} left uncommitted changes. "
        "Commit intentional changes or fix the hook before retrying.",
        flush=True,
    )
    print(status.stdout, end="", flush=True)
    return EXIT_PREFLIGHT


def _ensure_branch_unchanged(expected: str, phase: str) -> int:
    actual = _current_branch()
    if actual == expected:
        return 0
    print(
        f"agent-submit: refusing to continue — {phase} hook changed branches "
        f"from {expected} to {actual}. Switch back and retry.",
        flush=True,
    )
    return EXIT_PREFLIGHT


def _ensure_head_unchanged(expected: str, phase: str) -> int:
    actual = _head_sha()
    if actual == expected:
        return 0
    print(
        f"agent-submit: refusing to continue — {phase} hook changed HEAD after "
        "the branch was pushed. Commit those changes before retrying.",
        flush=True,
    )
    return EXIT_PREFLIGHT


def run_submit_hooks(
    hooks: SubmitHooks,
    phase: str,
    *,
    branch: str,
    base: str,
    sha: str,
    pr_number: str | None = None,
) -> int:
    commands = getattr(hooks, phase)
    if not commands:
        return 0

    env = os.environ.copy()
    env.update(
        {
            "AGENT_SUBMIT_PHASE": phase,
            "AGENT_SUBMIT_REPO_ROOT": str(hooks.root),
            "AGENT_SUBMIT_BRANCH": branch,
            "AGENT_SUBMIT_BASE": base,
            "AGENT_SUBMIT_SHA": sha,
        }
    )
    if pr_number is not None:
        env["AGENT_SUBMIT_PR_NUMBER"] = pr_number

    config = f" from {hooks.config_path}" if hooks.config_path is not None else ""
    for command in commands:
        print(f"agent-submit: running {phase} hook{config}: {command}", flush=True)
        result = subprocess.run(command, cwd=hooks.root, env=env, shell=True)
        if result.returncode != 0:
            print(
                f"agent-submit: {phase} hook failed with exit code "
                f"{result.returncode}: {command}",
                flush=True,
            )
            return EXIT_PREFLIGHT
    return 0


def _default_branch() -> str:
    # Duplicates common.default_branch() — kept local so every subprocess call in
    # this module flows through _run, which is the single test seam for mocking.
    result = _run(
        ["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"]
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "main"


def preflight() -> int:
    """Run all preflight checks. Returns 0 if clean, EXIT_PREFLIGHT otherwise."""
    inside = _run(["git", "rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print("agent-submit: not in a git repository.", flush=True)
        return EXIT_PREFLIGHT

    branch_result = _run(["git", "branch", "--show-current"])
    assert branch_result.returncode == 0, f"git branch --show-current failed: {branch_result.stderr}"
    branch = branch_result.stdout.strip()
    assert branch, "Expected non-empty current branch"

    default = _default_branch()
    if branch == default:
        print(
            f"agent-submit: refusing to push — HEAD is on the default branch ({default}). "
            "Create a feature branch first.",
            flush=True,
        )
        return EXIT_PREFLIGHT

    status = _run(["git", "status", "--porcelain"])
    assert status.returncode == 0, f"git status failed: {status.stderr}"
    if status.stdout.strip():
        print(
            "agent-submit: refusing to push — uncommitted changes in working tree. "
            "Commit or stash them first.",
            flush=True,
        )
        return EXIT_PREFLIGHT

    return 0


def upsert_pr(branch: str, base: str, title: str, body: str, draft: bool) -> str:
    """Create a PR if none exists on this branch, else edit the existing one.

    Returns the PR number as a string. Prints the PR URL.
    """
    list_result = _run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"]
    )
    assert list_result.returncode == 0, f"gh pr list failed: {list_result.stderr}"
    prs = json.loads(list_result.stdout) if list_result.stdout.strip() else []
    assert isinstance(prs, list), f"gh pr list returned non-list: {type(prs).__name__}"

    if len(prs) > 1:
        print(
            f"agent-submit: branch {branch} has more than one open PR ({len(prs)} found). "
            "Close the extras and retry.",
            flush=True,
        )
        sys.exit(EXIT_PREFLIGHT)

    if not prs:
        create_cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
        if draft:
            create_cmd.append("--draft")
        create_result = _run(create_cmd)
        assert create_result.returncode == 0, f"gh pr create failed: {create_result.stderr}"
        url = create_result.stdout.strip()
        print(url, flush=True)
        pr_number = url.rsplit("/", 1)[-1]
        assert pr_number.isdigit(), f"could not parse PR number from gh pr create output: {url!r}"
        return pr_number

    pr_number = str(prs[0]["number"])
    edit_result = _run(["gh", "pr", "edit", pr_number, "--title", title, "--body", body])
    assert edit_result.returncode == 0, f"gh pr edit failed: {edit_result.stderr}"
    view_result = _run(["gh", "pr", "view", pr_number, "--json", "url", "--jq", ".url"])
    assert view_result.returncode == 0, f"gh pr view failed: {view_result.stderr}"
    print(view_result.stdout.strip(), flush=True)
    return pr_number


def _current_branch() -> str:
    result = _run(["git", "branch", "--show-current"])
    assert result.returncode == 0, f"git branch --show-current failed: {result.stderr}"
    branch = result.stdout.strip()
    assert branch, "Expected non-empty current branch"
    return branch


def _push(force: bool = False) -> int:
    """Push HEAD to origin. Returns git's exit code.

    When force=True, uses --force-with-lease. Safe here because preflight
    refuses to run on the default branch, so force only ever targets an
    agent-owned feature branch, and --force-with-lease still rejects the push
    if someone else updated the remote since we last fetched.
    """
    cmd = ["git", "push"]
    if force:
        cmd.append("--force-with-lease")
    cmd += ["origin", "HEAD"]
    return subprocess.run(cmd).returncode


def _print_next_step(code: int) -> None:
    if code == 0:
        return
    if code == 1:
        print(
            "\nNEXT STEP: CI failed or merge conflict. Investigate with `gh run view <run-id> "
            "--log-failed`, fix, then re-run `agent-submit`.",
            flush=True,
        )
    elif code == 2:
        print(
            "\nNEXT STEP: Review feedback received. Address the comments, then re-run `agent-submit`.",
            flush=True,
        )
    elif code == 4:
        print(
            "\nNEXT STEP: agent-submit timed out while waiting for CI/review confirmation. "
            "Stop and consult the user before continuing. If the user asks you "
            "to continue later, first inspect and address any PR feedback that "
            "arrived while stopped, then re-run agent-submit rather than replacing "
            "it with manual PR watching via gh or connector tools.",
            flush=True,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run repo submit hooks, push HEAD, create or update the PR, "
            "and run the CI watcher."
        ),
    )
    parser.add_argument("--title", required=True, help="PR title")
    parser.add_argument("--body", required=True, help="PR body")
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create PR as draft (ignored on update).",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Base branch for new PRs (default: repo's default branch).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force-push with lease (after rebase or amend). Preflight still blocks the default branch.",
    )
    parser.add_argument(
        "--allow-escaped-backticks",
        action="store_true",
        help="Allow literal escaped backticks in the PR body.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    code = validate_pr_body_markdown(
        args.body, allow_escaped_backticks=args.allow_escaped_backticks
    )
    if code != 0:
        sys.exit(code)

    code = preflight()
    if code != 0:
        sys.exit(code)

    hooks = load_submit_hooks()
    branch = _current_branch()
    base = args.base if args.base is not None else _default_branch()

    if hooks.prepare:
        code = run_submit_hooks(
            hooks,
            "prepare",
            branch=branch,
            base=base,
            sha=_head_sha(),
        )
        if code != 0:
            sys.exit(code)
        code = _ensure_branch_unchanged(branch, "prepare")
        if code != 0:
            sys.exit(code)
        code = _ensure_clean_worktree("prepare hook")
        if code != 0:
            sys.exit(code)

    push_code = _push(force=args.force)
    if push_code != 0:
        sys.exit(push_code)

    published_sha = _head_sha() if hooks.after_publish else ""
    pr_number = upsert_pr(
        branch=branch, base=base, title=args.title, body=args.body, draft=args.draft
    )

    if hooks.after_publish:
        code = run_submit_hooks(
            hooks,
            "after_publish",
            branch=branch,
            base=base,
            sha=published_sha,
            pr_number=pr_number,
        )
        if code != 0:
            sys.exit(code)
        code = _ensure_branch_unchanged(branch, "after_publish")
        if code != 0:
            sys.exit(code)
        code = _ensure_head_unchanged(published_sha, "after_publish")
        if code != 0:
            sys.exit(code)
        code = _ensure_clean_worktree("after-publish hook")
        if code != 0:
            sys.exit(code)

    watcher_code = issue_watch_pr.run(pr=pr_number)
    _print_next_step(watcher_code)
    sys.exit(watcher_code)
