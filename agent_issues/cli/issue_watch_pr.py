"""Watch a PR until checks and review feedback settle."""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection

POLL_INTERVAL = 30
COMMENT_GRACE = 20
NO_EYES_TIMEOUT = 45 * 60
TIMEOUT = NO_EYES_TIMEOUT + 10 * 60

_PASS_BUCKETS = {"pass", "skipping"}

CODEX_STATE_MARKER = "codex-review-state:v1"
CODEX_SUMMARY_MARKER = "codex-pull-request-review-summary"
_CODEX_STATE_RE = re.compile(r"<!--\s*codex-review-state:v1\s+(\{.*?\})\s*-->", re.DOTALL)
# Codex review statuses that mean the review ended without a result for this commit.
# Any other non-completed status (observed: "running") is treated as still reviewing.
_CODEX_ENDED_STATUSES = {"failed", "error", "errored", "cancelled", "canceled", "skipped", "timed_out"}
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
    result = run_gh(
        "api",
        "--paginate",
        f"repos/{nwo}/issues/{pr}/reactions",
        "--jq",
        ".[] | {content, created_at, login: .user.login}",
    )
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch PR reactions for pr=%s repo=%s", pr, nwo)
        return []
    reactions = _json_lines(result.stdout)
    _log(logging.DEBUG, "pr=%s fetched %s reaction(s): %s", pr, len(reactions), reactions)
    return reactions


