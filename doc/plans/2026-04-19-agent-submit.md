# `agent-submit` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `issue-finalize-pr` with `agent-submit` — a single CLI that pushes HEAD, creates or updates the PR with the given title/body, and runs the CI watcher, relaying its exit code.

**Architecture:** New CLI module `agent_issues/cli/agent_submit.py`. Extract `run(pr) -> int` from `issue_watch_pr.main()` so `agent-submit` can call the watcher in-process with the PR number it just created/edited. Drop `issue_finalize_pr.py`. Update in-repo skills and docs to point at the new command.

**Tech Stack:** Python 3.11+, pytest (stdlib `unittest.mock`), `gh` CLI, `git`. Matches existing `agent-issues` patterns — no new dependencies.

**Spec:** `doc/specs/2026-04-19-agent-submit-design.md`

---

## File Structure

**Create:**
- `agent_issues/cli/agent_submit.py` — new CLI entry point. Owns: arg parsing, preflight guards, push step, PR create/update dispatch, watcher invocation, NEXT STEP footer, exit-code mapping.
- `tests/test_agent_submit.py` — unit tests for the new CLI.

**Modify:**
- `agent_issues/cli/issue_watch_pr.py` — extract `run(pr: str | None) -> int` from `main()`. `main()` becomes a thin `sys.exit(run(...))` wrapper. No behavior change.
- `pyproject.toml` — remove `issue-finalize-pr` entry; add `agent-submit = "agent_issues.cli.agent_submit:main"`.
- `skills/create-pr/SKILL.md` — collapse steps 8–10 into a single `agent-submit` step. Add exit-code table and "exit 4 is terminal" note.
- `skills/solve-issue/SKILL.md` — swap `issue-finalize-pr` references at lines ~108, ~129, ~138. Drop the claim-store note (`agent-submit` doesn't read it).
- `doc/issues.md` — swap the `issue-finalize-pr` example at line 86.

**Delete:**
- `agent_issues/cli/issue_finalize_pr.py` — replaced.

---

## Task 1: Extract `run()` from `issue_watch_pr.main()`

Refactor-only. Existing tests still exercise `main()` and should keep passing; this task makes the watcher loop callable in-process from `agent-submit`.

**Files:**
- Modify: `agent_issues/cli/issue_watch_pr.py` (the `main` function, lines 168-275)

- [ ] **Step 1: Read the existing `main()` body and confirm the refactor is mechanical**

Run: `cat agent_issues/cli/issue_watch_pr.py | head -200 | tail -40`
Expected: see `def main()` starting at line 168. All `sys.exit(N)` calls inside are the only exit points.

- [ ] **Step 2: Rewrite `main()` — extract a `run(pr)` function that returns an exit code**

Replace the entire `def main() -> None: ...` block (from `def main()` through end of file) with:

```python
def run(pr: str | None = None) -> int:
    """Watch a PR. Returns an exit code rather than calling sys.exit.

    Exit codes:
        0 - clean (merged, CI pass + codex approved, or CI pass without codex)
        1 - CI failed or merge conflict
        2 - review feedback present
        4 - timed out
    """
    pr = pr if pr is not None else get_pr_number()
    nwo = get_repo_nwo()
    print(f"Watching PR #{pr}...", flush=True)

    state = get_pr_lifecycle_state(pr)
    if state == "merged":
        print("\nPR has been merged.", flush=True)
        return 0
    if state == "closed":
        print("\nPR was closed without merging.", flush=True)
        return 1

    baseline_feedback = {f["formatted"] for f in get_review_feedback(pr, nwo)}

    start = time.monotonic()
    eyes_seen = False

    while True:
        state = get_pr_lifecycle_state(pr)
        if state == "merged":
            print("\nPR has been merged.", flush=True)
            return 0
        if state == "closed":
            print("\nPR was closed without merging.", flush=True)
            return 1

        if check_merge_conflict(pr):
            print(
                "\nPR has a merge conflict with the base branch. Merge or rebase to resolve.",
                flush=True,
            )
            return 1

        checks = get_checks(pr)
        elapsed = time.monotonic() - start

        if elapsed > TIMEOUT:
            pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
            print(
                f"\nTimed out after {TIMEOUT}s. Still pending: {', '.join(pending)}",
                flush=True,
            )
            return 4

        failed = [
            c for c in checks if c.get("bucket") not in _PASS_BUCKETS | {"pending", None}
        ]
        if failed:
            _print_failed(failed)
            return 1

        reactions = get_pr_reactions(pr, nwo)
        if has_reaction(reactions, "eyes"):
            eyes_seen = True

        all_feedback = get_review_feedback(pr, nwo)
        new_feedback = [f for f in all_feedback if f["formatted"] not in baseline_feedback]

        if new_feedback:
            oldest = min(f["created_at"] for f in new_feedback)
            age = (datetime.now(timezone.utc) - oldest).total_seconds()
            if age >= COMMENT_GRACE:
                _print_feedback(new_feedback)
                return 2

        if elapsed >= NO_EYES_TIMEOUT and not eyes_seen:
            mins = NO_EYES_TIMEOUT // 60
            pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
            if pending:
                print(
                    f"\nNo codex review after {mins} min. "
                    f"CI still has {len(pending)} pending check(s): {', '.join(pending)}",
                    flush=True,
                )
            elif not checks:
                print(f"\nNo codex review after {mins} min. No CI checks detected.", flush=True)
            else:
                passed = [c for c in checks if c.get("bucket") == "pass"]
                skipped = [c for c in checks if c.get("bucket") == "skipping"]
                print(
                    f"\nNo codex review after {mins} min. "
                    f"All checks passed ({len(passed)} passed, {len(skipped)} skipped). "
                    f"No review feedback.",
                    flush=True,
                )
            return 0

        all_checks_pass = bool(checks) and all(
            c.get("bucket") in _PASS_BUCKETS for c in checks
        )
        if all_checks_pass and has_reaction(reactions, "+1"):
            passed = [c for c in checks if c.get("bucket") == "pass"]
            skipped = [c for c in checks if c.get("bucket") == "skipping"]
            print(
                f"\nAll checks passed ({len(passed)} passed, {len(skipped)} skipped). "
                f"Codex approved (thumbs up). No review feedback.",
                flush=True,
            )
            return 0

        pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
        mins = int(elapsed // 60)
        if pending:
            print(
                f"  [{mins}m] {len(pending)} check(s) pending: {', '.join(pending[:5])}",
                flush=True,
            )
        elif not checks:
            print(f"  [{mins}m] Waiting for checks to start...", flush=True)
        elif eyes_seen:
            print(f"  [{mins}m] CI done; codex reviewing...", flush=True)
        else:
            print(f"  [{mins}m] CI done; waiting for codex...", flush=True)

        time.sleep(POLL_INTERVAL)


def main() -> None:
    pr = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(pr))
```

Key change vs. original: the two `exit_if_pr_finished(pr)` calls are inlined as direct state checks that `return` an exit code. The `exit_if_pr_finished` helper is no longer called and can stay in the module (other callers could use it) — but we should remove it too since it's unused. Delete the `exit_if_pr_finished` function (lines 45-53 in the original).

- [ ] **Step 3: Run the existing watcher tests — they must still pass unchanged**

Run: `uv run pytest tests/test_watch_pr.py -v`
Expected: all 9 tests PASS. They call `main()` which now wraps `run()`, so `SystemExit` is still raised with the same exit codes.

- [ ] **Step 4: Commit**

```bash
git add agent_issues/cli/issue_watch_pr.py
git commit -m "Extract run() from issue_watch_pr.main() for in-process reuse"
```

---

## Task 2: Arg parsing skeleton for `agent_submit`

Build the CLI entry point with just arg parsing first. No side effects yet.

**Files:**
- Create: `agent_issues/cli/agent_submit.py`
- Create: `tests/test_agent_submit.py`

- [ ] **Step 1: Write the failing test for arg parsing**

Create `tests/test_agent_submit.py` with:

```python
"""Tests for agent_issues.cli.agent_submit."""

import sys
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues.cli import agent_submit


def _result(stdout: str = "", returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_requires_title_and_body() -> None:
    with patch.object(sys, "argv", ["agent-submit"]), pytest.raises(SystemExit):
        agent_submit.main()


def test_parses_title_and_body() -> None:
    args = agent_submit.parse_args(["--title", "T", "--body", "B"])
    assert args.title == "T"
    assert args.body == "B"
    assert args.draft is False
    assert args.base is None


def test_parses_optional_flags() -> None:
    args = agent_submit.parse_args(
        ["--title", "T", "--body", "B", "--draft", "--base", "develop"]
    )
    assert args.draft is True
    assert args.base == "develop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_issues.cli.agent_submit'`.

- [ ] **Step 3: Create the minimal `agent_submit.py` to pass**

```python
"""Push HEAD, create or update the PR, and watch for CI+review outcomes."""

import argparse
from typing import Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push HEAD, create or update the PR, and run the CI watcher.",
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
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    raise NotImplementedError(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_issues/cli/agent_submit.py tests/test_agent_submit.py
git commit -m "Add agent_submit CLI skeleton with arg parsing"
```

---

## Task 3: Preflight guards

Refuse to run when the working context is wrong. Exit code 10 for preflight violations.

**Files:**
- Modify: `agent_issues/cli/agent_submit.py`
- Modify: `tests/test_agent_submit.py`

- [ ] **Step 1: Write failing tests for each guard**

Append to `tests/test_agent_submit.py`:

```python
def test_preflight_fails_when_not_in_git_repo(capsys) -> None:
    with patch.object(agent_submit, "_run", return_value=_result(returncode=128, stderr="not a git repo")):
        code = agent_submit.preflight()
    assert code == 10
    assert "not in a git repository" in capsys.readouterr().out.lower()


def test_preflight_fails_on_default_branch(capsys) -> None:
    # First _run call: git rev-parse --is-inside-work-tree -> ok
    # Second: git branch --show-current -> "main"
    # Third: gh repo view ... -> "main"
    results = [
        _result(stdout="true\n"),
        _result(stdout="main\n"),
        _result(stdout="main\n"),
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 10
    assert "default branch" in capsys.readouterr().out.lower()


def test_preflight_fails_on_dirty_tree(capsys) -> None:
    results = [
        _result(stdout="true\n"),
        _result(stdout="feature-x\n"),
        _result(stdout="main\n"),
        _result(stdout=" M file.py\n"),  # porcelain non-empty
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 10
    assert "uncommitted" in capsys.readouterr().out.lower()


def test_preflight_passes_on_clean_feature_branch() -> None:
    results = [
        _result(stdout="true\n"),
        _result(stdout="feature-x\n"),
        _result(stdout="main\n"),
        _result(stdout=""),  # porcelain empty
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 4 new tests FAIL with `AttributeError: module ... has no attribute 'preflight'` or `'_run'`.

- [ ] **Step 3: Implement `_run` and `preflight`**

Add these at the top of `agent_submit.py` (after the existing imports, before `parse_args`):

```python
import subprocess

EXIT_PREFLIGHT = 10


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _default_branch() -> str:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 7 tests PASS (3 arg-parse + 4 preflight).

- [ ] **Step 5: Commit**

```bash
git add agent_issues/cli/agent_submit.py tests/test_agent_submit.py
git commit -m "Add preflight guards to agent-submit"
```

---

## Task 4: PR create/update dispatch

Implement the step-2 logic: query `gh pr list`, then either create or edit.

**Files:**
- Modify: `agent_issues/cli/agent_submit.py`
- Modify: `tests/test_agent_submit.py`

- [ ] **Step 1: Write failing tests for PR create/update dispatch**

Append to `tests/test_agent_submit.py`:

```python
def test_upsert_pr_creates_when_none_exists() -> None:
    results = [
        _result(stdout="[]"),  # gh pr list
        _result(stdout="https://github.com/o/r/pull/7\n"),  # gh pr create
        _result(stdout="7\n"),  # gh pr list --json number after create
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        pr_num = agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=False
        )
    assert pr_num == "7"
    calls = [call.args[0] for call in run_mock.call_args_list]
    assert calls[0][:4] == ["gh", "pr", "list", "--head"]
    assert calls[1][:3] == ["gh", "pr", "create"]
    assert "--draft" not in calls[1]


def test_upsert_pr_creates_draft_when_flag_set() -> None:
    results = [
        _result(stdout="[]"),
        _result(stdout="https://github.com/o/r/pull/8\n"),
        _result(stdout="8\n"),
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=True
        )
    create_call = run_mock.call_args_list[1].args[0]
    assert "--draft" in create_call


def test_upsert_pr_edits_when_one_exists() -> None:
    import json as _json
    results = [
        _result(stdout=_json.dumps([{"number": 5}])),
        _result(stdout=""),  # gh pr edit
        _result(stdout="https://github.com/o/r/pull/5\n"),  # gh pr view for URL
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        pr_num = agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=True
        )
    assert pr_num == "5"
    edit_call = run_mock.call_args_list[1].args[0]
    assert edit_call[:3] == ["gh", "pr", "edit"]
    assert "5" in edit_call
    # --draft must NOT be passed on edit
    assert "--draft" not in edit_call


def test_upsert_pr_aborts_when_multiple_open_prs(capsys) -> None:
    import json as _json
    results = [
        _result(stdout=_json.dumps([{"number": 5}, {"number": 6}])),
    ]
    with patch.object(agent_submit, "_run", side_effect=results), pytest.raises(SystemExit) as exc:
        agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=False
        )
    assert exc.value.code == 10
    assert "more than one" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 4 new tests FAIL with `AttributeError: ... has no attribute 'upsert_pr'`.

- [ ] **Step 3: Implement `upsert_pr`**

Add to `agent_submit.py` after `preflight()`:

```python
import json
import sys


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
            f"agent-submit: branch {branch} has {len(prs)} open PRs, expected at most 1. "
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
        print(create_result.stdout.strip(), flush=True)
        number_result = _run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number"]
        )
        assert number_result.returncode == 0, f"gh pr list (post-create) failed: {number_result.stderr}"
        return number_result.stdout.strip()

    pr_number = str(prs[0]["number"])
    edit_result = _run(["gh", "pr", "edit", pr_number, "--title", title, "--body", body])
    assert edit_result.returncode == 0, f"gh pr edit failed: {edit_result.stderr}"
    view_result = _run(["gh", "pr", "view", pr_number, "--json", "url", "--jq", ".url"])
    if view_result.returncode == 0:
        print(view_result.stdout.strip(), flush=True)
    return pr_number
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_issues/cli/agent_submit.py tests/test_agent_submit.py
git commit -m "Add PR create/update dispatch to agent-submit"
```

---

## Task 5: Watcher integration + NEXT STEP footer

Wire preflight → push → upsert → watch into `main()`, relay exit codes, print tailored footers for non-zero.

**Files:**
- Modify: `agent_issues/cli/agent_submit.py`
- Modify: `tests/test_agent_submit.py`

- [ ] **Step 1: Write failing tests for the footer and main flow**

Append to `tests/test_agent_submit.py`:

```python
def test_next_step_footer_for_exit_1(capsys) -> None:
    agent_submit._print_next_step(1)
    out = capsys.readouterr().out
    assert "CI failed" in out
    assert "agent-submit" in out


