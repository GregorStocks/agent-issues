"""Watch a PR until checks and review feedback settle."""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLL_INTERVAL = 30
COMMENT_GRACE = 20
NO_EYES_TIMEOUT = 15 * 60
TIMEOUT = 1800

_PASS_BUCKETS = {"pass", "skipping"}
_RUN_LOGGER: logging.Logger | None = None


def run_gh(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if _RUN_LOGGER is not None:
        _RUN_LOGGER.debug(
            "gh %s -> exit=%s stdout=%r stderr=%r",
            " ".join(args),
            result.returncode,
            result.stdout,
            result.stderr,
        )
    return result


def _setup_run_logger() -> logging.Logger | None:
    logs_dir = Path.cwd() / "logs"
    if not logs_dir.is_dir():
        return None

    started_at = datetime.now().astimezone()
    file_name = (
        f"agent-submit-{started_at.strftime('%Y%m%d-%H%M%S%z')}"
        f"-pid{os.getpid()}-{time.time_ns()}.log"
    )
    log_path = logs_dir / file_name
    logger_name = f"agent_submit.issue_watch_pr.{started_at.strftime('%Y%m%d%H%M%S')}.{os.getpid()}.{time.time_ns()}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    logger.info(
        "agent-submit watcher logging enabled cwd=%s logs_dir=%s log_path=%s",
        Path.cwd(),
        logs_dir,
        log_path,
    )
    return logger


def _teardown_run_logger(logger: logging.Logger | None) -> None:
    if logger is None:
        return
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def _log(level: int, message: str, *args: Any) -> None:
    if _RUN_LOGGER is not None:
        _RUN_LOGGER.log(level, message, *args)


def get_pr_number() -> str:
    result = run_gh("pr", "view", "--json", "number", "--jq", ".number")
    assert result.returncode == 0, f"No open PR for current branch: {result.stderr}"
    pr = result.stdout.strip()
    _log(logging.DEBUG, "resolved current branch PR number=%s", pr)
    return pr


def get_repo_nwo() -> str:
    result = run_gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    assert result.returncode == 0, f"Failed to get repo info: {result.stderr}"
    nwo = result.stdout.strip()
    _log(logging.DEBUG, "resolved repo nameWithOwner=%s", nwo)
    return nwo


def get_pr_lifecycle_state(pr: str) -> str:
    result = run_gh("pr", "view", pr, "--json", "state,mergedAt")
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch PR lifecycle state for pr=%s; assuming open", pr)
        return "open"
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    if data.get("mergedAt"):
        _log(logging.DEBUG, "pr=%s lifecycle state=merged payload=%s", pr, data)
        return "merged"
    if data.get("state") == "CLOSED":
        _log(logging.DEBUG, "pr=%s lifecycle state=closed payload=%s", pr, data)
        return "closed"
    _log(logging.DEBUG, "pr=%s lifecycle state=open payload=%s", pr, data)
    return "open"


def check_merge_conflict(pr: str) -> bool:
    result = run_gh("pr", "view", pr, "--json", "mergeable", "--jq", ".mergeable")
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch mergeable state for pr=%s; assuming no conflict", pr)
        return False
    conflicting = result.stdout.strip() == "CONFLICTING"
    _log(logging.DEBUG, "pr=%s merge_conflict=%s raw=%r", pr, conflicting, result.stdout.strip())
    return conflicting


def get_checks(pr: str) -> list[dict]:
    result = run_gh("pr", "checks", pr, "--json", "bucket,name,link,workflow")
    assert result.returncode in (0, 1, 8), (
        f"gh pr checks failed (exit {result.returncode}): {result.stderr}"
    )
    checks = json.loads(result.stdout) if result.stdout.strip() else []
    _log(logging.DEBUG, "pr=%s fetched %s check(s): %s", pr, len(checks), checks)
    return checks


