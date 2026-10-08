#!/usr/bin/env python3
"""Extract tasks from meeting transcripts via headless claude.

Python port of task-extract-meetings.sh (the .sh is now a thin shim over this).
Invoked by transcript_post.run_downstream via sys.executable so the engine never
shells Python->bash->python. Path math uses pathlib so Windows drive-absolute
paths (C:\\...) are handled natively — no more PM_OS_DIR doubling.
"""
import json
import os
import re
import subprocess
import sys
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


def _task_cmd():
    """Shell-ready invocation of the task CLI with the RUNNING interpreter.

    The task-extract skill's examples use the bash wrapper, which is fragile on
    Windows, so the prompt points headless Claude at the Python CLI the wrapper
    wraps. Forward slashes + double quotes survive both a POSIX shell and
    cmd.exe, and paths with spaces (C:/Program Files/...)."""
    py = Path(sys.executable).as_posix()
    cli = (Path(PM_OS_DIR) / "scripts" / "task_cli.py").as_posix()
    return f'"{py}" "{cli}"'


def _prompt(filepath):
    task = _task_cmd()
    return f"""Read the meeting transcript at {filepath}.

TASK CLI: every task command below runs as `{task} <subcommand> ...`. Wherever the skill's examples show the scripts/task wrapper, run `{task}` instead with the same arguments.

BEFORE extracting tasks, run: {task} list --json
This gives you all existing open tasks. For EACH potential new task, check if a semantically similar task already exists (same underlying work, even if worded differently). If a duplicate exists:
- Do NOT create a new task
- Instead, run: {task} update TASK-NNNN --comment "Additional context from {filepath}: <new details>"
- Append-only: add new context, never remove existing context
- If priority should escalate, update that too

Only create a new task if no existing task covers the same work.

Use the task-extract-from-meeting skill at .claude/skills/task-extract-from-meeting/SKILL.md to identify action items and create tasks. For each action item, use {task} add with appropriate --queue, --priority, --domain, --source-meeting flags. Apply the auto-queue rules: human decisions -> human queue, autonomous work -> agent queue, joint work -> collab queue, delegated to others -> waiting queue.

When finished, end your reply with exactly one status line:
EXTRACT_STATUS: OK  (every task command you ran succeeded, or there was nothing to create)
EXTRACT_STATUS: FAILED <reason>  (any task command failed or the transcript could not be read)"""


def _extraction_failure(stdout):
    """Reason the headless extraction failed, or None if it succeeded.

    Reads claude's --output-format json result: an error result (is_error / a
    non-success subtype such as error_max_turns) or a reported
    EXTRACT_STATUS: FAILED line means tasks may not have been created. Output
    that is not claude's JSON (e.g. another harness) is judged by exit code."""
    try:
        data = json.loads((stdout or "").strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        return f"claude result {data.get('subtype') or 'error'}"
    m = re.search(r"EXTRACT_STATUS:\s*FAILED\b(.*)", str(data.get("result") or ""))
    if m:
        return "task creation failed:" + m.group(1)[:200]
    return None


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
    cmd, prompt_stdin = harness_lib.stdin_prompt(cmd)
    result = subprocess.run(
        cmd, cwd=str(PM_OS_DIR), env=env, input=prompt_stdin,
        capture_output=True, **platform_lib.text_kwargs(),
    )
    stderr_text = result.stderr or ""
    if result.returncode != 0:
        print(f"[ERROR] claude exited non-zero for: {relative} (not marking as processed)")
        sys.stderr.write(stderr_text)
        return 1
    if "cannot be launched inside another Claude Code session" in stderr_text:
        print(f"[ERROR] Nested Claude session detected for: {relative} (not marking as processed)")
        sys.stderr.write(stderr_text)
        return 1
    failure = _extraction_failure(result.stdout)
    if failure:
        print(f"[ERROR] {failure} for: {relative} (not marking as processed)")
        return 1
    mark_processed(relative)
    dispatched = _dispatch_new_tasks(before_ids)
    print(f"[DONE] {relative} ({dispatched} task(s) dispatched)")
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
