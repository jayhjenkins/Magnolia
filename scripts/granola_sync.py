#!/usr/bin/env python3
"""Granola transcript sync — mirrors otter_sync.

Fetches new Granola meeting transcripts via headless `claude -p` + the claude.ai
Granola connector, dedups by meeting UUID in granola_downloaded.json, writes a
dated .txt into the profile meetings target, then runs the SHARED downstream
(transcript_post.run_downstream).

Fetch is two-phase so no single call has to carry many transcripts:
  1. `_list_meetings(since)`     one call, ids/titles/dates/attendees only.
  2. `_fetch_transcripts(batch)` BATCH_SIZE meetings per call, per-call timeout.
     The transcript text is read from the raw MCP tool_result in claude's
     stream-json output (verbatim, never paraphrased by the model); a
     model-echo fallback covers a meeting the stream did not yield.
A meeting that fails is parked in granola_status.json `pending` and retried on
later runs (given up after MAX_ATTEMPTS). Progress is persisted per meeting.

Lookback: the first sync (no prior success) covers FIRST_SYNC_DAYS; later syncs
resume from the last success. `--days N` overrides. Every run that lists
successfully - even an empty one - records `last_success` (the Doctor's signal).

Provider-gated: main() is a no-op unless transcript.provider == "granola"."""
import argparse, json, logging, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib            # noqa: E402
import platform_lib           # noqa: E402
import profile_lib            # noqa: E402
import transcript_post        # noqa: E402

DEFAULT_MODEL = "haiku"
FIRST_SYNC_DAYS = 3           # owner decision: a fresh install backfills 3 days
MAX_LOOKBACK_DAYS = 30
OVERLAP = timedelta(hours=24)  # re-list a day before the last success (late-finalised notes)
MAX_NEW_PER_RUN = 20          # transcripts fetched per run; the rest wait for the next run
BATCH_SIZE = 2                # meetings per transcript-fetch call
MAX_ATTEMPTS = 5              # per-meeting fetch attempts before giving up
LIST_TIMEOUT = 180            # seconds for the listing call
FETCH_TIMEOUT = 240           # seconds per transcript batch call
MCP_OUTPUT_TOKENS = "100000"  # raise claude's MCP tool-output cap so long transcripts aren't cut
TRANSCRIPT_TOOL = "mcp__claude_ai_Granola__get_meeting_transcript"
GRANOLA_TOOLS = ("mcp__claude_ai_Granola__list_meetings," + TRANSCRIPT_TOOL)
_REAUTH_HINTS = ("granola_unavailable", "not authenticated", "authenticate",
                 "unauthorized", "no such tool", "not connected", "mcp server")

log = logging.getLogger("granola_sync")


class GranolaError(Exception):
    """A run-level failure. kind: "needs_reauth" (connector missing/unauthorised)
    or "error" (anything else)."""
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


# ── paths / state ─────────────────────────────────────────────────────────────

def _state_dir(root=None):
    return profile_lib.transcript_state_dir(root)


def _meetings_dir(root=None):
    return Path(profile_lib.PM_OS_DIR) / profile_lib.transcript_config(root)["target"]


def _state_file(root=None):
    return Path(_state_dir(root)) / "granola_downloaded.json"


def _status_file(root=None):
    return Path(_state_dir(root)) / "granola_status.json"


def _lock_file(root=None):
    return Path(_state_dir(root)) / "granola_sync.lock"


def _model(root=None):
    return (profile_lib.config(root).get("models") or {}).get("granola_fetch") or DEFAULT_MODEL


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_state(root=None):
    return _read_json(_state_file(root))


def _save_state(state, root=None):
    _write_json(_state_file(root), state)


def _load_status(root=None):
    st = _read_json(_status_file(root))
    st.setdefault("pending", {})
    return st


def _save_status(status, root=None):
    _write_json(_status_file(root), status)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── naming ────────────────────────────────────────────────────────────────────

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