def _json_lines(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def has_reaction(reactions: list[dict], content: str) -> bool:
    return any(r.get("content") == content for r in reactions)


def has_reaction_since(reactions: list[dict], content: str, since: datetime | None) -> bool:
    """True if a reaction with this content was created at or after `since`.

    Used for the +1 fallback so a thumbs-up left on an earlier head does not
    count as approval of a newly pushed head.
    """
    if since is None:
        return has_reaction(reactions, content)
    for reaction in reactions:
        if reaction.get("content") != content or not reaction.get("created_at"):
            continue
        if _parse_ts(reaction["created_at"]) >= since:
            return True
    return False


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_codex_summary_comment(login: str, body: str) -> bool:
    return "codex" in login.lower() and (CODEX_STATE_MARKER in body or CODEX_SUMMARY_MARKER in body)


def get_pr_head(pr: str) -> dict:
    """Return {"sha", "committed_at", "author"} for the PR's current head.

    committed_at is the head commit's committer date, used as a lower bound for
    when the head was pushed (GitHub no longer exposes push times directly).
    """
    result = run_gh("pr", "view", pr, "--json", "headRefOid,commits,author")
    assert result.returncode == 0, f"Failed to fetch PR head: {result.stderr}"
    data = json.loads(result.stdout)
    sha = data["headRefOid"]
    committed_at = None
    for commit in data.get("commits") or []:
        if commit.get("oid") == sha and commit.get("committedDate"):
            committed_at = _parse_ts(commit["committedDate"])
    head = {"sha": sha, "committed_at": committed_at, "author": data["author"]["login"]}
    _log(logging.DEBUG, "pr=%s head=%s", pr, head)
    return head


def parse_codex_review_state(body: str) -> dict | None:
    match = _CODEX_STATE_RE.search(body)
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return state if isinstance(state, dict) else None


def get_codex_review_state(pr: str, nwo: str) -> dict | None:
    """Return the state from Codex's latest "Codex Review Summary" comment.

    The comment carries a machine-readable marker such as
    `<!-- codex-review-state:v1 {"code_review": {"commit_sha": ..., "status":
    "completed", "has_unresolved_findings": ...}, "security_review": {...}} -->`.
    Returns the parsed object plus "updated_at" (datetime), or None when the
    repo has no summary comment.
    """
    result = run_gh(
        "api",
        "--paginate",
        f"repos/{nwo}/issues/{pr}/comments",
        "--jq",
        ".[] | {login: .user.login, body, created_at, updated_at}",
    )
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch issue comments for pr=%s repo=%s", pr, nwo)
        return None
    latest: tuple[datetime, dict] | None = None
    for comment in _json_lines(result.stdout):
        body = comment.get("body") or ""
        if not is_codex_summary_comment(comment.get("login") or "", body):
            continue
        state = parse_codex_review_state(body)
        if state is None:
            continue
        updated_at = _parse_ts(comment.get("updated_at") or comment["created_at"])
        if latest is None or updated_at >= latest[0]:
            latest = (updated_at, state)
    if latest is None:
        _log(logging.DEBUG, "pr=%s has no codex review summary comment", pr)
        return None
    state = dict(latest[1])
    state["updated_at"] = latest[0]
    _log(logging.DEBUG, "pr=%s codex review state=%s", pr, state)
    return state


def _review_part(state: dict, key: str) -> dict:
    part = state.get(key)
    return part if isinstance(part, dict) else {}


def classify_codex_review(state: dict | None, head_sha: str) -> str:
    """Classify the Codex summary relative to the PR head.

    Returns one of:
        "absent"    - no summary comment; fall back to reactions
        "stale"     - latest code review is for an older commit
        "reviewing" - a review of the head is still running
        "ended"     - the head's review ended without completing (e.g. failed)
        "completed" - the head's code review completed and no security review of it is running
    """
    if state is None:
        return "absent"
    code = _review_part(state, "code_review")
    if code.get("commit_sha") != head_sha:
        return "stale"
    status = code.get("status")
    if status in _CODEX_ENDED_STATUSES:
        return "ended"
    if status != "completed":
        return "reviewing"
    security = _review_part(state, "security_review")
    security_status = security.get("status")
    if (
        security.get("commit_sha") == head_sha
        and security_status is not None
        and security_status != "completed"
        and security_status not in _CODEX_ENDED_STATUSES
    ):
        return "reviewing"
    return "completed"


_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first: 50) {
            nodes { author { login } body createdAt url }
          }
        }
      }
    }
  }
}
"""


def get_unresolved_review_threads(pr: str, nwo: str, pr_author: str) -> list[dict]:
    """Return unresolved review threads started by someone other than the PR author.

    Each item: {"path", "line", "outdated", "addressed", "first", "url"} where
    "addressed" means the PR author has the last word in the thread (replied
    after the latest reviewer comment) and "first" is the opening comment body.
    """
    owner, name = nwo.split("/", 1)
    result = run_gh(
        "api",
        "graphql",
        "-f",
        f"query={_REVIEW_THREADS_QUERY}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={pr}",
    )
    if result.returncode != 0:
        _log(logging.WARNING, "failed to fetch review threads for pr=%s repo=%s", pr, nwo)
        return []
    data = json.loads(result.stdout)
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    threads: list[dict] = []
    for node in nodes:
        if node.get("isResolved"):
            continue
        comments = node["comments"]["nodes"]
        if not comments:
            continue
        logins = [((c.get("author") or {}).get("login") or "") for c in comments]
        if logins[0] == pr_author:
            continue
        threads.append(
            {
                "path": node.get("path"),
                "line": node.get("line"),
                "outdated": bool(node.get("isOutdated")),
                "addressed": logins[-1] == pr_author,
                "first": (comments[0].get("body") or "").strip(),
                "author": logins[0],
                "url": comments[0].get("url"),
            }
        )
    _log(logging.DEBUG, "pr=%s unresolved review threads=%s", pr, threads)
    return threads


def get_review_feedback(pr: str, nwo: str) -> list[dict]:
    """Return non-author review/comment feedback with stable IDs and timestamps.

    Each item: {"id": str, "formatted": str, "created_at": datetime}.

    The "id" prefixes the upstream identifier with a source tag
    ("review:", "comment:", "inline:") so callers can compare items
    across polls without confusing IDs from different namespaces. The
    "formatted" string can shift between polls — for inline comments
    GitHub relocates them to new line numbers after a force-push — so
    callers MUST track items by "id", not by "formatted".
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
        review_id = f"review:{review['id']}"
        if body:
            feedback.append(
                {
                    "id": review_id,
                    "formatted": f"[{state}] @{author}: {body}",
                    "created_at": ts,
                }
            )
        else:
            feedback.append(
                {
                    "id": review_id,
                    "formatted": f"[{state}] @{author} (see inline comments)",
                    "created_at": ts,
                }
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
        if is_codex_summary_comment(author, body):
            # Status comment that Codex edits in place; its state is read by
            # get_codex_review_state rather than reported as feedback.
            continue
        ts = _parse_ts(comment["createdAt"])
        feedback.append(
            {
                "id": f"comment:{comment['id']}",
                "formatted": f"[COMMENT] @{author}: {body}",
                "created_at": ts,
            }
        )

    inline_result = run_gh(
        "api",
        "--paginate",
        f"repos/{nwo}/pulls/{pr}/comments",
        "--jq",
        ".[] | [(.id | tostring), .user.login, .path, (.line | tostring), .body, .created_at] | @tsv",
    )
    assert inline_result.returncode == 0, (
        f"Failed to fetch inline comments: {inline_result.stderr}"
    )
    if inline_result.stdout.strip():
        for line in inline_result.stdout.strip().split("\n"):
            parts = line.split("\t", 5)
            if len(parts) < 6:
                continue
            comment_id, author, path, line_no, body, created_at = parts
            if author == pr_author:
                continue
            feedback.append(
                {
                    "id": f"inline:{comment_id}",
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


_BADGE_RE = re.compile(r"<sub>.*?</sub>|!\[[^\]]*\]\([^)]*\)|<[^>]+>|\*\*")


def _summarize_finding(body: str, limit: int = 200) -> str:
    """First meaningful line of a finding, with Codex's priority badge markup removed."""
    for line in body.splitlines():
        text = _BADGE_RE.sub("", line).strip()
        if text:
            return text if len(text) <= limit else text[: limit - 3] + "..."
    return ""


def _print_threads(threads: list[dict]) -> None:
    for thread in threads:
        location = f"{thread['path']}:{thread['line']}" if thread.get("line") else f"{thread['path']}"
        tags = []
        if thread["outdated"]:
            tags.append("outdated")
        tags.append("replied" if thread["addressed"] else "no reply")
        print(f"  [{', '.join(tags)}] @{thread['author']} on {location}: {_summarize_finding(thread['first'])}")
        if thread.get("url"):
            print(f"      {thread['url']}")


def run(pr: str | None = None, feedback_seen: Collection[str] | None = None) -> int:
    """Watch a PR. Returns an exit code rather than calling sys.exit.

    feedback_seen is the set of feedback IDs to treat as already handled.
    When omitted, all feedback present when the watcher starts is treated as seen.

    Codex review state comes from Codex's "Codex Review Summary" comment when
    present: the review is done once its code_review.commit_sha is the PR head
    and its status is "completed". Without that comment, the watcher falls back
    to PR-body reactions (eyes = reviewing, +1 created after the head commit =
    approved).

    Exit codes:
        0 - clean (merged, CI pass + codex finished the head with no new
            feedback, or CI pass without codex)
        1 - CI failed or merge conflict
        2 - review feedback present, including unresolved review threads
            that the PR author has not replied to
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

        if feedback_seen is not None:
            baseline_feedback = set(feedback_seen)
        else:
            baseline_feedback = {f["id"] for f in get_review_feedback(pr, nwo)}
        _log(logging.INFO, "baseline feedback count=%s ids=%s", len(baseline_feedback), sorted(baseline_feedback))

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

            if failed:
                _log(logging.INFO, "poll=%s exiting with failed checks=%s", poll_count, failed)
                _print_failed(failed)
                return 1

            head = get_pr_head(pr)
            codex_state = get_codex_review_state(pr, nwo)
            codex_status = classify_codex_review(codex_state, head["sha"])
            reactions = get_pr_reactions(pr, nwo)
            saw_eyes = has_reaction(reactions, "eyes")
            # A summary comment is authoritative; +1 is only a fallback, and only
            # counts if it was left after the head commit was created.
            saw_plus_one = codex_status == "absent" and has_reaction_since(
                reactions, "+1", head["committed_at"]
            )
            if saw_eyes or codex_status == "reviewing":
                eyes_seen = True

            all_feedback = get_review_feedback(pr, nwo)
            new_feedback = [f for f in all_feedback if f["id"] not in baseline_feedback]
            oldest_feedback_age = None
            if new_feedback:
                oldest = min(f["created_at"] for f in new_feedback)
                oldest_feedback_age = (datetime.now(timezone.utc) - oldest).total_seconds()
            _log(
                logging.INFO,
                "poll=%s observed head=%s codex_status=%s codex_state=%s reactions=%s eyes_seen=%s plus_one=%s feedback_total=%s new_feedback=%s oldest_new_feedback_age=%.1f",
                poll_count,
                head["sha"],
                codex_status,
                codex_state,
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

            if elapsed > TIMEOUT:
                _log(logging.WARNING, "poll=%s exiting with timeout pending=%s", poll_count, pending)
                pending_text = ", ".join(pending) if pending else "none"
                print(
                    f"\nTimed out after {TIMEOUT}s waiting for CI/review confirmation. "
                    f"Still pending: {pending_text}. "
                    "If this was run through agent-submit, stop and consult the user "
                    "before continuing. If the user asks you to continue later, first "
                    "inspect and address any PR feedback that arrived while stopped, "
                    "then re-run agent-submit rather than replacing it with manual PR "
                    "watching.",
                    flush=True,
                )
                return 4

            all_checks_pass = bool(checks) and all(
                c.get("bucket") in _PASS_BUCKETS for c in checks
            )
            codex_completed = codex_status == "completed"
            if codex_completed:
                summary_age = (datetime.now(timezone.utc) - codex_state["updated_at"]).total_seconds()
                # Give inline comments from the same review time to land.
                codex_completed = summary_age >= COMMENT_GRACE
            if all_checks_pass and not new_feedback and codex_completed:
                passed = [c for c in checks if c.get("bucket") == "pass"]
                skipped = [c for c in checks if c.get("bucket") == "skipping"]
                checks_text = f"All checks passed ({len(passed)} passed, {len(skipped)} skipped)."
                code_review = _review_part(codex_state, "code_review")
                if code_review.get("has_unresolved_findings"):
                    threads = get_unresolved_review_threads(pr, nwo, head["author"])
                    blocking = [t for t in threads if not t["addressed"] and not t["outdated"]]
                    _log(
                        logging.INFO,
                        "poll=%s codex completed head with unresolved findings threads=%s blocking=%s",
                        poll_count,
                        threads,
                        blocking,
                    )
                    if blocking:
                        print(
                            f"\n{checks_text} Codex finished reviewing {head['sha'][:10]}, "
                            f"but {len(blocking)} unresolved review thread(s) have no reply from "
                            f"@{head['author']}:",
                            flush=True,
                        )
                        _print_threads(blocking)
                        print(
                            "Fix each finding (or reply on the thread explaining why not), "
                            "reply on the thread, and resolve it.",
                            flush=True,
                        )
                        return 2
                    if threads:
                        print(
                            f"\n{checks_text} Codex finished reviewing {head['sha'][:10]} with no new feedback.\n"
                            f"Warning: {len(threads)} review thread(s) are still unresolved, so Codex "
                            "will not add its +1. Each has a reply or is outdated; resolve the ones "
                            "you have addressed:",
                            flush=True,
                        )
                        _print_threads(threads)
                        return 0
                _log(
                    logging.INFO,
                    "poll=%s exiting clean with codex review completed for head=%s",
                    poll_count,
                    head["sha"],
                )
                print(
                    f"\n{checks_text} Codex finished reviewing {head['sha'][:10]}. No review feedback.",
                    flush=True,
                )
                return 0

            if all_checks_pass and not new_feedback and codex_status == "ended":
                status = _review_part(codex_state, "code_review").get("status")
                _log(logging.INFO, "poll=%s exiting clean; codex review ended with status=%s", poll_count, status)
                print(
                    f"\nAll checks passed. Codex review of {head['sha'][:10]} ended with status "
                    f"{status!r} and no review feedback.",
                    flush=True,
                )
                return 0

            if all_checks_pass and not new_feedback and saw_plus_one:
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
            elif codex_status in ("reviewing", "completed") or (codex_status == "absent" and saw_eyes):
                _log(logging.INFO, "poll=%s sleeping %ss with ci done and codex reviewing", poll_count, POLL_INTERVAL)
                print(f"  [{mins}m] CI done; codex reviewing {head['sha'][:10]}...", flush=True)
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
