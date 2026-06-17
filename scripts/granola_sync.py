#!/usr/bin/env python3
"""Granola transcript sync — mirrors otter_sync.

Fetches new Granola meeting transcripts via headless `claude -p` + the Granola
MCP (the single mockable seam `_fetch_new_meetings`), dedups by meeting UUID in
granola_downloaded.json, writes a dated .txt into the profile meetings target,
then runs the SHARED Otter downstream (transcript_post.run_downstream).

Provider-gated: main() is a no-op unless transcript.provider == "granola", so the
Engine-tab provider selection is the on/off switch for the hourly LaunchAgent."""
import json, logging, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib           # noqa: E402
import profile_lib            # noqa: E402
import transcript_post        # noqa: E402

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_NEW_PER_RUN = 20
SEEN_IN_PROMPT = 200          # cap ledger ids interpolated into the fetch prompt
FETCH_TIMEOUT = 300           # seconds for the claude -p subprocess
GRANOLA_TOOLS = ("mcp__claude_ai_Granola__list_meetings,"
                 "mcp__claude_ai_Granola__get_meeting_transcript")

log = logging.getLogger("granola_sync")


def _state_dir(root=None):
    return profile_lib.transcript_state_dir(root)


def _meetings_dir(root=None):
    return Path(profile_lib.PM_OS_DIR) / profile_lib.transcript_config(root)["target"]


def _state_file(root=None):
    return Path(_state_dir(root)) / "granola_downloaded.json"


def _model(root=None):
    return (profile_lib.config(root).get("models") or {}).get("granola_fetch") or DEFAULT_MODEL


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name or "").strip()


def _basename(created_at, title, mid=None):
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        stamp = dt.strftime("%Y-%m-%d_%H-%M")
    except Exception:
        dt, stamp = None, "unknown"
    clean = re.sub(r"[_ ]{2,}", " ", safe_filename(title)).strip() or "untitled"
    # Suffix a short id slice so same minute+title across meetings can't collide.
    suffix = (mid or "")[:8]
    base = f"{stamp}_{clean}_{suffix}" if suffix else f"{stamp}_{clean}"
    return base, dt


def _load_state(root=None):
    f = _state_file(root)
    return json.loads(f.read_text()) if f.exists() else {}


def _save_state(state, root=None):
    f = _state_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2))


def _fetch_prompt(seen_ids):
    return (
        "Use the Granola MCP. Call list_meetings(time_range='last_30_days'). "
        "For EACH meeting whose id is NOT in this already-downloaded list, call "
        "get_meeting_transcript(meeting_id). Already downloaded ids: "
        + json.dumps(sorted(seen_ids)) + ". "
        f"Return at most {MAX_NEW_PER_RUN} meetings as STRICT JSON: a JSON array of "
        '{"id","title","created_at","attendees","transcript"} and NOTHING else. '
        "If a transcript is unavailable (e.g. plan restriction), omit that meeting. "
        "If there are no new meetings, return []."
    )


def _parse_fetch_output(stdout):
    """claude --output-format json wraps the result; the model's text is the
    payload. Find the JSON array. Returns list, or None if unparseable."""
    if not stdout:
        return None
    text = stdout.strip()
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            text = outer.get("result", text)
    except json.JSONDecodeError:
        pass
    if isinstance(text, list):
        return text
    if not isinstance(text, str):
        # e.g. {"result": null} / {"result": 42} / {"result": {...}}
        return None
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def _prompt_ids(state_or_ids):
    """Cap the ledger ids interpolated into the fetch prompt to a recent slice.
    The LOCAL post-fetch filter (using the FULL seen set) is the authoritative
    dedup — this only bounds prompt size. If we got a dict keyed by UUID with
    `downloaded_at`, take the most-recently-downloaded N; else a plain slice."""
    if isinstance(state_or_ids, dict):
        ordered = sorted(
            state_or_ids,
            key=lambda k: (state_or_ids.get(k) or {}).get("downloaded_at") or "",
            reverse=True,
        )
        return ordered[:SEEN_IN_PROMPT]
    return list(state_or_ids)[:SEEN_IN_PROMPT]