# ── lookback window ───────────────────────────────────────────────────────────

def _lookback_since(status, ledger, days=None, now=None):
    """Start of the listing window.

    --days wins. Otherwise resume from the last success (minus OVERLAP). A
    pre-status install falls back to its newest ledger download. With no history
    at all this is a first sync: FIRST_SYNC_DAYS. Never more than MAX_LOOKBACK_DAYS."""
    now = now or _now()
    if days is not None:
        return now - timedelta(days=days)
    anchor = _parse_dt(status.get("last_success"))
    if anchor is None and ledger:
        stamps = [_parse_dt((v or {}).get("downloaded_at")) for v in ledger.values()
                  if isinstance(v, dict)]
        stamps = [s for s in stamps if s]
        anchor = max(stamps) if stamps else None
    if anchor is None:
        return now - timedelta(days=FIRST_SYNC_DAYS)
    return max(anchor - OVERLAP, now - timedelta(days=MAX_LOOKBACK_DAYS))


# ── claude -p seam ────────────────────────────────────────────────────────────

def _cmd(prompt, root=None, max_turns=6, stream=False):
    model = _model(root)
    kw = dict(allowed_tools=GRANOLA_TOOLS, permission_mode="bypassPermissions",
              max_turns=max_turns)
    cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model, root=root, **kw)
    # Granola needs MCP; fall back to Claude if the active harness lacks it.
    if harness_lib.requires_claude_fallback(harness_name, requires_mcp=True):
        log.info("harness=%s but Granola needs MCP - falling back to Claude", harness_name)
        cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model, harness="claude", **kw)
    if stream and "--output-format" in cmd:
        i = cmd.index("--output-format")
        cmd[i + 1] = "stream-json"
        cmd.append("--verbose")
    return cmd, harness_name


def _run(cmd, timeout):
    """Run claude headless: utf-8 output, prompt via stdin for a .cmd shim,
    nested-session vars stripped, MCP output cap raised."""
    cmd, stdin = harness_lib.stdin_prompt(cmd)
    env = transcript_post._hook_env()
    env["MAX_MCP_OUTPUT_TOKENS"] = MCP_OUTPUT_TOKENS
    return subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout,
                          env=env, cwd=str(profile_lib.PM_OS_DIR),
                          **platform_lib.text_kwargs())


def _json_from_text(text, opener):
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _result_text(stdout):
    """The model's final text from `--output-format json` (or a bare payload)."""
    text = (stdout or "").strip()
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(outer, dict) and "result" in outer:
        return outer["result"]
    return outer


def _parse_fetch_output(stdout):
    """Find the JSON array in claude's output. Returns list, or None."""
    if not stdout:
        return None
    text = _result_text(stdout)
    if isinstance(text, list):
        return text
    if not isinstance(text, str):
        return None
    data = _json_from_text(text, "[")
    return data if isinstance(data, list) else None


def _looks_like_reauth(*texts):
    blob = " ".join(t for t in texts if isinstance(t, str)).lower()
    return any(h in blob for h in _REAUTH_HINTS)


def _parse_list_output(stdout, stderr=""):
    """List-call output -> list of meeting dicts. Raises GranolaError."""
    text = _result_text(stdout)
    if isinstance(text, str):
        obj = _json_from_text(text, "{")
        arr = _parse_fetch_output(stdout)
        if isinstance(obj, dict) and obj.get("error") and (
                arr is None or text.find("{") < text.find("[")):
            raise GranolaError("needs_reauth" if _looks_like_reauth(json.dumps(obj))
                               else "error", f"{obj.get('error')}: {obj.get('detail', '')}")
        if arr is not None:
            return arr
    elif isinstance(text, list):
        return text
    kind = "needs_reauth" if _looks_like_reauth(str(text), stderr) else "error"
    raise GranolaError(kind, "unparseable meeting list: %.200s" % (stderr or text or ""))