def test_next_step_footer_for_exit_2(capsys) -> None:
    agent_submit._print_next_step(2)
    assert "Review feedback" in capsys.readouterr().out


def test_next_step_footer_for_exit_3(capsys) -> None:
    agent_submit._print_next_step(3)
    out = capsys.readouterr().out
    assert "Both" in out or "both" in out


def test_next_step_footer_for_exit_4(capsys) -> None:
    agent_submit._print_next_step(4)
    out = capsys.readouterr().out
    assert "timed out" in out.lower()
    assert "wait for the user" in out.lower()


def test_next_step_footer_silent_on_exit_0(capsys) -> None:
    agent_submit._print_next_step(0)
    assert capsys.readouterr().out == ""


def test_main_runs_full_flow_and_relays_watcher_exit() -> None:
    from agent_issues.cli import issue_watch_pr
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=0),
        patch.object(agent_submit, "upsert_pr", return_value="42"),
        patch.object(issue_watch_pr, "run", return_value=2) as watcher_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 2
    watcher_mock.assert_called_once_with(pr="42")


def test_main_exits_early_on_preflight_failure() -> None:
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=10),
        patch.object(agent_submit, "_push") as push_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 10
    push_mock.assert_not_called()


def test_main_exits_on_push_failure_without_upserting() -> None:
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=128),
        patch.object(agent_submit, "upsert_pr") as upsert_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 128
    upsert_mock.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_submit.py -v`
Expected: 8 new tests FAIL with `AttributeError` on `_print_next_step`, `_current_branch`, or `_push`.

- [ ] **Step 3: Implement the remaining pieces and rewrite `main()`**

Add to `agent_submit.py` after `upsert_pr`:

```python
from agent_issues.cli import issue_watch_pr


