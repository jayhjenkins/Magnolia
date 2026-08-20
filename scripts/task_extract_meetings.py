#!/usr/bin/env python3
"""Extract tasks from meeting transcripts via headless claude.

Python port of task-extract-meetings.sh (the .sh is now a thin shim over this).
Invoked by transcript_post.run_downstream via sys.executable so the engine never
shells Python->bash->python. Path math uses pathlib so Windows drive-absolute
paths (C:\\...) are handled natively — no more PM_OS_DIR doubling.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib  # noqa: E402
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402
import task_lib  # noqa: E402

PM_OS_DIR = Path(__file__).resolve().parent.parent
_DISPATCHABLE_QUEUES = ("agent", "collab")
PROCESSED_FILE = PM_OS_DIR / "datasets" / "tasks" / "_processed-meetings.txt"

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def resolve_path(filepath):
    """Resolve a transcript path to absolute. Relative paths are taken from
    PM_OS_DIR; absolute paths (POSIX / or a Windows drive C:\\) are used as-is.
    The Windows-drive check makes this correct even when run on POSIX."""
    p = Path(filepath)
    if not p.is_absolute() and not _WIN_DRIVE.match(str(filepath)):
        p = Path(PM_OS_DIR) / p
    return p.resolve()


def _normalized_key(file):
    return str(file).replace("\\", "/").split("datasets/meetings/")[-1]


def is_processed(file):
    if not Path(PROCESSED_FILE).exists():
        return False
    key = _normalized_key(file)
    return key in Path(PROCESSED_FILE).read_text(encoding="utf-8")


def mark_processed(file):
    Path(PROCESSED_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(PROCESSED_FILE).touch(exist_ok=True)
    if not is_processed(file):
        with open(PROCESSED_FILE, "a", encoding="utf-8") as fh:
            fh.write(str(file) + "\n")


def _prompt(filepath):
    return f"""Read the meeting transcript at {filepath}.

BEFORE extracting tasks, run: ./scripts/task.sh list --json
This gives you all existing open tasks. For EACH potential new task, check if a semantically similar task already exists (same underlying work, even if worded differently). If a duplicate exists:
- Do NOT create a new task
- Instead, run: ./scripts/task.sh update TASK-NNNN --comment "Additional context from {filepath}: <new details>"
- Append-only: add new context, never remove existing context
- If priority should escalate, update that too

Only create a new task if no existing task covers the same work.

Use the task-extract-from-meeting skill at .claude/skills/task-extract-from-meeting/SKILL.md to identify action items and create tasks. For each action item, use ./scripts/task.sh add with appropriate --queue, --priority, --domain, --source-meeting flags. Apply the auto-queue rules: human decisions -> human queue, autonomous work -> agent queue, joint work -> collab queue, delegated to others -> waiting queue."""


def _snapshot_task_ids():
    """Return the set of task IDs currently in dispatchable queues."""
    ids = set()
    for q in _DISPATCHABLE_QUEUES:
        for t in task_lib.list_tasks(queue=q):
            ids.add(t.get("id"))
    return ids


def _dispatch_new_tasks(before_ids):
    """Dispatch any tasks in agent/collab that weren't there before extraction."""
    dispatch_script = str(PM_OS_DIR / "scripts" / "task_dispatch.py")
    env = platform_lib.headless_claude_env()
    dispatched = 0
    for q in _DISPATCHABLE_QUEUES:
        for t in task_lib.list_tasks(queue=q, status="open"):
            tid = t.get("id")
            if tid and tid not in before_ids and not t.get("agent_status"):
                try:
                    subprocess.Popen(
                        [sys.executable, dispatch_script, "--task", tid],
                        cwd=str(PM_OS_DIR), env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        **platform_lib.process_group_kwargs(),
                    )
                    dispatched += 1
                    print(f"[DISPATCH] {tid}")
                except Exception as exc:
                    print(f"[DISPATCH-FAIL] {tid}: {exc}")
    return dispatched


def process_transcript(filepath):
    target = resolve_path(filepath)
    if not target.is_file():
        print(f"[ERROR] File not found: {target}")
        return 1
    relative = str(target).replace(str(Path(PM_OS_DIR)) + os.sep, "")
    relative = relative.replace("\\", "/")
    if is_processed(relative):
        print(f"[SKIP] Already processed: {relative}")
        return 0
    print(f"[PROCESSING] {relative}")

    before_ids = _snapshot_task_ids()

    model = profile_lib.resolve_model("standard")
    cmd, harness_name = harness_lib.build_oneshot_cmd(
        _prompt(target), model,
        allowed_tools="Bash(*),Read(*),Write(*)",
        max_turns=20,
    )
    env = platform_lib.headless_harness_env(harness_name)
    with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
        err_path = tf.name
    try:
        with open(err_path, "w") as errf:
            result = subprocess.run(
                cmd,
                cwd=str(PM_OS_DIR), env=env, stderr=errf,
            )
        stderr_text = Path(err_path).read_text(encoding="utf-8", errors="ignore")
        if result.returncode == 0:
            if "cannot be launched inside another Claude Code session" in stderr_text:
                print(f"[ERROR] Nested Claude session detected for: {relative} (not marking as processed)")
                sys.stderr.write(stderr_text)
            else:
                mark_processed(relative)
                dispatched = _dispatch_new_tasks(before_ids)
                print(f"[DONE] {relative} ({dispatched} task(s) dispatched)")
        else:
            print(f"[ERROR] claude exited non-zero for: {relative} (not marking as processed)")
            sys.stderr.write(stderr_text)
    finally:
        Path(err_path).unlink(missing_ok=True)
    return 0


def main(argv):
    if not argv:
        print("Usage: task_extract_meetings.py <transcript-path> | --all-unprocessed")
        return 1
    if argv[0] == "--all-unprocessed":
        meetings = Path(PM_OS_DIR) / "datasets" / "meetings"
        files = sorted([p for p in meetings.rglob("*") if p.suffix in (".md", ".txt")])
        if not files:
            print(f"[INFO] No .md or .txt files found under {meetings}")
        for f in files:
            process_transcript(str(f))
        return 0
    return process_transcript(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
