# scripts/transcript_post.py
"""Shared transcript downstream: classify -> front matter -> task-extract + qmd.

Used by every transcript provider (otter_sync, granola_sync) so the post-download
pipeline is byte-for-byte identical. Reads all paths from profile_lib."""
import logging, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_lib  # noqa: E402
import platform_lib  # noqa: E402

SCRIPT_DIR = Path(__file__).parent.resolve()


def _null_log():
    lg = logging.getLogger("transcript_post.null")
    lg.addHandler(logging.NullHandler())
    return lg


def _classify_fn():
    """Late import keeps otter_classify optional for environments without claude CLI."""
    from otter_classify import process_file
    return process_file


def _hook_env():
    return platform_lib.headless_claude_env()


def _run_qmd_index(env, log_dir, log):
    qmd = platform_lib.resolve_tool("qmd")
    if not qmd:
        log.info("  qmd not found — skipping index update (semantic search optional)")
        return
    try:
        subprocess.Popen([qmd, "update", "-c", "meetings_product"],
                         cwd=str(profile_lib.PM_OS_DIR), env=env,
                         stdout=open(log_dir / "qmd-index.log", "a"),
                         stderr=subprocess.STDOUT,
                         **platform_lib.process_group_kwargs())
        log.info("  Triggered qmd index update (meetings_product)")
    except Exception as exc:
        log.warning("  QMD index update hook failed: %s", exc)


def run_downstream(txt_path, item_id, state, log):
    """Classify the txt, record domain/final_path in state[item_id], fire the
    task-extract + qmd hooks. Returns final_path (or txt_path if classify absent).
    Mirrors the Otter post-write block exactly; provider-agnostic via item_id."""
    txt_path = Path(txt_path)
    final_path = str(txt_path)
    try:
        process_file = _classify_fn()
    except ImportError:
        log.warning("  otter_classify not available — skipping classification for %s", item_id)
        process_file = None
    if process_file:
        try:
            result = process_file(txt_path, speech_id=item_id, downloaded_state=state)
            state.setdefault(item_id, {})["domain"] = result["domain"]
            state[item_id]["final_path"] = str(result["final_path"])
            final_path = str(result["final_path"])
            log.info("  Classified -> %s", result["final_path"])
        except Exception as exc:
            log.warning("  Classification failed for %s: %s", item_id, exc)

    log_dir = Path(profile_lib.PM_OS_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = _hook_env()
    task_extract = str(SCRIPT_DIR / "task_extract_meetings.py")
    try:
        subprocess.Popen([sys.executable, task_extract, final_path],
                         cwd=str(profile_lib.PM_OS_DIR), env=env,
                         stdout=open(log_dir / "task-extract.log", "a"),
                         stderr=subprocess.STDOUT,
                         **platform_lib.process_group_kwargs())
        log.info("  Triggered task extraction for %s", final_path)
    except Exception as exc:
        log.warning("  Task extraction hook failed: %s", exc)
    _run_qmd_index(env, log_dir, log)
    return final_path