def _current_branch() -> str:
    result = _run(["git", "branch", "--show-current"])
    assert result.returncode == 0, f"git branch --show-current failed: {result.stderr}"
    branch = result.stdout.strip()
    assert branch, "Expected non-empty current branch"
    return branch


def _push() -> int:
    """Push HEAD to origin. Returns git's exit code."""
    return subprocess.run(["git", "push", "origin", "HEAD"]).returncode


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
    elif code == 3:
        print(
            "\nNEXT STEP: Both CI failures and review feedback. Address both, then re-run `agent-submit`.",
            flush=True,
        )
    elif code == 4:
        print(
            "\nNEXT STEP: Watcher timed out — likely all fine but didn't confirm. "
            "Do not re-run automatically; stop and wait for the user.",
            flush=True,
        )


def main() -> None:
    args = parse_args()

    code = preflight()
    if code != 0:
        sys.exit(code)

    branch = _current_branch()
    base = args.base if args.base is not None else _default_branch()

    push_code = _push()
    if push_code != 0:
        sys.exit(push_code)

    pr_number = upsert_pr(
        branch=branch, base=base, title=args.title, body=args.body, draft=args.draft
    )

    watcher_code = issue_watch_pr.run(pr=pr_number)
    _print_next_step(watcher_code)
    sys.exit(watcher_code)