def get_pr_reactions(pr: str, nwo: str) -> list[dict]:
    """Fetch reactions on the PR body."""
    result = run_gh("api", "--paginate", f"repos/{nwo}/issues/{pr}/reactions")
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch PR reactions for pr=%s repo=%s", pr, nwo)
        return []
    reactions = json.loads(result.stdout) if result.stdout.strip() else []
    _log(logging.DEBUG, "pr=%s fetched %s reaction(s): %s", pr, len(reactions), reactions)
    return reactions


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

    _log(logging.DEBUG, "pr=%s fetched %s feedback item(s)", pr, len(feedback))
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
    global _RUN_LOGGER

    _RUN_LOGGER = _setup_run_logger()
    try:
        pr = pr if pr is not None else get_pr_number()
        nwo = get_repo_nwo()
        print(f"Watching PR #{pr}...", flush=True)
        _log(logging.INFO, "starting watcher pr=%s repo=%s", pr, nwo)

        state = get_pr_lifecycle_state(pr)
        if state == "merged":
            _log(logging.INFO, "exiting clean because pr=%s is already merged", pr)
            print("\nPR has been merged.", flush=True)
            return 0
        if state == "closed":
            _log(logging.INFO, "exiting with failure because pr=%s is already closed", pr)
            print("\nPR was closed without merging.", flush=True)
            return 1

        baseline_feedback = {f["formatted"] for f in get_review_feedback(pr, nwo)}
        _log(logging.INFO, "baseline feedback count=%s items=%s", len(baseline_feedback), sorted(baseline_feedback))

        start = time.monotonic()
        eyes_seen = False
        poll_count = 0

        while True:
            poll_count += 1
            _log(logging.INFO, "poll=%s begin", poll_count)

            state = get_pr_lifecycle_state(pr)
            if state == "merged":
                _log(logging.INFO, "poll=%s exiting clean because pr merged", poll_count)
                print("\nPR has been merged.", flush=True)
                return 0
            if state == "closed":
                _log(logging.INFO, "poll=%s exiting with failure because pr closed", poll_count)
                print("\nPR was closed without merging.", flush=True)
                return 1

            if check_merge_conflict(pr):
                _log(logging.INFO, "poll=%s exiting with failure because merge conflict detected", poll_count)
                print(
                    "\nPR has a merge conflict with the base branch. Merge or rebase to resolve.",
                    flush=True,
                )
                return 1

            checks = get_checks(pr)
            elapsed = time.monotonic() - start
            pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
            failed = [
                c for c in checks if c.get("bucket") not in _PASS_BUCKETS | {"pending", None}
            ]
            _log(
                logging.INFO,
                "poll=%s observed elapsed=%.1fs checks=%s pending=%s failed=%s",
                poll_count,
                elapsed,
                checks,
                pending,
                failed,
            )

            if elapsed > TIMEOUT:
                _log(logging.WARNING, "poll=%s exiting with timeout pending=%s", poll_count, pending)
                print(
                    f"\nTimed out after {TIMEOUT}s. Still pending: {', '.join(pending)}",
                    flush=True,
                )
                return 4

            if failed:
                _log(logging.INFO, "poll=%s exiting with failed checks=%s", poll_count, failed)
                _print_failed(failed)
                return 1

            reactions = get_pr_reactions(pr, nwo)
            saw_eyes = has_reaction(reactions, "eyes")
            saw_plus_one = has_reaction(reactions, "+1")
            if saw_eyes:
                eyes_seen = True

            all_feedback = get_review_feedback(pr, nwo)
            new_feedback = [f for f in all_feedback if f["formatted"] not in baseline_feedback]
            oldest_feedback_age = None
            if new_feedback:
                oldest = min(f["created_at"] for f in new_feedback)
                oldest_feedback_age = (datetime.now(timezone.utc) - oldest).total_seconds()
            _log(
                logging.INFO,
                "poll=%s observed reactions=%s eyes_seen=%s plus_one=%s feedback_total=%s new_feedback=%s oldest_new_feedback_age=%.1f",
                poll_count,
                reactions,
                eyes_seen,
                saw_plus_one,
                len(all_feedback),
                new_feedback,
                -1.0 if oldest_feedback_age is None else oldest_feedback_age,
            )

            if new_feedback and oldest_feedback_age is not None and oldest_feedback_age >= COMMENT_GRACE:
                _log(
                    logging.INFO,
                    "poll=%s exiting with review feedback age=%.1fs items=%s",
                    poll_count,
                    oldest_feedback_age,
                    new_feedback,
                )
                _print_feedback(new_feedback)
                return 2

            if elapsed >= NO_EYES_TIMEOUT and not eyes_seen:
                mins = NO_EYES_TIMEOUT // 60
                if pending:
                    _log(
                        logging.INFO,
                        "poll=%s exiting clean after no-eyes timeout with pending checks=%s",
                        poll_count,
                        pending,
                    )
                    print(
                        f"\nNo codex review after {mins} min. "
                        f"CI still has {len(pending)} pending check(s): {', '.join(pending)}",
                        flush=True,
                    )
                elif not checks:
                    _log(logging.INFO, "poll=%s exiting clean after no-eyes timeout with no checks", poll_count)
                    print(f"\nNo codex review after {mins} min. No CI checks detected.", flush=True)
                else:
                    passed = [c for c in checks if c.get("bucket") == "pass"]
                    skipped = [c for c in checks if c.get("bucket") == "skipping"]
                    _log(
                        logging.INFO,
                        "poll=%s exiting clean after no-eyes timeout passed=%s skipped=%s",
                        poll_count,
                        passed,
                        skipped,
                    )
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
            if all_checks_pass and saw_plus_one:
                passed = [c for c in checks if c.get("bucket") == "pass"]
                skipped = [c for c in checks if c.get("bucket") == "skipping"]
                _log(
                    logging.INFO,
                    "poll=%s exiting clean with codex approval passed=%s skipped=%s",
                    poll_count,
                    passed,
                    skipped,
                )
                print(
                    f"\nAll checks passed ({len(passed)} passed, {len(skipped)} skipped). "
                    f"Codex approved (thumbs up). No review feedback.",
                    flush=True,
                )
                return 0

            mins = int(elapsed // 60)
            if pending:
                _log(logging.INFO, "poll=%s sleeping %ss with pending checks=%s", poll_count, POLL_INTERVAL, pending)
                print(
                    f"  [{mins}m] {len(pending)} check(s) pending: {', '.join(pending[:5])}",
                    flush=True,
                )
            elif not checks:
                _log(logging.INFO, "poll=%s sleeping %ss waiting for checks to start", poll_count, POLL_INTERVAL)
                print(f"  [{mins}m] Waiting for checks to start...", flush=True)
            elif eyes_seen:
                _log(logging.INFO, "poll=%s sleeping %ss with ci done and codex reviewing", poll_count, POLL_INTERVAL)
                print(f"  [{mins}m] CI done; codex reviewing...", flush=True)
            else:
                _log(logging.INFO, "poll=%s sleeping %ss with ci done and waiting for codex", poll_count, POLL_INTERVAL)
                print(f"  [{mins}m] CI done; waiting for codex...", flush=True)

            time.sleep(POLL_INTERVAL)
    finally:
        logger = _RUN_LOGGER
        _RUN_LOGGER = None
        _teardown_run_logger(logger)


def main() -> None:
    pr = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(pr))