def _list_prompt(since, now):
    s, e = since.date().isoformat(), now.date().isoformat()
    return (
        "Use the Granola MCP tool list_meetings to list my meetings from "
        f"{s} through {e} (inclusive). Prefer a custom date range starting {s}; "
        "if the tool only offers preset ranges, use the smallest preset that covers "
        f"{s} onward. Do NOT fetch transcripts.\n"
        "Return STRICT JSON and nothing else: an array of objects with keys "
        '"id", "title", "created_at" (ISO 8601), "attendees" (list of names). '
        "If there are no meetings, return [].\n"
        "If the Granola tools are unavailable or not authorized, return "
        '{"error": "granola_unavailable", "detail": "<short reason>"} instead.'
    )


def _list_meetings(since, root=None):
    """THE listing seam: meeting metadata since `since` (no transcripts).
    One retry on malformed output. Raises GranolaError."""
    cmd, _ = _cmd(_list_prompt(since, _now()), root, max_turns=4)
    last = None
    for attempt in (1, 2):
        try:
            out = _run(cmd, LIST_TIMEOUT)
        except subprocess.TimeoutExpired:
            last = GranolaError("error", f"listing timed out after {LIST_TIMEOUT}s")
            log.warning("list attempt %d: %s", attempt, last)
            continue
        except OSError as exc:
            raise GranolaError("error", f"could not run claude: {exc}")
        try:
            return _parse_list_output(out.stdout, out.stderr)
        except GranolaError as exc:
            last = exc
            log.warning("list attempt %d failed (rc=%s): %s", attempt, out.returncode, exc)
            if exc.kind == "needs_reauth":
                break
    raise last


def _transcript_text(raw):
    """Normalise a tool_result payload into plain transcript text."""
    if isinstance(raw, list):
        raw = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if s.startswith("{") or s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return s
        if isinstance(data, dict):
            data = data.get("transcript", data.get("text", data))
        if isinstance(data, list):
            lines = []
            for u in data:
                if isinstance(u, dict):
                    who = u.get("speaker") or u.get("source") or ""
                    txt = u.get("text") or ""
                    lines.append(f"{who}: {txt}" if who else txt)
                else:
                    lines.append(str(u))
            return "\n".join(lines).strip()
        if isinstance(data, str):
            return data.strip()
    return s


