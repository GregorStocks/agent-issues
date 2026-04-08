"""Watch a PR until checks and review feedback settle."""

import json
import subprocess
import sys
import time

POLL_INTERVAL = 30
TIMEOUT = 1800
STARTUP_GRACE = 120
CODEX_REVIEW_WAIT = 600

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


def should_stop_polling(checks: list[dict]) -> bool:
    if not checks:
        return False
    for check in checks:
        bucket = check.get("bucket")
        if bucket not in _PASS_BUCKETS and bucket not in ("pending", None):
            return True
    return all(check.get("bucket") not in ("pending", None) for check in checks)


def get_pr_reactions(pr: str, nwo: str) -> list[dict]:
    """Fetch reactions on the PR body."""
    result = run_gh("api", "--paginate", f"repos/{nwo}/issues/{pr}/reactions")
    if result.returncode != 0:
        return []
    return json.loads(result.stdout) if result.stdout.strip() else []


def has_reaction(reactions: list[dict], content: str) -> bool:
    return any(r.get("content") == content for r in reactions)


def get_review_feedback(pr: str, nwo: str) -> list[str]:
    result = run_gh("pr", "view", pr, "--json", "author,reviews,comments")
    assert result.returncode == 0, f"Failed to fetch PR details: {result.stderr}"
    data = json.loads(result.stdout)
    pr_author = data["author"]["login"]

    feedback = []
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
        if body:
            feedback.append(f"[{state}] @{author}: {body}")
        else:
            feedback.append(f"[{state}] @{author} (see inline comments)")

    comments = data.get("comments") or []
    for comment in comments:
        author = comment["author"]["login"]
        if author == pr_author:
            continue
        body = comment.get("body")
        body = body.strip() if body else None
        if body:
            feedback.append(f"[COMMENT] @{author}: {body}")

    inline_result = run_gh(
        "api",
        "--paginate",
        f"repos/{nwo}/pulls/{pr}/comments",
        "--jq",
        ".[] | [.user.login, .path, (.line | tostring), .body] | @tsv",
    )
    assert inline_result.returncode == 0, (
        f"Failed to fetch inline comments: {inline_result.stderr}"
    )
    if inline_result.stdout.strip():
        for line in inline_result.stdout.strip().split("\n"):
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            author, path, line_no, body = parts
            if author == pr_author:
                continue
            feedback.append(f"[INLINE] @{author} on {path}:{line_no}: {body.strip()}")

    return feedback


def main() -> None:
    pr = sys.argv[1] if len(sys.argv) > 1 else get_pr_number()
    nwo = get_repo_nwo()
    print(f"Watching PR #{pr}...", flush=True)

    # Snapshot existing feedback so we only report new comments/reviews
    baseline_feedback = set(get_review_feedback(pr, nwo))

    start = time.monotonic()
    checks = get_checks(pr)
    while not checks:
        if check_merge_conflict(pr):
            print(
                "\nPR has a merge conflict — CI will not run until conflicts are resolved.",
                flush=True,
            )
            sys.exit(1)
        elapsed = time.monotonic() - start
        assert elapsed <= STARTUP_GRACE, f"No CI checks found for PR #{pr} after {STARTUP_GRACE}s"
        print("Waiting for checks to start...", flush=True)
        time.sleep(POLL_INTERVAL)
        checks = get_checks(pr)

    while not should_stop_polling(checks):
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT:
            pending = [check["name"] for check in checks if check.get("bucket") == "pending"]
            print(f"\nTimed out after {TIMEOUT}s. Still pending: {', '.join(pending)}", flush=True)
            sys.exit(4)

        if check_merge_conflict(pr):
            print("\nPR has a merge conflict with the base branch. Merge or rebase to resolve.", flush=True)
            sys.exit(1)

        pending = [check["name"] for check in checks if check.get("bucket") == "pending"]
        mins = int(elapsed // 60)
        print(f"  [{mins}m] {len(pending)} pending: {', '.join(pending[:5])}", flush=True)
        time.sleep(POLL_INTERVAL)
        checks = get_checks(pr)

    if check_merge_conflict(pr):
        print("\nPR has a merge conflict with the base branch. Merge or rebase to resolve.", flush=True)
        sys.exit(1)

    failed = [check for check in checks if check.get("bucket") not in _PASS_BUCKETS | {"pending", None}]

    # Wait for potential codex review (eyes emoji on PR) if CI passed
    if not failed:
        eyes_seen = False
        # Poll until at least CODEX_REVIEW_WAIT elapsed since start
        while time.monotonic() - start < CODEX_REVIEW_WAIT:
            reactions = get_pr_reactions(pr, nwo)
            if has_reaction(reactions, "eyes") or has_reaction(reactions, "+1"):
                eyes_seen = has_reaction(reactions, "eyes")
                break
            remaining = max(0, int((CODEX_REVIEW_WAIT - (time.monotonic() - start)) // 60))
            print(f"  Waiting for codex review... ({remaining}m remaining)", flush=True)
            time.sleep(POLL_INTERVAL)
        else:
            # Final check after wait expires
            reactions = get_pr_reactions(pr, nwo)
            eyes_seen = has_reaction(reactions, "eyes")

        if eyes_seen:
            print("\nCodex is reviewing (eyes). Waiting for verdict...", flush=True)
            codex_baseline = get_review_feedback(pr, nwo)
            while True:
                elapsed = time.monotonic() - start
                if elapsed > TIMEOUT:
                    print(f"\nTimed out waiting for codex review after {TIMEOUT}s.", flush=True)
                    sys.exit(4)

                reactions = get_pr_reactions(pr, nwo)
                if has_reaction(reactions, "+1"):
                    print("Codex approved (thumbs up).", flush=True)
                    break

                current_feedback = get_review_feedback(pr, nwo)
                if len(current_feedback) > len(codex_baseline):
                    print("Codex left review comments.", flush=True)
                    break

                mins = int(elapsed // 60)
                print(f"  [{mins}m] Codex still reviewing...", flush=True)
                time.sleep(POLL_INTERVAL)

    all_feedback = get_review_feedback(pr, nwo)
    feedback = [f for f in all_feedback if f not in baseline_feedback]

    exit_code = 0

    if failed:
        exit_code |= 1
        print(f"\n{len(failed)} check(s) FAILED:")
        for check in failed:
            print(f"  - {check['name']} ({check.get('bucket')}): {check.get('link', 'no link')}")

    if feedback:
        exit_code |= 2
        print(f"\n{len(feedback)} new review comment(s):")
        for item in feedback:
            print(f"  {item}")

    if exit_code == 0:
        passed = [check for check in checks if check.get("bucket") == "pass"]
        skipped = [check for check in checks if check.get("bucket") == "skipping"]
        print(
            f"\nAll checks passed ({len(passed)} passed, {len(skipped)} skipped). No review feedback.",
            flush=True,
        )

    sys.exit(exit_code)