```

Remove the `raise NotImplementedError(args)` stub from Task 2 — `main()` is now fully implemented.

- [ ] **Step 4: Run all tests to verify they pass**

Run: `uv run pytest tests/test_agent_submit.py tests/test_watch_pr.py -v`
Expected: 28 tests PASS (19 agent_submit tests — 3 from Task 2 + 4 from Task 3 + 4 from Task 4 + 8 from this task — plus 9 watch_pr tests unchanged from Task 1).

- [ ] **Step 5: Commit**

```bash
git add agent_issues/cli/agent_submit.py tests/test_agent_submit.py
git commit -m "Wire agent-submit end-to-end: preflight, push, upsert, watch"
```

---

## Task 6: Script entry + delete `issue_finalize_pr.py`

Wire the new script into `pyproject.toml` and drop the old module.

**Files:**
- Modify: `pyproject.toml`
- Delete: `agent_issues/cli/issue_finalize_pr.py`

- [ ] **Step 1: Update `pyproject.toml`**

In `pyproject.toml`, replace this line:

```
issue-finalize-pr = "agent_issues.cli.issue_finalize_pr:main"
```

with:

```
agent-submit = "agent_issues.cli.agent_submit:main"
```

Keep alphabetical order within the `[project.scripts]` block (so `agent-submit` goes at the top, before `issue-abandon`).

- [ ] **Step 2: Delete the old module**

Run: `rm agent_issues/cli/issue_finalize_pr.py`

- [ ] **Step 3: Reinstall the package so the new script is on PATH**

Run: `uv pip install -e .`
Expected: "Successfully installed agent-issues-0.1.0" or similar.

- [ ] **Step 4: Sanity-check the new command resolves**

Run: `agent-submit --help`
Expected: argparse help text showing `--title`, `--body`, `--draft`, `--base`.

- [ ] **Step 5: Verify no stale references to `issue-finalize-pr` remain in code**

Run: `grep -rn "issue-finalize-pr\|issue_finalize_pr" agent_issues/ tests/ pyproject.toml`
Expected: no output. (References in `skills/` and `doc/` are handled in Task 7.)

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml agent_issues/cli/issue_finalize_pr.py
git commit -m "Register agent-submit script; remove issue_finalize_pr"
```

