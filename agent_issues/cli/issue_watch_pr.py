"""Watch a PR until checks and review feedback settle."""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone

POLL_INTERVAL = 30
COMMENT_GRACE = 20
NO_EYES_TIMEOUT = 15 * 60
TIMEOUT = 1800

_PASS_BUCKETS = {"pass", "skipping"}


def run_gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def get_pr_number() -> str:
    result = run_gh("pr", "view", "--json", "number", "--jq", ".number")
    assert result.returncode == 0, f"No open PR for current branch: {result.stderr}"
    return result.stdout.strip()


def get_repo_nwo() -> str:
    result = run_gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    assert result.returncode == 0, f"Failed to get repo info: {result.stderr}"
    return result.stdout.strip()


def get_pr_lifecycle_state(pr: str) -> str:
    result = run_gh("pr", "view", pr, "--json", "state,mergedAt")
    if result.returncode != 0:
        return "open"
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    if data.get("mergedAt"):
        return "merged"
    if data.get("state") == "CLOSED":
        return "closed"
    return "open"




def check_merge_conflict(pr: str) -> bool:
    result = run_gh("pr", "view", pr, "--json", "mergeable", "--jq", ".mergeable")
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "CONFLICTING"


def get_checks(pr: str) -> list[dict]:
    result = run_gh("pr", "checks", pr, "--json", "bucket,name,link,workflow")
    assert result.returncode in (0, 1, 8), (
        f"gh pr checks failed (exit {result.returncode}): {result.stderr}"
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def get_pr_reactions(pr: str, nwo: str) -> list[dict]:
    """Fetch reactions on the PR body."""
    result = run_gh("api", "--paginate", f"repos/{nwo}/issues/{pr}/reactions")
    if result.returncode != 0:
        return []
    return json.loads(result.stdout) if result.stdout.strip() else []


def has_reaction(reactions: list[dict], content: str) -> bool:
    return any(r.get("content") == content for r in reactions)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_review_feedback(pr: str, nwo: str) -> list[dict]:
    """Return non-author review/comment feedback with timestamps.

    Each item: {"formatted": str, "created_at": datetime}.
    """
    result = run_gh("pr", "view", pr, "--json", "author,reviews,comments")
    assert result.returncode == 0, f"Failed to fetch PR details: {result.stderr}"
    data = json.loads(result.stdout)
    pr_author = data["author"]["login"]

    feedback: list[dict] = []
    latest_review: dict[str, dict] = {}
    reviews = data.get("reviews") or []
    for review in reviews:
        latest_review[review["author"]["login"]] = review

    for author, review in latest_review.items():
        state = review.get("state")
        if state in ("APPROVED", "PENDING", "DISMISSED") or author == pr_author:
            continue
        body = review.get("body")
        body = body.strip() if body else None
        ts = _parse_ts(review["submittedAt"])
        if body:
            feedback.append({"formatted": f"[{state}] @{author}: {body}", "created_at": ts})
        else:
            feedback.append(
                {"formatted": f"[{state}] @{author} (see inline comments)", "created_at": ts}
            )

    comments = data.get("comments") or []
    for comment in comments:
        author = comment["author"]["login"]
        if author == pr_author:
            continue
        body = comment.get("body")
        body = body.strip() if body else None
        if not body:
            continue
        ts = _parse_ts(comment["createdAt"])
        feedback.append({"formatted": f"[COMMENT] @{author}: {body}", "created_at": ts})

    inline_result = run_gh(
        "api",
        "--paginate",
        f"repos/{nwo}/pulls/{pr}/comments",
        "--jq",
        ".[] | [.user.login, .path, (.line | tostring), .body, .created_at] | @tsv",
    )
    assert inline_result.returncode == 0, (
        f"Failed to fetch inline comments: {inline_result.stderr}"
    )
    if inline_result.stdout.strip():
        for line in inline_result.stdout.strip().split("\n"):
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            author, path, line_no, body, created_at = parts
            if author == pr_author:
                continue
            feedback.append(
                {
                    "formatted": f"[INLINE] @{author} on {path}:{line_no}: {body.strip()}",
                    "created_at": _parse_ts(created_at),
                }
            )

    return feedback


def _print_failed(failed: list[dict]) -> None:
    print(f"\n{len(failed)} check(s) FAILED:")
    for check in failed:
        print(f"  - {check['name']} ({check.get('bucket')}): {check.get('link', 'no link')}")


def _print_feedback(items: list[dict]) -> None:
    print(f"\n{len(items)} new review comment(s):")
    for item in items:
        print(f"  {item['formatted']}")


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