def _parse_stream_transcripts(stdout):
    """Map meeting_id -> transcript from the raw get_meeting_transcript
    tool_results in claude's stream-json output (one JSON event per line)."""
    uses, got = {}, {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = ((ev.get("message") or {}).get("content")) if isinstance(ev, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and str(block.get("name", "")).endswith(
                    "get_meeting_transcript"):
                inp = block.get("input") or {}
                mid = inp.get("meeting_id") or inp.get("id") or next(
                    (v for v in inp.values() if isinstance(v, str)), None)
                if mid:
                    uses[block.get("id")] = mid
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                if block.get("is_error"):
                    continue
                text = _transcript_text(block.get("content"))
                if text:
                    got[uses[block["tool_use_id"]]] = text
    return got


def _batch_prompt(ids):
    return (
        "Use the Granola MCP. For EACH meeting id below, call "
        "get_meeting_transcript(meeting_id=<id>) exactly once. Do not summarise "
        "or repeat the transcripts. After the tool calls, reply with just DONE.\n"
        "Meeting ids: " + json.dumps(ids)
    )


def _echo_prompt(mid):
    return (
        f'Use the Granola MCP. Call get_meeting_transcript(meeting_id="{mid}"). '
        f'Return STRICT JSON and nothing else: {{"id": "{mid}", "transcript": '
        '"<the full transcript text, verbatim>"}. If the transcript is unavailable, '
        f'return {{"id": "{mid}", "transcript": null}}.'
    )


def _echo_fetch(mid, root=None):
    """Fallback for one meeting: ask the model to return the transcript as JSON."""
    cmd, _ = _cmd(_echo_prompt(mid), root, max_turns=3)
    out = _run(cmd, FETCH_TIMEOUT)
    text = _result_text(out.stdout)
    obj = _json_from_text(text, "{") if isinstance(text, str) else text
    if isinstance(obj, dict) and isinstance(obj.get("transcript"), str):
        return obj["transcript"].strip() or None
    return None


def _fetch_transcripts(batch, root=None):
    """THE transcript seam: {meeting_id: transcript} for the meetings in
    `batch` that could be fetched. Missing ids are treated as failures by the
    caller. May raise (timeout etc.) - the caller isolates the batch."""
    ids = [m["id"] for m in batch]
    cmd, _ = _cmd(_batch_prompt(ids), root, max_turns=2 * len(ids) + 2, stream=True)
    out = _run(cmd, FETCH_TIMEOUT)
    got = _parse_stream_transcripts(out.stdout)
    for mid in ids:
        if mid in got:
            continue
        try:
            text = _echo_fetch(mid, root)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("  echo fallback failed for %s: %s", mid, exc)
            continue
        if text:
            got[mid] = text
    return got


# ── write + downstream ────────────────────────────────────────────────────────

def _attendee_names(attendees):
    names = []
    for a in attendees or []:
        if isinstance(a, dict):
            a = a.get("name") or a.get("email") or ""
        a = str(a).strip()
        if a:
            names.append(a)
    return names


def _write_meeting(m, transcript, root=None):
    mid = m["id"]
    base, dt = _basename(m.get("created_at"), m.get("title"), mid)
    folder = _meetings_dir(root) / (dt.strftime("%Y-%m") if dt else "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    txt_path = folder / f"{base}.txt"
    header = f"# {m.get('title') or 'untitled'}\nDate: {m.get('created_at') or ''}\n"
    names = _attendee_names(m.get("attendees"))
    if names:
        header += "Attendees: " + ", ".join(names) + "\n"
    txt_path.write_text(header + "\n" + transcript, encoding="utf-8")
    return txt_path, folder


def _record_failure(status, m, reason, state, root=None):
    """Park a meeting for retry; give up (ledger it as skipped) after MAX_ATTEMPTS."""
    mid = m["id"]
    p = status["pending"].get(mid) or {k: m.get(k) for k in
                                        ("id", "title", "created_at", "attendees")}
    p["attempts"] = int(p.get("attempts") or 0) + 1
    p["last_error"] = str(reason)[:300]
    if p["attempts"] >= MAX_ATTEMPTS:
        status["pending"].pop(mid, None)
        state[mid] = {"title": m.get("title"), "downloaded_at": _iso(_now()),
                      "skipped": f"gave up after {p['attempts']} attempts: {p['last_error']}"}
        _save_state(state, root)
        log.warning("  giving up on %s after %d attempts", mid, p["attempts"])
    else:
        status["pending"][mid] = p


def _process_batch(batch, state, status, root=None):
    """Fetch one batch and land each meeting. Returns (new, failed)."""
    new = failed = 0
    try:
        got = _fetch_transcripts(batch, root)
        err = "transcript not returned"
    except Exception as exc:          # timeout / CLI crash: isolate to this batch
        log.warning("  batch %s failed: %s", [m["id"] for m in batch], exc)
        got, err = {}, exc
    for m in batch:
        mid = m["id"]
        text = got.get(mid)
        if not text:
            _record_failure(status, m, err, state, root)
            failed += 1
            continue
        try:
            txt_path, folder = _write_meeting(m, text, root)
        except OSError as exc:
            log.error("  Failed to write %s: %s", mid, exc)
            _record_failure(status, m, exc, state, root)
            failed += 1
            continue
        status["pending"].pop(mid, None)
        state[mid] = {"title": m.get("title"), "downloaded_at": _iso(_now()),
                      "folder": str(folder)}
        _save_state(state, root)               # per-meeting progress
        new += 1
        # Downstream is isolated: one bad meeting can't abort the loop (stays seen).
        try:
            final_path = transcript_post.run_downstream(txt_path, mid, state, log)
            state[mid]["final_path"] = str(final_path)
        except Exception as exc:
            log.error("  Downstream failed for %s: %s", mid, exc)
            new -= 1
        _save_state(state, root)
    _save_status(status, root)
    return new, failed


def _candidates(listed, state, status, since):
    out, seen = [], set()
    for m in listed or []:
        mid = m.get("id") if isinstance(m, dict) else None
        if not mid or mid in state or mid in seen:
            continue
        dt = _parse_dt(m.get("created_at"))
        if dt is not None and dt < since:
            continue
        seen.add(mid)
        out.append(m)
    for mid, p in list(status["pending"].items()):
        if mid in state:
            status["pending"].pop(mid, None)
        elif mid not in seen:
            seen.add(mid)
            out.append(dict(p, id=mid))
    return out


# ── entrypoint ────────────────────────────────────────────────────────────────

def _setup_logging():
    if not log.handlers:
        logging.basicConfig(level=logging.INFO,
            format="%(asctime)s  %(levelname)s  %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)])


def main(root=None, days=None):
    _setup_logging()
    # Provider gate — the Engine-tab switch.
    provider = profile_lib.transcript_config(root)["provider"]
    if provider != "granola":
        log.info("transcript provider is not granola (%s) — skipping", provider)
        return {"status": "skipped", "provider": provider}

    Path(_state_dir(root)).mkdir(parents=True, exist_ok=True)
    with open(_lock_file(root), "a+") as lock_fh:
        if not platform_lib.lock(lock_fh, blocking=False):
            log.info("another Granola sync is already running — skipping")
            return {"status": "skipped", "provider": "granola",
                    "reason": "another sync is already running"}
        try:
            return _sync(root, days)
        finally:
            platform_lib.unlock(lock_fh)


def _sync(root, days):
    state = _load_state(root)
    status = _load_status(root)
    if not _state_file(root).exists():
        _save_state(state, root)               # marker exists from the first run
    now = _now()
    since = _lookback_since(status, state, days=days, now=now)
    status["last_attempt"] = _iso(now)
    log.info("Listing Granola meetings since %s", since.date().isoformat())
    try:
        listed = _list_meetings(since, root)
    except GranolaError as exc:
        status["last_error"], status["error_kind"] = str(exc), exc.kind
        _save_status(status, root)
        log.error("Granola listing failed (%s): %s", exc.kind, exc)
        return {"status": "error", "provider": "granola", "error": str(exc),
                "error_kind": exc.kind}

    todo = _candidates(listed, state, status, since)
    deferred = max(0, len(todo) - MAX_NEW_PER_RUN)
    todo = todo[:MAX_NEW_PER_RUN]
    log.info("Granola listed %d meeting(s); %d to fetch%s", len(listed or []), len(todo),
             f" ({deferred} deferred to next run)" if deferred else "")

    new = failed = 0
    for i in range(0, len(todo), BATCH_SIZE):
        n, f = _process_batch(todo[i:i + BATCH_SIZE], state, status, root)
        new, failed = new + n, failed + f

    status["last_success"] = _iso(_now())
    status["last_error"] = None
    status["error_kind"] = None
    status["last_result"] = {"new": new, "failed": failed, "deferred": deferred}
    _save_status(status, root)
    log.info("Downloaded %d new Granola transcript(s); %d failed (will retry)", new, failed)
    return {"status": "ok", "provider": "granola", "new": new, "failed": failed}


def cli(argv=None):
    ap = argparse.ArgumentParser(description="Sync Granola meeting transcripts")
    ap.add_argument("--days", type=int, default=None,
                    help=f"look back N days (default: since last success; "
                         f"{FIRST_SYNC_DAYS} on the first sync)")
    args = ap.parse_args(argv)
    return main(days=args.days)


if __name__ == "__main__":
    print(cli())