---

## Task 7: Update skills and docs

Swap `issue-finalize-pr` callers to `agent-submit`; update `create-pr` skill to use it directly.

**Files:**
- Modify: `skills/create-pr/SKILL.md`
- Modify: `skills/solve-issue/SKILL.md`
- Modify: `doc/issues.md`

- [ ] **Step 1: Update `skills/create-pr/SKILL.md`**

Replace the current block that runs from "8. **Push and create the PR:**" through the end of the step-10 fix-loop instructions with the following single step (keep the surrounding numbered sequence; this replaces steps 8, 9, and 10):

````markdown
8. **Submit the PR.** Run `agent-submit` — it pushes HEAD, creates or updates the PR with the title/body you provide, and runs the CI watcher end-to-end:

   ```bash
   agent-submit --title "<concise title>" --body "$(cat <<'EOF'
   ## Summary
   <2-5 bullets mixing why and what>

   ## Test plan
   <bulleted checklist — what you verified>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

   `agent-submit` exits with one of these codes:

   | Code  | Meaning                                                                                   |
   |-------|-------------------------------------------------------------------------------------------|
   | 0     | All clean. Done.                                                                          |
   | 1     | CI failed or merge conflict. If merge conflict, merge the default branch and resolve. Otherwise use `gh run view <run-id> --log-failed` (ID from the printed link), fix the root cause, commit, then re-run `agent-submit`. |
   | 2     | Review feedback arrived. Read the printed feedback; for inline comments fetch full context with `gh api repos/{owner}/{repo}/pulls/{number}/comments`. Address each, commit, re-run. |
   | 3     | Both CI failures and review feedback. Address both, then re-run.                          |
   | 4     | Watcher timed out — **terminal**. Do not re-run automatically; stop and wait for the user. |
   | 10+   | Preflight failed (on default branch, dirty working tree, not a git repo, etc.). Fix and re-run. |

   **Cap at 10 fix-and-resubmit iterations.** If after 10 rounds CI still fails or new feedback keeps arriving, report the situation to the user and stop.
````

Remove the old step 9 ("Report the PR URL") and step 10 ("Watch CI, codex review...") — `agent-submit` now prints the URL and runs the watcher itself.

- [ ] **Step 2: Update `skills/solve-issue/SKILL.md`**

In `skills/solve-issue/SKILL.md`:

- At line ~107-108, replace:
  ```
     - [ ] Push final changes: `git push origin HEAD`
     - [ ] Finalize PR: `issue-finalize-pr --title "..." --body "..."`
  ```
  with:
  ```
     - [ ] Submit PR: `agent-submit --title "..." --body "..."` (handles push, PR create/update, and CI watcher)
  ```

- At line ~129, replace the two-sentence note that starts "After the file is deleted, `issue-claim --current` may stop working..." with:
  ```
      - If you merged the default branch after claiming, re-check whether the issue file was renamed (for example to add a priority prefix or `blocked-` prefix) and delete the renamed path that now exists on your branch. If `issue-claim --current` can no longer resolve the claim because the file is gone, that does not mean the claim itself is gone — `agent-submit` does not need the claim file to exist.
  ```

- At line ~137-138, replace:
  ```
      ```bash
      issue-finalize-pr --title "<concise PR title>" --body "<PR description with summary, test plan>"
      ```
  ```
  with:
  ```
      ```bash
      agent-submit --title "<concise PR title>" --body "<PR description with summary, test plan>"
      ```
  ```

- [ ] **Step 3: Update `doc/issues.md`**

At line ~86 of `doc/issues.md`, replace:

```
issue-finalize-pr --title "Fix login redirect" --body "..."
```

with:

```
agent-submit --title "Fix login redirect" --body "..."
```

- [ ] **Step 4: Confirm no stale references remain anywhere**

Run: `grep -rn "issue-finalize-pr\|issue_finalize_pr" .`
Expected: no matches (or only matches inside `doc/specs/` and `doc/plans/` — those reference the old name intentionally as historical context and are fine to leave).

- [ ] **Step 5: Commit**

```bash
git add skills/create-pr/SKILL.md skills/solve-issue/SKILL.md doc/issues.md
git commit -m "Update skills and docs to use agent-submit"
```

---

## Task 8: End-to-end verification

Final pass: run the full test suite and lint, confirm nothing is broken.

**Files:** none

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS (including any pre-existing tests untouched by this work).

- [ ] **Step 2: Confirm the `agent-submit --help` output is accurate**

Run: `agent-submit --help`
Expected: usage line showing `--title`, `--body`, optional `--draft`, `--base`.

- [ ] **Step 3: Manual smoke check (read-only — do not actually push)**

Run: `agent-submit --title T --body B` from a clean working tree on a feature branch.
Expected: it will attempt a real push. **Skip this step unless you have a disposable test branch with no PR** — otherwise review the code one more time and trust the unit tests.

- [ ] **Step 4: No commit for this task** — verification only.