def _fetch_new_meetings(state_or_ids, root=None):
    """THE mockable seam. Shell out to claude -p + Granola MCP; return a list of
    new meeting dicts. Validates JSON; one retry on malformed; [] on hard failure.

    Accepts the full state dict (keyed by UUID) or a bare set/iterable of ids.
    `seen` (the FULL set) is the authoritative local dedup; the prompt only
    carries a bounded recent slice."""
    seen = set(state_or_ids)
    cmd = [platform_lib.resolve_claude(), "-p", _fetch_prompt(_prompt_ids(state_or_ids)),
           "--model", _model(root), "--output-format", "json",
           "--allowedTools", GRANOLA_TOOLS,
           "--permission-mode", "bypassPermissions", "--max-turns", "30"]
    env = transcript_post._hook_env()    # strips CLAUDECODE so nested claude -p runs
    for attempt in (1, 2):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=FETCH_TIMEOUT, env=env, cwd=str(profile_lib.PM_OS_DIR))
        except Exception as exc:
            log.error("claude -p fetch failed (attempt %d): %s", attempt, exc)
            continue
        meetings = _parse_fetch_output(out.stdout)
        if meetings is not None:
            return [m for m in meetings if m.get("id") not in seen and m.get("transcript")]
        log.warning("malformed fetch JSON (attempt %d): rc=%s stderr=%.500s",
                    attempt, out.returncode, (out.stderr or ""))
    return []


def main(root=None):
    if not log.handlers:
        logging.basicConfig(level=logging.INFO,
            format="%(asctime)s  %(levelname)s  %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)])
    # Provider gate — the Engine-tab switch.
    provider = profile_lib.transcript_config(root)["provider"]
    if provider != "granola":
        log.info("transcript provider is not granola (%s) — skipping", provider)
        return {"status": "skipped", "provider": provider}

    Path(_state_dir(root)).mkdir(parents=True, exist_ok=True)
    state = _load_state(root)
    meetings = _fetch_new_meetings(state, root=root)
    log.info("Granola returned %d new meeting(s)", len(meetings))

    new_count = 0
    for m in meetings:
        mid = m.get("id")
        if not mid or mid in state:
            continue
        base, dt = _basename(m.get("created_at"), m.get("title"), mid)
        folder = _meetings_dir(root) / (dt.strftime("%Y-%m") if dt else "unknown")
        folder.mkdir(parents=True, exist_ok=True)
        txt_path = folder / f"{base}.txt"
        header = f"# {m.get('title','untitled')}\nDate: {m.get('created_at','')}\n"
        if m.get("attendees"):
            header += "Attendees: " + ", ".join(str(a) for a in m["attendees"]) + "\n"
        try:
            txt_path.write_text(header + "\n" + (m.get("transcript") or ""), encoding="utf-8")
        except Exception as exc:
            log.error("  Failed to write %s: %s", mid, exc)
            continue            # do NOT mark seen -> retried next run
        state[mid] = {"title": m.get("title"), "downloaded_at": datetime.now().isoformat(),
                      "folder": str(folder)}
        # Already marked seen + the .txt write succeeded; isolate downstream so one
        # bad meeting can't abort the loop. On error, log and continue (stays seen).
        try:
            final_path = transcript_post.run_downstream(txt_path, mid, state, log)
            state[mid]["final_path"] = str(final_path)
            _save_state(state, root)
        except Exception as exc:
            log.error("  Downstream failed for %s: %s", mid, exc)
            _save_state(state, root)
            continue
        new_count += 1

    log.info("Downloaded %d new Granola transcript(s)", new_count)
    return {"status": "ok", "provider": "granola", "new": new_count}


if __name__ == "__main__":
    print(main())
