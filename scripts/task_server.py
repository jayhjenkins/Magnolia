#!/usr/bin/env python3
"""
task_server.py — Local HTTP server for PM-OS task management.

Serves the task board web UI and exposes a JSON API backed by task_lib.py.
Runs on the configured server port (default 8742) using only Python stdlib.

API endpoints:
  GET  /api/tasks            — List non-archived tasks (filterable)
  GET  /api/tasks/{id}       — Full task detail with parsed activity log
  POST /api/tasks/{id}/done  — Mark task complete, move to archive
  POST /api/tasks/{id}/comment — Append activity log entry
"""

import json
import os
import re
import socket
import shlex
import subprocess
import sys
import traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 100

# Add script directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_lib
import chat_runner
import chat_transcript
import ladder_lib
import cron_lib
import program_lib
import jira_publish
import profile_lib
import packs_lib
import platform_lib
import adaptations_lib
import adapt_runner
import adapt_transcript
import live_runs
import adapters
from adapters.project_management._contract import NotConfigured
from adapters import NeedsConfirmation
from cron_scheduler import CronScheduler
from cadence.scheduler import CadenceScheduler
import shipper
from shipper import (
    _message_draft_from_task, _attempt_send_message, _record_manual_send,
    _attempt_publish, _emit_confirm_card, _note, _load_email_cache,
)

# ─── Chat concurrency ────────────────────────────────────────────────────────
# A session must never have two concurrent chat runs. This guarantee — and the
# survive-disconnect substrate — is now provided by live_runs: a chat run is
# keyed by `chat:<task_id>`, a second concurrent POST while that key is live
# returns 409, and a client disconnect only stops the SSE tail (never the run).
# See handle_chat below. (The old in-memory _CHAT_RUNS lock was removed when this
# moved onto live_runs — it had no other callers.)
def _chat_run_key(task_id):
    """live_runs key for a task's chat run. Namespaced to avoid adapt-key collision."""
    return "chat:%s" % (task_id,)


# ─── Load LangFuse env vars if not already set ───────────────────────────────
_PM_OS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_langfuse = os.path.join(_PM_OS, ".env.langfuse")
if not os.environ.get("LANGFUSE_SECRET_KEY") and os.path.isfile(_env_langfuse):
    with open(_env_langfuse) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _line = _line.removeprefix("export ")
                _key, _, _val = _line.partition("=")
                os.environ[_key.strip()] = _val.strip()

# ─── Constants ────────────────────────────────────────────────────────────────

PORT = profile_lib.server_port()
PM_OS_DIR = _PM_OS
UI_DIR = os.path.join(PM_OS_DIR, "ui", "task-board")

# Regex for parsing activity log entries:
#   ### 2026-02-26T12:00:00Z — human [comment]
#   The comment text here.
ACTIVITY_LOG_RE = re.compile(
    r"^###\s+(?P<timestamp>\S+)\s+[—-]+\s+(?P<actor>\S+)(?:\s+\[(?P<type>[^\]]+)\])?\s*\n(?P<content>(?:(?!^###\s).*\n?)*)",
    re.MULTILINE,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _json_response(handler, data, status=200):
    """Write a JSON HTTP response with CORS headers."""
    body = json.dumps(data, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _error_response(handler, message, status=400):
    """Write a JSON error response."""
    _json_response(handler, {"error": message}, status=status)


def _sse_begin(handler):
    """Send the SSE response headers (200 + text/event-stream, unbuffered)."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    # The server is HTTP/1.1 (keep-alive by default) and an SSE body has no
    # Content-Length, so a browser fetch() reader would never see end-of-stream
    # on a kept-alive connection — the read loop hangs and the composer never
    # re-enables. Force the connection to close after this response so the
    # client's stream terminates cleanly. (The client also breaks on our
    # explicit `event: done` sentinel, belt-and-suspenders.)
    handler.send_header("Connection", "close")
    handler.close_connection = True
    handler.send_header("X-Accel-Buffering", "no")
    # NOTE: do NOT send Access-Control-Allow-Origin here — the handler's
    # overridden end_headers() injects it into every response. Sending it
    # explicitly would duplicate the header on SSE responses.
    handler.end_headers()


def _sse_send(handler, obj):
    """Write one SSE data frame and flush so the client sees it immediately."""
    payload = json.dumps(obj, default=str)
    handler.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
    handler.wfile.flush()


def _sse_end(handler):
    """Write a terminal sentinel so the client knows the stream is complete."""
    handler.wfile.write(b"event: done\ndata: {}\n\n")
    handler.wfile.flush()


def _read_request_body(handler):
    """Read and parse JSON request body."""
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _resolve_output_path(rel):
    """Resolve a task's agent_output to an absolute .md path inside PM_OS_DIR.

    Returns the absolute path, or None when there is no path, it is not a .md
    file, or it would escape PM_OS_DIR (path-traversal guard). Mirrors
    handle_open_file's PM_OS_DIR resolution, plus the containment check.
    """
    rel = (rel or "").strip()
    if not rel or not rel.endswith(".md"):
        return None
    base = os.path.realpath(PM_OS_DIR)
    candidate = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(base, rel))
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


def _parse_activity_log(body):
    """Parse the activity log section from a task's markdown body.

    Each entry follows:
        ### {ISO-timestamp} — {actor} [{type}]
        {content}

    Returns a list of dicts with keys: timestamp, actor, type, content.
    """
    entries = []
    for match in ACTIVITY_LOG_RE.finditer(body):
        entries.append({
            "timestamp": match.group("timestamp"),
            "actor": match.group("actor"),
            "type": match.group("type"),
            "content": match.group("content").strip(),
        })
    return entries


def _parse_task_id(path_segment):
    """Validate and normalize a task ID from a URL path segment.

    Accepts 'TASK-0001' format. Returns the ID string or None if invalid.
    """
    segment = path_segment.strip("/")
    if re.match(r"^TASK-\d{4,}$", segment):
        return segment
    return None


# ─── API Route Handlers ──────────────────────────────────────────────────────

def _enrich_sharepoint_url(task_dict):
    """Add sharepoint_url to a task dict if missing. Tries sharepoint_path first, then agent_output."""
    if task_dict.get("sharepoint_url"):
        return task_dict
    # Try from existing sharepoint_path (local docx path)
    if task_dict.get("sharepoint_path"):
        url = task_lib._sharepoint_url_from_docx(str(task_dict["sharepoint_path"]))
        if url:
            task_dict["sharepoint_url"] = url
            return task_dict
    # Fall back to computing from agent_output (markdown path)
    if task_dict.get("agent_output"):
        url = task_lib._sharepoint_url(str(task_dict["agent_output"]))
        if url:
            task_dict["sharepoint_url"] = url
    return task_dict


def handle_list_tasks(handler, query_params):
    """GET /api/tasks — Return all non-archived tasks as JSON."""
    queue = query_params.get("queue", [None])[0]
    status = query_params.get("status", [None])[0]
    domain = query_params.get("domain", [None])[0]
    priority = query_params.get("priority", [None])[0]

    try:
        tasks = task_lib.list_tasks(
            queue=queue,
            status=status,
            domain=domain,
            priority=priority,
            include_archive=False,
        )
        # Adaptation liveness seam: hide cards whose card-type is owned by an
        # adaptation that is currently off. Core/unowned types (task, receipt,
        # recommendation, ...) are never in a manifest, so is_live returns True
        # for them and they always show. Cache by distinct card_type so each type
        # is checked once: is_live scans the store per call (avoid O(tasks x adapt)).
        _live = {}

        def _card_live(card_type):
            if card_type not in _live:
                _live[card_type] = adaptations_lib.is_live("card-type", card_type)
            return _live[card_type]

        tasks = [t for t in tasks if _card_live(t.get("card_type") or "task")]
        for t in tasks:
            _enrich_sharepoint_url(t)
        _json_response(handler, tasks)
    except Exception as e:
        _error_response(handler, f"Failed to list tasks: {e}", status=500)


def handle_list_activity(handler, query_params):
    """GET /api/activity — Return archived (completed/cancelled) tasks as JSON."""
    raw_limit = query_params.get("limit", ["200"])[0]
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 200

    try:
        tasks = task_lib.list_archived(limit=limit)
        for t in tasks:
            _enrich_sharepoint_url(t)
        _json_response(handler, tasks)
    except Exception as e:
        _error_response(handler, f"Failed to list activity: {e}", status=500)


# ─── Quality (shadow judge) ───────────────────────────────────────────────────

JUDGE_GOOD_THRESHOLD = 7  # judge_score >= this is "positive"; mirrors human 👍


def _task_type_of(task):
    """The scoreboard's grouping unit: explicit task_type, else domain."""
    return task.get("task_type") or task.get("domain") or "uncategorized"


def build_quality(ladder_path=None):
    """Pure shadow-judge scoreboard aggregation, sourced from frontmatter.

    Aggregates judged tasks (those with judge_score) into a row per task-type:
    count, average score, trend, per-dimension averages, judge↔human agreement %
    (from each task's human_react frontmatter), and the ladder trust-tier label.
    Also returns the disagreement list (judge vs. human divergences). No LangFuse
    dependency for agreement; the langfuse flag is still surfaced for the UI.
    """
    active = task_lib.list_tasks()
    archived = task_lib.list_archived(limit=1000)
    judged = [t for t in (active + archived) if t.get("judge_score") is not None]
    groups, disagreements = {}, []
    for t in judged:
        gkey = _task_type_of(t)
        g = groups.setdefault(gkey, {"task_type": gkey, "count": 0, "scores": [],
                                     "scored_at": [], "dimensions": {},
                                     "agree": 0, "disagree": 0})
        try:
            score = float(t["judge_score"])
        except (TypeError, ValueError):
            continue
        g["count"] += 1
        g["scores"].append(score)
        g["scored_at"].append(t.get("judge_scored_at") or "")
        dims = t.get("judge_dimensions") or {}
        if isinstance(dims, dict):
            for k, v in dims.items():
                if v is not None:
                    try:
                        g["dimensions"].setdefault(k, []).append(float(v))
                    except (TypeError, ValueError):
                        pass
        # Agreement: compare the human 👍/👎 (from frontmatter) to the judge.
        react = t.get("human_react")
        if react in ("up", "down"):
            human_positive = react == "up"
            judge_positive = score >= JUDGE_GOOD_THRESHOLD
            if human_positive == judge_positive:
                g["agree"] += 1
            else:
                g["disagree"] += 1
                disagreements.append({
                    "task_id": t["id"], "title": t.get("title", ""), "task_type": gkey,
                    "judge_score": score, "judge_why": t.get("judge_why", ""),
                    "human_value": 1 if human_positive else 0,
                    "human_comment": t.get("human_react_note", "")})

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    rows = []
    for g in groups.values():
        scores = g["scores"]
        order = sorted(range(len(scores)), key=lambda i: g["scored_at"][i])
        ordered = [scores[i] for i in order]
        trend = None
        if len(ordered) >= 2:
            mid = len(ordered) // 2
            older, newer = ordered[:mid], ordered[mid:]
            if older and newer:
                trend = round((sum(newer) / len(newer)) - (sum(older) / len(older)), 1)
        reacted = g["agree"] + g["disagree"]
        rows.append({
            "task_type": g["task_type"], "count": g["count"], "avg_score": avg(scores),
            "trend": trend, "history": [round(v, 1) for v in ordered[-8:]],
            "phase": ladder_lib.tier_of(g["task_type"], path=ladder_path),
            "dimensions": {k: avg(v) for k, v in g["dimensions"].items()},
            "agreement_pct": round(100 * g["agree"] / reacted) if reacted else None,
            "reacted": reacted})
    rows.sort(key=lambda r: r["count"], reverse=True)
    disagreements.sort(key=lambda d: d["judge_score"])
    return {"groups": rows, "disagreements": disagreements,
            "total_judged": len(judged), "langfuse": _get_langfuse() is not None}


# ─── Profile / Config room (GET /api/profile) ──────────────────────────────────

# Known adapters per integration category, keyed by the output category name.
# Labels are human-readable; ids match provider strings in integrations.yaml.
_INTEGRATION_OPTIONS = {
    "transcripts": ("Transcripts", [("otter", "Otter.ai"), ("granola", "Granola")]),
    "project_management": ("Project Management",
                           [("jira", "Jira"), ("asana", "Asana"), ("linear", "Linear")]),
    "calendar": ("Calendar", [("m365", "Microsoft 365"), ("google", "Google Calendar")]),
}

# Doctor capability-status vocabulary -> Profile room frontend vocabulary.
# The Doctor writes a rich set of statuses; the frontend (profile.js) only
# keys its dots/buttons/degraded-lock off {ok, reauth, available, unset}.
# build_profile owns the /api/profile contract, so it normalizes here.
# Any unrecognized value falls back to "unset" (safe default).
_CAP_STATUS_TO_FRONTEND = {
    "ok": "ok",
    "running": "ok",
    "needs_reauth": "reauth",   # works-but-needs-attention -> surface re-auth nudge
    "degraded": "reauth",
    "missing": "unset",
    "down": "unset",
    "not_expected": "unset",
    "unknown": "unset",
}

# Output category -> integrations.yaml key (transcripts is singular on disk).
_INTEGRATION_SOURCE_KEY = {
    "transcripts": "transcript",
    "project_management": "project_management",
    "calendar": "calendar",
}


def _profile_workers(root=None):
    """Read scripts/workers/*.md frontmatter; surface any that declare a
    model/tier. Returns [] when none do — never fabricates a tier."""
    workers = []
    workers_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers")
    if not os.path.isdir(workers_dir):
        return workers
    for fname in sorted(os.listdir(workers_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(workers_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        fm = text[3:end]
        name = None
        tier = None
        for line in fm.splitlines():
            m = re.match(r"\s*(name|model|tier)\s*:\s*(.+?)\s*$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if key == "name":
                name = val
            elif key in ("model", "tier"):
                tier = val
        if tier is not None:
            workers.append({"name": name or os.path.splitext(fname)[0], "tier": tier})
    return workers


def build_profile(root=None):
    """Pure assembler for the Profile/Config room (GET /api/profile).

    FIVE sections, no system-status section: identity, integrations, voice
    (two channels), skill packs, model posture. Reads everything through
    profile_lib; integration option status derives from the Doctor's
    capabilities.json when present (normalized to the frontend vocabulary
    {ok, reauth, available, unset} via _CAP_STATUS_TO_FRONTEND), else "ok"
    for the active provider and "available" for the rest. Read-only.
    """
    prof = profile_lib.profile(root)
    cfg = profile_lib.config(root)
    integ = profile_lib.integrations(root)
    caps = (profile_lib.read_capabilities(root) or {}).get("capabilities", {}) or {}

    identity = {
        "name": prof.get("display_name") or "Operator",
        "email": prof.get("email", ""),
        "company": prof.get("company", ""),
        "timezone": prof.get("timezone", ""),
    }

    integrations = {}
    for out_key, (label, options) in _INTEGRATION_OPTIONS.items():
        src_key = _INTEGRATION_SOURCE_KEY[out_key]
        active = (integ.get(src_key) or {}).get("provider") or "none"
        opts = []
        for opt_id, opt_label in options:
            # Resolve the capability entry. Per-provider key first (covers
            # jira/m365 and any future per-provider key). If absent AND this is
            # the active provider, fall back to the category-keyed entry — the
            # Doctor keys some capabilities (e.g. transcripts) by category name
            # and only probes the active provider (see doctor.py probe_transcript).
            cap = caps.get(opt_id)
            if not cap and opt_id == active:
                cap = caps.get(src_key)
            if cap and cap.get("status"):
                status = _CAP_STATUS_TO_FRONTEND.get(cap["status"], "unset")
            elif opt_id == active:
                status = "ok"
            else:
                status = "available"
            opts.append({"id": opt_id, "label": opt_label,
                         "status": status, "detail": (cap or {}).get("detail", "")})
        integrations[out_key] = {"label": label, "active": active, "options": opts}

    voice = {
        "teams": profile_lib.voice_text("teams", root),
        "email": profile_lib.voice_text("email", root),
    }

    packs = {
        "active": cfg.get("active_skill_packs") or [],
        "available": packs_lib.pack_catalog(root),
    }

    posture_level = (cfg.get("models") or {}).get("cost_posture") or "balanced"
    workers = _profile_workers(root)
    for w in workers:
        w["model"] = profile_lib.resolve_model(w.get("tier"), posture=posture_level)
    model_posture = {"level": posture_level, "workers": workers}

    return {
        "identity": identity,
        "integrations": integrations,
        "voice": voice,
        "packs": packs,
        "model_posture": model_posture,
    }


def _worker_packs(worker_skills, packs=None, root=None):
    """Pack ids whose skill set intersects the worker's (flat) skill names.
    [] when no manifest or no intersection."""
    if packs is None:
        packs = packs_lib.load_packs(root)
    sk = set(worker_skills or [])
    return [pid for pid, spec in packs.items() if sk & set(spec.get("skills", []))]


def workers_payload(posture=None):
    """Enriched, read-only worker list for GET /api/workers: file truth from
    scripts/workers/*.md plus Phase-7 tier, resolved model at the current cost
    posture, and pack membership. Degrades: missing tier -> resolve_model
    defaults to standard; missing packs.yaml -> packs == []."""
    from task_dispatch import load_workers
    if posture is None:
        posture = profile_lib.cost_posture()
    packs = packs_lib.load_packs()
    out = []
    for w in load_workers():
        tier = w.get("tier")
        out.append({
            "name": w.get("name", ""),
            "description": w.get("description", ""),
            "priority": w.get("priority", 0),
            "match": w.get("match", {}),
            "allowed_tools": w.get("allowed_tools", []),
            "skills": w.get("skills", []),
            "langfuse_prompt": w.get("langfuse_prompt", ""),
            "timeout": w.get("timeout", 600),
            "max_turns": w.get("max_turns", 30),
            "prompt_body": w.get("prompt_body", ""),
            "tier": tier,
            "model": profile_lib.resolve_model(tier, posture=posture),
            "packs": _worker_packs(w.get("skills", []), packs=packs),
        })
    return out


# ─── Profile write endpoints (pure helpers + HTTP wrappers) ────────────────────
# The pure helpers take the parsed payload + root and return (status_code, body).
# They own validation (path-traversal guards against the un-sanitizing profile_lib
# setters) BEFORE persisting. The thin handle_* wrappers read the body and emit.

_VOICE_CHANNELS = {"teams", "email"}
_INTEGRATION_CATEGORIES = set(_INTEGRATION_SOURCE_KEY)   # transcripts/project_management/calendar
_MODEL_POSTURE_LEVELS = {"low", "balanced", "high"}


def apply_profile_identity(payload, root=None):
    """Write the four known identity fields to profile.yaml. Unknown keys dropped."""
    data = {}
    for out_key, disk_key in (("name", "display_name"), ("email", "email"),
                              ("company", "company"), ("timezone", "timezone")):
        if out_key in payload:
            data[disk_key] = payload[out_key]
    if not data:
        return 400, {"error": "No identity fields provided"}
    profile_lib.write_identity(data, root=root)
    return 200, {"ok": True, "identity": data}


def apply_profile_voice(payload, root=None):
    """Write voice channel file(s). Channel keys validated against {teams, email}
    BEFORE any write (path-traversal guard); reject unknown -> 400, write nothing."""
    channels = payload
    if not channels:
        return 400, {"error": "No voice channels provided"}
    unknown = [k for k in channels if k not in _VOICE_CHANNELS]
    if unknown:
        return 400, {"error": f"Unknown voice channel(s): {', '.join(sorted(unknown))}"}
    for channel, text in channels.items():
        profile_lib.write_voice(channel, text if text is not None else "", root=root)
    return 200, {"ok": True, "channels": sorted(channels)}


def apply_profile_integration(category, payload, root=None):
    """Set the provider for an integration category. The category is validated
    against the known output keys BEFORE mapping to the on-disk key (transcripts
    -> transcript) and calling the un-sanitizing setter."""
    if category not in _INTEGRATION_CATEGORIES:
        return 400, {"error": f"Unknown integration category: {category}"}
    active = payload.get("active")
    if not active or not isinstance(active, str):
        return 400, {"error": "Missing 'active' provider id"}
    disk_key = _INTEGRATION_SOURCE_KEY[category]
    profile_lib.set_integration_provider(disk_key, active, root=root)
    return 200, {"ok": True, "category": category, "active": active}


def apply_profile_packs(payload, root=None):
    """Set the active skill packs list."""
    active = payload.get("active")
    if not isinstance(active, list):
        return 400, {"error": "'active' must be a list of pack ids"}
    if not all(isinstance(p, str) for p in active):
        return 400, {"error": "'active' must contain only pack id strings"}
    profile_lib.set_active_packs(active, root=root)
    return 200, {"ok": True, "active": list(active)}


def apply_profile_model_posture(payload, root=None):
    """Set the model cost posture. Level validated against {low, balanced, high}."""
    level = payload.get("level")
    if level not in _MODEL_POSTURE_LEVELS:
        return 400, {"error": f"Invalid model posture level: {level!r}"}
    profile_lib.set_cost_posture(level, root=root)
    return 200, {"ok": True, "level": level}


def _respond_apply(handler, fn, *args):
    """Run a pure apply_* helper inside try/except and emit its (status, body).

    The helper writes to disk via profile_lib, so a permission error / disk-full /
    YAML round-trip failure can raise. Catch it and emit a clean JSON 500 (matching
    handle_update_description / handle_update_message) instead of letting the
    exception propagate into a dropped connection + server traceback."""
    try:
        status, body = fn(*args)
    except Exception as e:
        _error_response(handler, f"Failed to update profile: {e}", status=500)
        return
    if status != 200:
        _error_response(handler, body.get("error", "Bad request"), status=status)
    else:
        _json_response(handler, body)


def handle_get_profile(handler):
    """GET /api/profile — the build_profile() payload."""
    try:
        _json_response(handler, build_profile())
    except Exception as e:
        _error_response(handler, f"Failed to build profile: {e}", status=500)


def handle_profile_identity(handler):
    """PUT /api/profile/identity — body {name,email,company,timezone}."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    _respond_apply(handler, apply_profile_identity, body)


def handle_profile_voice(handler):
    """PUT /api/profile/voice — body {teams?, email?}."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    _respond_apply(handler, apply_profile_voice, body)


def handle_profile_integration(handler, category):
    """POST /api/profile/integrations/{category} — body {active}."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    _respond_apply(handler, apply_profile_integration, category, body)


def handle_profile_packs(handler):
    """POST /api/profile/packs — body {active:[...]}."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    _respond_apply(handler, apply_profile_packs, body)


def handle_profile_model_posture(handler):
    """PUT /api/profile/model-posture — body {level}."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    _respond_apply(handler, apply_profile_model_posture, body)


def handle_quality(handler):
    """GET /api/quality — read-only shadow-judge scoreboard, frontmatter-sourced."""
    try:
        _json_response(handler, build_quality())
    except Exception as e:
        _error_response(handler, f"Failed to gather tasks: {e}", status=500)


def handle_get_task(handler, task_id):
    """GET /api/tasks/{id} — Return full task detail with parsed activity log."""
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Failed to read task: {e}", status=500)
        return

    fm = task_data["frontmatter"]
    body = task_data["body"]

    # Build response: all frontmatter fields + body + parsed activity log
    result = {}
    for key, value in fm.items():
        result[key] = value
    result["body"] = body.strip()
    result["activity_log"] = _parse_activity_log(body)

    _enrich_sharepoint_url(result)
    _json_response(handler, result)


def handle_get_output(handler, task_id):
    """GET /api/tasks/{id}/output — return the task's .md artifact for inline editing."""
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Failed to read task: {e}", status=500)
        return

    rel = str(task_data["frontmatter"].get("agent_output") or "")
    filepath = _resolve_output_path(rel)
    if filepath is None:
        _error_response(handler, "Task has no editable markdown output", status=404)
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        # The task points at a markdown output, but no file exists at that path
        # yet (e.g. an agent stamped agent_output without producing the file).
        # Return the path so the client can title the doc and show an honest
        # "not found" state, rather than 404ing into a silent blank editor.
        _json_response(handler, {"path": rel.strip(), "format": "markdown", "content": "", "exists": False})
        return
    except Exception as e:
        _error_response(handler, f"Failed to read output: {e}", status=500)
        return
    _json_response(handler, {"path": rel.strip(), "format": "markdown", "content": content, "exists": True})


def _utc_now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def handle_save_output(handler, task_id):
    """PUT /api/tasks/{id}/output — persist edited markdown back to the artifact file."""
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Failed to read task: {e}", status=500)
        return

    rel = str(task_data["frontmatter"].get("agent_output") or "")
    filepath = _resolve_output_path(rel)
    if filepath is None:
        _error_response(handler, "Task has no editable markdown output", status=404)
        return
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    if not isinstance(body, dict):
        _error_response(handler, "Request body must be a JSON object", status=400)
        return
    content = body.get("content")
    if content is None:  # None = missing field (400); "" is a legitimate "cleared the document" save.
        _error_response(handler, "Missing 'content' field", status=400)
        return
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        _error_response(handler, f"Failed to write output: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "savedAt": _utc_now_iso()})


def handle_complete_task(handler, task_id):
    """POST /api/tasks/{id}/done — Mark task complete and archive."""
    try:
        archive_path = task_lib.complete_task(task_id, actor="human")
        _json_response(handler, {
            "status": "ok",
            "message": f"Task {task_id} completed and archived",
            "archive_path": archive_path,
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to complete task: {e}", status=500)


def handle_complete_and_delete_task(handler, task_id):
    """POST /api/tasks/{id}/done-and-delete — Complete task and delete output files."""
    try:
        archive_path, deleted = task_lib.complete_and_delete_task(task_id, actor="human")
        _json_response(handler, {
            "status": "ok",
            "message": f"Task {task_id} completed; output files deleted",
            "archive_path": archive_path,
            "files_deleted": deleted,
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to complete task: {e}", status=500)


def handle_update_description(handler, task_id):
    """POST /api/tasks/{id}/description — Update the description section."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return

    description = body.get("description", "").strip()
    if not description:
        _error_response(handler, "Missing or empty 'description' field", status=400)
        return

    try:
        filepath = task_lib.update_task_description(task_id, description, actor="human")
        _json_response(handler, {
            "status": "ok",
            "message": f"Description updated for {task_id}",
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to update description: {e}", status=500)


def handle_update_message(handler, task_id):
    """POST /api/tasks/{id}/message — Save edits to a send-message draft.

    Body: {message_body, message_subject?}. Persists to frontmatter so the
    Message preview reflects the human's edits before sending.
    """
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return

    message_body = (body.get("message_body") or "").strip()
    if not message_body:
        _error_response(handler, "Missing or empty 'message_body' field", status=400)
        return

    changes = {"message_body": message_body}
    if body.get("message_subject") is not None:
        changes["message_subject"] = str(body.get("message_subject")).strip()

    try:
        task_lib.update_task(
            task_id,
            changes=changes,
            comment="Message draft edited.",
            actor="human",
        )
        _json_response(handler, {
            "status": "ok",
            "message": f"Message draft updated for {task_id}",
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to update message: {e}", status=500)


def handle_send_message(handler, task_id):
    """POST /api/tasks/{id}/send-message — actually send the drafted message.

    Routes through the Tier-2 messaging adapter (mgc → Graph): email via
    sendMail, Teams via chat. First-ever send raises NeedsConfirmation → a
    one-time confirm card; later sends fire straight through. With no provider
    configured it falls back to recording a manual send (pre-mgc behavior)."""
    try:
        draft = _message_draft_from_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    status, payload = _attempt_send_message(task_id, draft)
    if status == "needs_confirm":
        cid = _emit_confirm_card("messaging", task_id)
        _json_response(handler, {
            "status": "needs_confirmation", "confirm_task": cid,
            "message": "First send needs a one-time confirm — see the collab queue.",
        })
        return
    if status == "already_sent":
        _json_response(handler, {"status": "ok", "message": "Already sent — no duplicate."})
        return
    if status == "unconfigured":
        try:
            archive_path = _record_manual_send(task_id)
        except FileNotFoundError:
            _error_response(handler, f"Task {task_id} not found", status=404)
            return
        _json_response(handler, {
            "status": "ok", "archive_path": archive_path,
            "message": f"Recorded as sent (sent manually); {task_id} archived",
        })
        return
    if status == "ok":
        message_id, _ = payload
        _json_response(handler, {
            "status": "ok", "message_id": message_id,
            "message": f"Message sent ({draft['channel']}); {task_id} archived",
        })
        return
    code, msg = payload
    _error_response(handler, msg, status=code)


def handle_add_comment(handler, task_id):
    """POST /api/tasks/{id}/comment — Append activity log entry."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return

    content = body.get("content", "").strip()
    if not content:
        _error_response(handler, "Missing or empty 'content' field", status=400)
        return

    try:
        filepath = task_lib.update_task(
            task_id,
            comment=content,
            actor="human",
        )
        _json_response(handler, {
            "status": "ok",
            "message": f"Comment added to {task_id}",
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to add comment: {e}", status=500)


def _spawn_task_dispatch(task_id):
    """Fire task_dispatch.py --task {id} in the background (fire-and-forget).

    Strips CLAUDE_* env vars to dodge nested-session detection and keeps the
    claude binary on PATH. Shared by the single-task dispatch route and Quick
    Add so both spawn the dispatcher identically. Raises on Popen failure.
    """
    dispatch_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_dispatch.py")
    env = platform_lib.headless_claude_env()
    subprocess.Popen(
        [sys.executable, dispatch_script, "--task", task_id],
        cwd=PM_OS_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **platform_lib.process_group_kwargs(),
    )


def handle_dispatch_task(handler, task_id):
    """POST /api/tasks/{id}/dispatch — Dispatch agent for a single task in background."""
    try:
        _spawn_task_dispatch(task_id)
    except Exception as e:
        _error_response(handler, f"Failed to start dispatcher: {e}", status=500)
        return

    _json_response(handler, {
        "status": "ok",
        "message": f"Agent dispatched for {task_id}",
    })


# ─── Quick Add: capture a task in plain language ─────────────────────────────
# One line of free text -> the same create -> parse -> worker-match -> dispatch
# pipeline meeting- and cron-spawned tasks ride. The parser classifies the lane;
# agent-runnable lanes (agent, collab) auto-dispatch so the card lands in Now.

# Parser fields that map straight onto task_lib.create_task kwargs.
_QUICK_ADD_FIELDS = (
    "title", "queue", "priority", "domain", "description", "due", "project",
    "waiting_on", "task_type", "meeting_attendees", "meeting_duration",
    "meeting_title", "meeting_description", "message_channel", "message_to",
    "message_subject",
)

# Lanes the dispatcher actually services (see task_dispatch.get_actionable_tasks).
_DISPATCHABLE_QUEUES = ("agent", "collab")


def _parse_task_fields(text):
    """NL-parse free text into structured task fields (the pipeline's task parser)."""
    from parse_task_input import parse_task
    return parse_task(text)


def quick_add_task(text, auto_dispatch=True):
    """Quick Add core: NL-parse free text, create the task, optionally dispatch.

    Returns (face, dispatched) where face is the created task's frontmatter — the
    same projection /api/tasks returns — and dispatched is whether the dispatcher
    was spawned. A failed dispatch spawn does not fail the create; the task is
    already in its queue and the human can start it.
    """
    parsed = _parse_task_fields(text) or {}
    kwargs = {k: parsed[k] for k in _QUICK_ADD_FIELDS if parsed.get(k) is not None}
    if not kwargs.get("title"):
        kwargs["title"] = text.strip()[:120]
    kwargs.setdefault("queue", "agent")

    tags = parsed.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if "quick-add" not in tags:
        tags = ["quick-add"] + list(tags)
    kwargs["tags"] = tags
    kwargs["creator"] = "human"

    task_id, _ = task_lib.create_task(**kwargs)

    dispatched = False
    if auto_dispatch and kwargs["queue"] in _DISPATCHABLE_QUEUES:
        try:
            _spawn_task_dispatch(task_id)
            dispatched = True
        except Exception:
            pass  # task is created and queued; the human can start it manually

    face = task_lib.read_task(task_id)["frontmatter"]
    return face, dispatched


def handle_quick_add(handler):
    """POST /api/tasks/quick-add — create a task from one line of plain language.

    Body: {"text": "...", "auto_dispatch": true}. Returns {"ok": true, "task": <face>}
    so the client can land the card in Now; errors surface as {"error": "..."}.
    """
    body = _read_request_body(handler)
    text = (body.get("text") or "").strip()
    if not text:
        _error_response(handler, "Missing 'text' field", status=400)
        return
    auto_dispatch = body.get("auto_dispatch", True)
    try:
        face, _ = quick_add_task(text, auto_dispatch=auto_dispatch)
    except Exception as e:
        _error_response(handler, f"Quick add failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "task": face})


# ─── Card actions: accept → apply → receipt → undo, and graduate ─────────────
# These power the recommendation/receipt/graduation card types. The git path runs
# with `git -C PM_OS_DIR`; tests monkeypatch PM_OS_DIR to a throwaway repo.

def _program_id_from_tags(tags):
    """The program id a cadence card targets: the first tag that isn't 'cadence'.

    The reconciler tags every cadence card [program_id, "cadence"]; the program
    id is the non-"cadence" tag (a PROG-xxxx). Returns None when absent.
    """
    for tag in (tags or []):
        if tag and tag != "cadence":
            return tag
    return None


def _apply_cadence_proposal(task_id, t):
    """Accept a cadence propose-update card: apply the program mutation locally.

    Reads the structured `proposal` mutation + the target program id (the
    non-"cadence" tag), applies it via program_lib.apply_mutation (a working-tree
    program-file write, NOT a git commit), completes the proposal card with a
    comment recording the applied mutation, and spawns an INFORMATIONAL receipt.

    The receipt carries NO revert_commit: a program mutation is not a git revert,
    and these writes are not git-committed here. It documents non-revertibility
    the way an autoship receipt does (receipt_kind tells undo_receipt to skip the
    git path). No external write, no second shipper -- Tier-1.
    """
    proposal = t.get("proposal")
    if not isinstance(proposal, dict) or not proposal.get("op"):
        raise ValueError("This cadence card carries no applicable proposal to apply.")
    program_id = _program_id_from_tags(t.get("tags"))
    if not program_id:
        raise ValueError("This cadence card names no program to update.")

    # ── Birth branch (inc4a) ──────────────────────────────────────────────────
    # A birth proposal has no existing program to mutate: it CREATES one. Branch
    # here, before apply_mutation, so the advance-phase/adjust-checkpoint path
    # below stays byte-identical for every non-birth proposal.
    if proposal.get("op") == "birth":
        return _apply_cadence_birth(task_id, t, proposal, program_id)

    try:
        result = program_lib.apply_mutation(program_id, proposal)
    except ValueError as e:
        # An out-of-set / malformed mutation: surface as operator-actionable (409).
        raise RuntimeError(f"Could not apply this change: {e}")
    if result.get("applied") is None:
        # The applier refused (terminal phase, unknown checkpoint, etc.) -- no
        # mutation happened; tell the operator why rather than spawn a false receipt.
        raise RuntimeError(
            f"Could not apply this change: {result.get('reason', 'refused')}.")

    op = result.get("applied")
    summary = f"Applied {op} to {program_id}."
    if op == "advance-phase":
        summary = (f"Advanced {program_id}: phase {result.get('from')} -> "
                   f"{result.get('to')}.")
    elif op == "adjust-checkpoint":
        summary = f"Adjusted checkpoint {result.get('id')} on {program_id}."
        if result.get("advanced"):
            summary += (f" Cascaded to advance phase to "
                        f"{result['advanced'].get('to')}.")
    elif op == "archive":
        summary = f"Archived {program_id} (moved to programs/archive)."

    # Record the applied mutation on the proposal card, then archive it.
    task_lib.update_task(task_id, comment=f"Accepted: {summary}", actor="human")
    task_lib.complete_task(task_id, actor="human")

    receipt_id, _ = task_lib.create_task(
        f"Applied: {t.get('title', '')}", queue="human", domain="ops",
        creator="agent", card_type="receipt",
        description=(f"{summary} This is a local program update, not a git change, "
                     "so there is nothing to revert automatically."))
    # receipt_kind marks this receipt as non-revertible (no git commit behind it),
    # so undo_receipt never attempts a git revert on it.
    task_lib.update_task(receipt_id, changes={
        "receipt_kind": "cadence-apply", "source_recommendation": task_id,
        "program_id": program_id})
    return receipt_id


def _dispatch_bootstrap_task(task_id):
    """Fire the agent dispatcher for a bootstrap task (detached), best-effort.

    Reuses the reconciler's dispatch idiom (`cadence.reconcile._dispatch_agent_task`):
    spawn `task_dispatch.py --task {id}` with the Claude-stripped headless env and
    the platform process-group kwargs. Never raises -- a worker/provider that is
    not configured simply means the queued task waits (no external write fires on
    accept; the bootstrap rides the existing Tier-2 path only when later walked).
    Factored out so a test can monkeypatch it (no real claude spawn under test).
    """
    try:
        from cadence import reconcile as _reconcile
        _reconcile._dispatch_agent_task(task_id)
    except Exception as e:
        sys.stderr.write(f"[cadence] bootstrap dispatch skipped for {task_id}: {e}\n")


def _enqueue_bootstrap_emissions(program_type, new_id):
    """Enqueue the born type's bootstrap_emissions as ordinary tasks (Tier-1).

    Looks the emissions up in the registry by `program_type`. Each emission is a
    {action, template} dict. Tags every created task [new_id, "cadence"] and carries
    the template name onto the task (`bootstrap_template`) so the worker knows what
    to draft. Closed action set:
      - "draft-ticket"   -> an agent task that routes to the ticket-creator worker
                            (queue="agent"); it drafts only and rides the existing
                            Tier-2 path when later walked -- no external write now.
      - "propose-update" -> a recommendation / cadence-propose-update card (an
                            ordinary internal proposal, no proposal-mutation yet).
    An unknown action is skipped (logged), never a crash. Degrades gracefully when
    a worker/provider is unconfigured (the dispatch is best-effort). Returns the
    list of created task ids.
    """
    try:
        registry = program_lib.load_registry()
    except Exception as e:
        sys.stderr.write(f"[cadence] could not load registry for bootstrap: {e}\n")
        return []
    type_entry = next(
        (ty for ty in registry.get("types", []) if ty.get("id") == program_type),
        None)
    intake = (type_entry or {}).get("intake") or {}
    emissions = intake.get("bootstrap_emissions") or []

    created = []
    for em in emissions:
        if not isinstance(em, dict):
            continue
        action = em.get("action")
        template = em.get("template")
        if action == "draft-ticket":
            # An agent task routed to the ticket-creator worker deterministically
            # by task_type (dispatch scores an exact +100 match on the worker's
            # match.task_type list -- not a fragile title/description substring).
            # It DRAFTS only -- no external write until the existing Tier-2.
            bid, _ = task_lib.create_task(
                title=f"Create tracker for {new_id}",
                queue="agent", creator="cadence", tags=[new_id, "cadence"],
                task_type="ticket-creator",
                description=(
                    f"Draft the initial tracker issue for the newly born program "
                    f"{new_id}. Use jira-home to create the tracker initiative. "
                    f"Draft only -- the human publishes via the board."))
            task_lib.update_task(bid, changes={"bootstrap_template": template})
            created.append(bid)
            _dispatch_bootstrap_task(bid)
        elif action == "propose-update":
            # An ordinary internal proposal card (no program mutation attached --
            # this is a draftable roadmap entry, not a cadence apply).
            bid, _ = task_lib.create_task(
                title=f"Add roadmap entry for {new_id}",
                queue="human", priority="medium", creator="cadence",
                card_type="recommendation", tags=[new_id, "cadence"],
                description=(
                    f"Add a roadmap entry for the newly born program {new_id}."))
            task_lib.update_task(bid, changes={"bootstrap_template": template})
            created.append(bid)
        elif action:
            sys.stderr.write(
                f"[cadence] unknown bootstrap action '{action}' for {new_id} "
                f"-- skipped\n")
    return created


def _apply_cadence_birth(task_id, t, proposal, intake_program_id):
    """Accept a BIRTH proposal: create the program, enqueue bootstrap, receipt.

    Tier-1 throughout. Builds a birth spec from the proposal, calls
    program_lib.birth_program (which validates the type, creates the active
    program, writes ## Intent + one origin observation), marks the intake
    candidate birthed + linked to the new program, enqueues the born type's
    bootstrap_emissions as ordinary queued tasks (no external write fires here),
    completes the proposal card, and spawns an informational cadence-apply receipt
    (no git revert). Returns the receipt id. Reuses the existing card-completion +
    receipt machinery (does not reimplement it).
    """
    spec = {
        "program_type": proposal.get("program_type"),
        "title": proposal.get("title"),
        "checkpoints": proposal.get("checkpoints") or [],
        "citations": proposal.get("citations") or [],
    }
    try:
        new_id = program_lib.birth_program(spec)
    except ValueError as e:
        raise RuntimeError(f"Could not birth this program: {e}")

    # Mark the intake candidate birthed + linked. Tolerant: a missing/unknown
    # candidate must not strand a successfully created program.
    candidate_id = proposal.get("candidate_id")
    if candidate_id:
        try:
            program_lib.mark_candidate_birthed(
                intake_program_id, candidate_id, new_id)
        except Exception as e:
            sys.stderr.write(
                f"[cadence] could not mark candidate {candidate_id} birthed: {e}\n")

    bootstrap_ids = _enqueue_bootstrap_emissions(spec["program_type"], new_id)

    summary = (f"Born {spec['program_type']} {new_id}: "
               f"{proposal.get('title', '')}.")
    if bootstrap_ids:
        summary += f" Enqueued {len(bootstrap_ids)} bootstrap task(s)."

    # Reuse the existing completion + receipt machinery (same as _apply_cadence_proposal).
    task_lib.update_task(task_id, comment=f"Accepted: {summary}", actor="human")
    task_lib.complete_task(task_id, actor="human")

    receipt_id, _ = task_lib.create_task(
        f"Applied: {t.get('title', '')}", queue="human", domain="ops",
        creator="agent", card_type="receipt",
        description=(f"{summary} This created a local program file and queued its "
                     "bootstrap tasks; nothing was sent externally, so there is "
                     "nothing to revert automatically."))
    task_lib.update_task(receipt_id, changes={
        "receipt_kind": "cadence-apply", "source_recommendation": task_id,
        "program_id": new_id})
    return receipt_id


def apply_recommendation(task_id):
    """Accept a recommendation: git apply its patch, commit, spawn a receipt card.

    Returns the receipt task id. Raises RuntimeError on a patch that won't apply
    (the handler maps that to a 409).

    Working-tree assumption: `git add -A` stages ALL working-tree changes, so the
    accept-commit (and therefore the receipt's one-tap Undo) assumes a clean tree —
    unrelated edits would get swept into this commit. Acceptable for Phase 4 (single
    user, deliberate action); Phase 6 should scope the commit to the patch's files.

    Partial-failure rollback: if any step after `git apply` fails (including a commit
    that has nothing staged — e.g. a patch touching only gitignored paths), the
    working tree is hard-reset to HEAD (`git reset --hard HEAD`) before raising a
    RuntimeError. This relies on the same clean-working-tree assumption above —
    reset --hard would also discard unrelated working-tree edits, which is acceptable
    for Phase 4 but is another reason Phase 6 should scope the commit.
    """
    t = task_lib.read_task(task_id)["frontmatter"]

    # Cadence branch: a propose-update recommendation carries a structured program
    # `proposal` mutation (emitted by the reconciler's interpretation door). Accept
    # applies a LOCAL program-file mutation via program_lib.apply_mutation -- Tier-1,
    # no git apply/commit, no external write, no second shipper. The action type
    # rides the existing trust ladder at shadow (propose-only): the reconciler only
    # ever PROPOSES; this human accept IS the apply.
    if t.get("task_type") == "cadence-propose-update" or t.get("proposal"):
        return _apply_cadence_proposal(task_id, t)

    patch_path = t.get("patch_path")
    if not patch_path:
        raise ValueError("No patch to apply automatically — apply this change by hand per the card's notes, then dismiss it.")
    abspath = patch_path if os.path.isabs(patch_path) else os.path.join(PM_OS_DIR, patch_path)
    chk = subprocess.run(["git", "-C", PM_OS_DIR, "apply", "--check", abspath],
                         capture_output=True, text=True)
    if chk.returncode != 0:
        raise RuntimeError(f"patch does not apply cleanly: {chk.stderr.strip()[:300]}")
    # Pre-gate passed; apply for real. From here on, a failure leaves the tree dirty,
    # so any failure rolls back to HEAD before raising (RuntimeError -> 409).
    subprocess.run(["git", "-C", PM_OS_DIR, "apply", abspath], check=True, capture_output=True, text=True)
    try:
        subprocess.run(["git", "-C", PM_OS_DIR, "add", "-A"], check=True, capture_output=True, text=True)
        # `git diff --cached --quiet` returns 0 when NOTHING is staged.
        staged = subprocess.run(["git", "-C", PM_OS_DIR, "diff", "--cached", "--quiet"],
                                capture_output=True, text=True)
        if staged.returncode == 0:
            raise RuntimeError("patch produced no committable changes")
        msg = f"apply recommendation {task_id}: {t.get('title', '')}"
        subprocess.run(["git", "-C", PM_OS_DIR, "commit", "-m", msg], check=True, capture_output=True, text=True)
        rev = subprocess.run(["git", "-C", PM_OS_DIR, "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()
    except Exception as e:
        # Roll back the half-applied change so a failed accept never strands the tree.
        subprocess.run(["git", "-C", PM_OS_DIR, "reset", "--hard", "HEAD"],
                       capture_output=True, text=True)
        raise RuntimeError(str(e)) if not isinstance(e, RuntimeError) else e
    # Commit landed; archive the recommendation so it leaves the actionable lanes.
    task_lib.complete_task(task_id, actor="human")
    receipt_id, _ = task_lib.create_task(
        f"Applied: {t.get('title', '')}", queue="human", domain="ops", creator="agent",
        description=f"Applied recommendation {task_id}. One-tap Undo reverts it.",
        card_type="receipt")
    task_lib.update_task(receipt_id, changes={"revert_commit": rev, "source_recommendation": task_id})
    return receipt_id


def undo_receipt(task_id):
    """Undo a receipt: git revert the commit it recorded, mark the receipt done.

    For an auto-shipped receipt (receipt_kind=='autoship') it cannot revert the
    external action, so it instead demotes that action type to supervised and
    marks the receipt done.
    """
    t = task_lib.read_task(task_id)["frontmatter"] or {}
    # Auto-shipped action (email/ticket): cannot be un-sent. Undo means "stop
    # auto-shipping this type" — drop it to supervised and flag the card. Never
    # attempt a git revert (there is no local commit to revert).
    if t.get("receipt_kind") == "autoship":
        at = t.get("autoship_task_type")
        if at:
            ladder_lib.kill_to_supervised(at)
            comment = (f"Undo: stopped auto-shipping '{at}' (dropped to supervised). "
                       "The external action already happened and cannot be un-sent.")
        else:
            comment = ("Undo: the external action already happened and cannot be un-sent. "
                       "(No task type recorded on this receipt, so nothing was demoted.)")
        task_lib.update_task(task_id, changes={"status": "done"}, comment=comment, actor="human")
        return
    # Cadence program-apply receipt: the mutation is a local program-file change,
    # not a git commit, so there is no commit to revert. Undo just dismisses the
    # receipt with a note (the program file remains; revert by editing it / a new
    # proposal). Never attempt a git revert (there is no revert_commit).
    if t.get("receipt_kind") == "cadence-apply":
        comment = ("Undo: this was a local Cadence program update, not a git change, "
                   "so there is nothing to revert automatically. The program file "
                   "remains as applied.")
        task_lib.update_task(task_id, changes={"status": "done"}, comment=comment, actor="human")
        return
    rev = t.get("revert_commit")
    if not rev:
        raise ValueError("no revert_commit on this receipt")
    rv = subprocess.run(["git", "-C", PM_OS_DIR, "revert", "--no-edit", rev],
                        capture_output=True, text=True)
    if rv.returncode != 0:
        # A later commit touched the same lines: the revert conflicted and left the
        # tree half-reverted (REVERT_HEAD + conflict markers). Abort to restore it,
        # best-effort, then surface a 409 (same RuntimeError type accept uses).
        subprocess.run(["git", "-C", PM_OS_DIR, "revert", "--abort"],
                       capture_output=True, text=True)
        raise RuntimeError("Couldn't undo automatically - later changes conflict; revert by hand.")
    task_lib.update_task(task_id, changes={"status": "done"}, comment="Undone - reverted", actor="human")


def graduate_card(task_id, ladder_path=None):
    """Graduate a task-type to its proposed tier in the ladder store."""
    t = task_lib.read_task(task_id)["frontmatter"]
    task_type = t.get("grad_task_type")
    proposed = t.get("grad_proposed_tier")
    if not task_type or not proposed:
        raise ValueError("graduation card missing grad_task_type / grad_proposed_tier")
    ladder_lib.set_tier(task_type, proposed, path=ladder_path)
    # Archive so the graduation card leaves the actionable lanes.
    task_lib.complete_task(task_id, actor="human")


def handle_accept(handler, task_id):
    """POST /api/tasks/{id}/accept — apply a recommendation's patch, spawn a receipt."""
    try:
        receipt_id = apply_recommendation(task_id)
    except (ValueError, RuntimeError) as e:
        # ValueError: no patch to auto-apply (prose-only card). RuntimeError: patch
        # won't apply / nothing to commit. Both are operator-actionable — surface the
        # plain reason as a 409, not an opaque 500.
        _error_response(handler, str(e), status=409)
        return
    except Exception as e:
        _error_response(handler, f"Accept failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "receipt_id": receipt_id})


def reject_recommendation(task_id):
    """Dismiss a recommendation (no git). For a BIRTH proposal, also close the
    intake candidate with a reason so it is not re-proposed.

    A birth proposal carries proposal {op: "birth", candidate_id} and is tagged
    [intake_program_id, "cadence"]. Closing the candidate (append-only, the
    "declined" memory) keeps the nursery from re-emitting the same birth. The
    candidate close is best-effort: a missing candidate/program must not block the
    card dismissal (the existing reject behavior is preserved either way).
    """
    try:
        t = task_lib.read_task(task_id)["frontmatter"] or {}
    except Exception:
        t = {}
    proposal = t.get("proposal")
    if isinstance(proposal, dict) and proposal.get("op") == "birth":
        intake_program_id = _program_id_from_tags(t.get("tags"))
        candidate_id = proposal.get("candidate_id")
        if intake_program_id and candidate_id:
            try:
                program_lib.close_candidate(
                    intake_program_id, candidate_id,
                    reason="rejected at birth proposal")
            except Exception as e:
                sys.stderr.write(
                    f"[cadence] could not close candidate {candidate_id} on "
                    f"reject: {e}\n")
    task_lib.cancel_task(task_id, reason="rejected", actor="human")


def handle_reject(handler, task_id):
    """POST /api/tasks/{id}/reject — dismiss a recommendation (no git)."""
    try:
        reject_recommendation(task_id)
    except Exception as e:
        _error_response(handler, f"Reject failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True})


def handle_graduate(handler, task_id):
    """POST /api/tasks/{id}/graduate — advance a task-type to its proposed tier."""
    try:
        graduate_card(task_id)
    except Exception as e:
        _error_response(handler, f"Graduate failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True})


def handle_get_autonomy(handler):
    """GET /api/config/autonomy — current global Autonomous-Mode posture flag."""
    _json_response(handler, {"enabled": profile_lib.autonomy_enforcement()})


def handle_set_autonomy(handler):
    """POST /api/config/autonomy {"enabled": bool} — flip the posture flag."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    if not isinstance(body.get("enabled"), bool):
        _error_response(handler, "Body must include boolean 'enabled'", status=400)
        return
    enabled = body["enabled"]
    profile_lib.set_autonomy_enforcement(enabled)
    _json_response(handler, {"ok": True, "enabled": enabled})


def handle_demote(handler, task_type):
    """POST /api/tasks/{type}/demote — kill switch: drop a type to supervised."""
    if not task_type:
        _error_response(handler, "Missing task_type", status=400)
        return
    try:
        tier = ladder_lib.kill_to_supervised(task_type)
    except Exception as e:
        _error_response(handler, f"Demote failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "task_type": task_type, "tier": tier})


def handle_keep(handler, task_id):
    """POST /api/tasks/{id}/keep — dismiss a receipt, keeping the applied change."""
    try:
        task_lib.complete_task(task_id, actor="human")
    except Exception as e:
        _error_response(handler, f"Keep failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True})


def handle_undo(handler, task_id):
    """POST /api/tasks/{id}/undo — revert the commit a receipt recorded."""
    try:
        undo_receipt(task_id)
    except RuntimeError as e:
        # Conflict on revert — surface the plain reason as a 409.
        _error_response(handler, str(e), status=409)
        return
    except Exception as e:
        _error_response(handler, f"Undo failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True})


def handle_react(handler, task_id):
    """POST /api/tasks/{id}/react — record human 👍/👎 + optional note to frontmatter."""
    body = _read_request_body(handler)
    react = body.get("react")
    note = body.get("note") or None
    if react not in ("up", "down"):
        _error_response(handler, "react must be 'up' or 'down'")
        return
    try:
        task_lib.react_to_task(task_id, react, note=note)
    except Exception as e:
        _error_response(handler, f"React failed: {e}", status=500)
        return
    # Silent LangFuse mirror (opt-in): score the worker-execution trace if enabled.
    try:
        from langfuse_client import get_langfuse, score_trace
        lf = get_langfuse()
        if lf is not None:
            result = lf.api.trace.list(session_id=task_id, order_by="timestamp.desc")
            traces = result.data if hasattr(result, "data") else []
            for t in traces:
                if str(getattr(t, "name", "")).startswith("worker-execution"):
                    score_trace(getattr(t, "id", None), "human-feedback",
                                1.0 if react == "up" else 0.0, comment=note or "", data_type="NUMERIC")
                    break
    except Exception:
        pass
    _json_response(handler, {"ok": True, "task_id": task_id, "react": react})


def handle_get_chat(handler, task_id):
    """GET /api/tasks/{id}/chat — return the task's persisted chat transcript.

    Returns {"events": [...]} — the normalized chat events the frontend replays
    into the panel on task open. A read failure (missing sidecar, malformed
    line, OS error) never 500s the panel: it degrades to {"events": []} so the
    chat opens empty rather than broken.
    """
    try:
        events = chat_transcript.read_events(task_id)
    except Exception:
        events = []
    _json_response(handler, {"events": events})


def handle_chat(handler, task_id):
    """POST /api/tasks/{id}/chat — run one chat turn, stream events over SSE.

    Enforces two guards before streaming:
      1. The background worker must not be mid-run on this session
         (frontmatter agent_status == "running" → 409).
      2. No other chat run may be live for this session (live_runs.is_live → 409).

    DECOUPLED run (mirrors Adapt): the chat turn no longer streams the
    chat_runner.run_turn generator directly. Instead it is started on a
    live_runs daemon thread (keyed by `chat:<task_id>`) and the SSE handler only
    TAILS the durable chat transcript. So a client disconnect (the panel closing
    / the task closing) stops only the tail — never the run. The run finishes on
    its own and keeps persisting to the transcript; reopening the panel replays
    the progress-so-far via GET /api/tasks/{id}/chat.

    NO-OP append_fn: chat_runner.run_turn ALREADY appends every event to the
    chat transcript itself (chat_transcript.append_event), so the read side
    tails chat_transcript.read_events(task_id) and start() is given a no-op
    append_fn to avoid double-logging (same decision as Adapt).

    Frontend contract: once _sse_begin has sent 200 OK, the stream ALWAYS
    terminates with a normal `event: done` (via _sse_end), a `kind:error`
    frame, or a clean stop on client disconnect. A stream that ends without
    `event: done` (or with a `kind:error` frame) should be treated by the
    frontend as a failed turn that can be retried.
    """
    body = _read_request_body(handler)
    message = (body.get("message") or "").strip()
    if not message:
        _error_response(handler, "message is required")
        return

    # Background-busy check: if the dispatch worker is running on this session,
    # a chat turn would collide with it.
    try:
        task = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, "task not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Failed to read task: {e}", status=500)
        return
    # NOTE: this mutual-exclusion between a background run and a chat run is
    # best-effort — there's a TOCTOU window between this read and the separate
    # dispatcher process flipping agent_status. Acceptable for the single-
    # operator local board.
    if task.get("frontmatter", {}).get("agent_status") == "running":
        _error_response(handler, "Agent is currently working", status=409)
        return

    # Chat-concurrency: one LIVE chat run per session. A second concurrent POST
    # while the run is live is a 409 (same status the old in-memory lock
    # returned) — the surviving run keeps filling the transcript regardless.
    key = _chat_run_key(task_id)
    if live_runs.is_live(key):
        _error_response(handler, "A chat run is already in progress", status=409)
        return

    # Start the decoupled run. NO-OP append_fn: run_turn owns the durable log
    # (chat_transcript); passing append_event here would double-log. start() is
    # itself a no-op if a run for this key is already live.
    live_runs.start(
        key,
        chat_runner.run_turn(task_id, message),
        lambda event: None,
    )

    _sse_begin(handler)
    _stream_live_run(
        handler,
        key,
        lambda: chat_transcript.read_events(task_id),
        "The chat run ended unexpectedly. You can retry.",
    )


# ─── Adapt: gated build session + rail CRUD ────────────────────────────────────
# The build run is DECOUPLED from the SSE client via live_runs: the run is keyed
# by adaptation_id, runs to completion on its own daemon thread, and a client
# disconnect only stops the tail (never the run). One LIVE run per key (a NEW
# message while live -> 409); the GET stream may always reconnect and tail.

def _heartbeat_or_send(handler, event):
    """Stream one tail event as SSE, turning heartbeats into a `: ping` comment.

    The client ignores comment lines; data frames carry real events. Raises
    BrokenPipeError/ConnectionResetError on a dead socket (the caller stops
    tailing on that - the run keeps going).
    """
    if event.get("kind") == "heartbeat":
        handler.wfile.write(b": ping\n\n")
        handler.wfile.flush()
    else:
        _sse_send(handler, event)


def _stream_live_run(handler, key, read_fn, error_text):
    """Tail the live run for `key` over SSE, then surface a run error if any.

    Shared survive-disconnect streaming substrate for both Adapt and the task
    chat panel. Replays the durable event log and tails new events
    (live_runs.tail over `read_fn`). On a client disconnect we stop tailing but
    DO NOT touch the run — it is owned by live_runs, keeps filling its durable
    log, and reaches completion. After the tail drains with the socket intact,
    if the run ended abnormally (live_runs.run_error) we emit a terminal error
    frame (`error_text`) so a crashed run is not mistaken for a clean finish.
    Always closes with the `event: done` sentinel when the socket is still alive.
    """
    disconnected = False
    try:
        for event in live_runs.tail(key, read_fn):
            _heartbeat_or_send(handler, event)
    except (BrokenPipeError, ConnectionResetError):
        # Client went away mid-stream. Stop tailing; the run is owned by
        # live_runs and keeps filling the durable log for a later reconnect.
        disconnected = True
    if disconnected:
        return
    # Tail drained with the socket intact. If the run ended in error, the tail
    # looked like a clean finish (the runner may emit no error event) - surface
    # a terminal error frame so the client can offer a retry.
    try:
        if live_runs.run_error(key) is not None:
            _sse_send(handler, {
                "kind": "error",
                "role": "error",
                "text": error_text,
            })
        _sse_end(handler)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _stream_adapt_run(handler, key):
    """Tail the live Adapt build run for `key` over SSE (see _stream_live_run)."""
    _stream_live_run(
        handler,
        key,
        lambda: adapt_transcript.read_events(key),
        "The build run ended unexpectedly. You can retry.",
    )


def handle_adapt(handler):
    """POST /api/adapt {message, adaptation_id?} - run a build turn over SSE.

    New build (no adaptation_id): create the row at POST time so the run can be
    keyed by id before claude reports a session (provisional name = first chars
    of the message). Resume/edit (adaptation_id given): key by that id.

    Decoupled run: if no live run exists for the key, start one
    (live_runs.start) with a NO-OP append_fn - adapt_runner already appends every
    event to its own durable log, so the read side uses adapt_transcript.
    Then stream by tailing that log. A NEW message while a run is already live
    returns 409 (the client should reconnect via GET /api/adapt/stream instead).
    """
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    message = (body.get("message") or "").strip()
    if not message:
        _error_response(handler, "message is required")
        return
    adaptation_id = (body.get("adaptation_id") or "").strip() or None

    # New build: mint the row now so the run is keyed by a real id from the
    # start (provisional name = leading chars of the message; empty session id
    # so adapt_runner treats it as a new session and binds the id on first
    # result).
    if adaptation_id is None:
        try:
            provisional = adapt_runner._provisional_name(message)
            adaptation_id = adaptations_lib.create(provisional, "")
        except Exception as e:
            _error_response(handler, f"Failed to create adaptation: {e}", status=500)
            return
    else:
        # Resume/edit: a NEW message while the run is already live is a 409 -
        # the client should reconnect via the GET stream, not start a 2nd run.
        if live_runs.is_live(adaptation_id):
            _error_response(handler, "A build run is already in progress", status=409)
            return

    # Start the decoupled run if not already live. NO-OP append_fn: adapt_runner
    # owns the durable log (adapt_transcript); passing append_event here would
    # double-log. start() is itself a no-op if a run for this key is live.
    live_runs.start(
        adaptation_id,
        adapt_runner.run_turn(adaptation_id, message),
        lambda event: None,
    )

    _sse_begin(handler)
    _stream_adapt_run(handler, adaptation_id)


def handle_adapt_stream(handler, query_params):
    """GET /api/adapt/stream?adaptation=<id> - reconnect: replay + tail a run.

    Tails the run keyed by the adaptation id, replaying the durable event log
    first (live_runs.tail does the replay-then-stream). Works whether the run is
    in-progress or already finished; a finished-with-error run gets a terminal
    error frame after the replay.
    """
    adaptation_id = (query_params.get("adaptation", [None])[0] or "").strip()
    if not adaptation_id:
        _error_response(handler, "adaptation query parameter is required", status=400)
        return
    _sse_begin(handler)
    _stream_adapt_run(handler, adaptation_id)


def handle_list_adaptations(handler):
    """GET /api/adaptations - rail data: every active adaptation (id, name, state)."""
    try:
        adaptations = adaptations_lib.list_all()
    except Exception as e:
        _error_response(handler, f"Failed to list adaptations: {e}", status=500)
        return
    _json_response(handler, {"adaptations": adaptations})


def handle_toggle_adaptation(handler, adaptation_id):
    """PUT /api/adaptations/{id}/toggle {state} - flip on/off (rail toggle)."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    state = (body.get("state") or "").strip()
    # Only on/off are user-toggleable; "pending"/"building" are runner-internal states.
    if state not in ("on", "off"):
        _error_response(handler, "state must be 'on' or 'off'", status=400)
        return
    try:
        adaptations_lib.set_state(adaptation_id, state)
    except FileNotFoundError:
        _error_response(handler, f"Adaptation {adaptation_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Toggle failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "state": state})


def handle_rename_adaptation(handler, adaptation_id):
    """PUT /api/adaptations/{id} {name} - inline rename (id is stable)."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return
    name = (body.get("name") or "").strip()
    if not name:
        _error_response(handler, "name is required", status=400)
        return
    try:
        adaptations_lib.set_name(adaptation_id, name)
    except FileNotFoundError:
        _error_response(handler, f"Adaptation {adaptation_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Rename failed: {e}", status=500)
        return
    _json_response(handler, {"ok": True, "name": name})


def _adapt_git_revert(sha):
    """Revert one commit, best-effort. Returns (ok, message).

    Reuses undo_receipt's mechanism: `git revert --no-edit <sha>`; on failure
    (a later commit touched the same lines, or the commit is already reverted)
    abort to restore the tree and report the failure rather than raising - the
    bundle delete tolerates a partial failure (it must still tombstone). A
    module-level seam so tests assert revert ORDER without touching the repo.
    """
    rv = subprocess.run(["git", "-C", PM_OS_DIR, "revert", "--no-edit", sha],
                        capture_output=True, text=True)
    if rv.returncode != 0:
        subprocess.run(["git", "-C", PM_OS_DIR, "revert", "--abort"],
                       capture_output=True, text=True)
        return (False, (rv.stderr or rv.stdout or "revert failed").strip()[:200])
    return (True, "")


def handle_delete_adaptation(handler, adaptation_id):
    """POST /api/adaptations/{id}/delete - bundle revert, then tombstone.

    Reverts each manifest commit NEWEST-first (reverse of capture order, which
    the manifest stores oldest->newest), best-effort: a conflicting/already-
    reverted commit is skipped and reported as a partial failure rather than a
    500. After reverting, tombstone the row (invariant #6: never hard-delete;
    the file stays, marked deleted). The warning modal is client-side.
    """
    try:
        rec = adaptations_lib.read(adaptation_id)
    except FileNotFoundError:
        _error_response(handler, f"Adaptation {adaptation_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Delete failed: {e}", status=500)
        return

    manifest = rec.get("manifest") or []
    # Capture order is oldest->newest; revert newest-first. De-dupe shas (a
    # single commit can produce several manifest entries) while preserving the
    # newest-first order.
    shas = []
    for entry in reversed(manifest):
        sha = entry.get("commit")
        if sha and sha not in shas:
            shas.append(sha)

    partial = False
    failures = []
    for sha in shas:
        ok, msg = _adapt_git_revert(sha)
        if not ok:
            partial = True
            failures.append({"commit": sha, "error": msg})

    try:
        adaptations_lib.tombstone(adaptation_id)
    except Exception as e:
        _error_response(handler, f"Delete failed to tombstone: {e}", status=500)
        return

    resp = {"ok": True}
    if partial:
        resp["partial"] = True
        resp["failures"] = failures
    _json_response(handler, resp)


def handle_rerun_task(handler, task_id):
    """POST /api/tasks/{id}/rerun — Reset agent state and re-dispatch the task."""
    try:
        # Reset agent fields and status to open
        task_lib.update_task(task_id, changes={
            "status": "open",
            "agent_status": "",
            "agent_error": "",
            "agent_output": "",
            "agent_started": "",
            "agent_completed": "",
        }, comment="Task reset for agent rerun.", actor="human", )
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    except Exception as e:
        _error_response(handler, f"Failed to reset task: {e}", status=500)
        return

    # Dispatch the agent through the shared OS-aware respawn path.
    import task_dispatch
    if task_dispatch.respawn(task_id, rerun=True) is None:
        _error_response(handler, "Reset succeeded but dispatch failed", status=500)
        return

    _json_response(handler, {
        "status": "ok",
        "message": f"Task {task_id} reset and agent re-dispatched",
    })


def handle_schedule_meeting(handler, task_id):
    """POST /api/tasks/{id}/schedule-meeting — Create calendar event and complete task."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return

    slot_start = body.get("slot_start", "").strip()
    slot_end = body.get("slot_end", "").strip()
    if not slot_start or not slot_end:
        _error_response(handler, "Missing slot_start or slot_end", status=400)
        return

    # Load the task and validate it's a schedule-meeting task
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return

    fm = task_data["frontmatter"]
    if fm.get("task_type") != "schedule-meeting":
        _error_response(handler, f"Task {task_id} is not a schedule-meeting task", status=400)
        return

    # Use values from POST body if provided (user may have edited in UI), else fall back to task frontmatter
    meeting_title = body.get("meeting_title") or fm.get("meeting_title") or fm.get("title", "Meeting")
    meeting_attendees_raw = body.get("attendees") or fm.get("meeting_attendees") or []
    meeting_description = body.get("meeting_description") or fm.get("meeting_description") or ""
    timezone = "America/New_York"

    # Resolve name-based attendees to email addresses
    email_cache = _load_email_cache()
    meeting_attendees = []
    for a in meeting_attendees_raw:
        resolved = email_cache.get(str(a), str(a))
        if resolved:  # skip None entries from cache
            meeting_attendees.append(resolved)

    # Recurrence: from POST body or task frontmatter
    recurring = body.get("recurring") or None
    if not recurring and fm.get("meeting_recurring"):
        recurring = fm.get("meeting_recurrence_pattern") or "weekly"

    # Build args for create_calendar_event.py
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_calendar_event.py")
    cmd = [
        sys.executable, script_path,
        "--subject", meeting_title,
        "--start", slot_start,
        "--end", slot_end,
        "--timezone", timezone,
    ]
    if meeting_attendees:
        cmd.extend(["--attendees", ",".join(str(a) for a in meeting_attendees)])
    if meeting_description:
        cmd.extend(["--body", meeting_description])
    if recurring:
        cmd.extend(["--recurring", recurring])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        _error_response(handler, "Calendar event creation timed out", status=500)
        return
    except Exception as e:
        _error_response(handler, f"Failed to run calendar script: {e}", status=500)
        return

    if result.returncode != 0:
        error_msg = result.stderr.strip() or f"Exit code {result.returncode}"
        _error_response(handler, f"Calendar event creation failed: {error_msg}", status=500)
        return

    # Parse response for event ID
    event_id = None
    try:
        event_data = json.loads(result.stdout)
        event_id = event_data.get("id")
    except (json.JSONDecodeError, AttributeError):
        pass

    # Update task with selected slot and event ID, then complete+archive
    now = task_lib._now_iso()
    changes = {
        "meeting_selected_slot": slot_start,
    }
    if event_id:
        changes["meeting_event_id"] = event_id

    task_lib.update_task(
        task_id,
        changes=changes,
        comment=f"Calendar event created for {slot_start}. Attendees: {', '.join(str(a) for a in meeting_attendees)}",
        actor="human",
    )

    # Complete and archive
    archive_path = task_lib.complete_task(task_id, actor="human")

    _json_response(handler, {
        "status": "ok",
        "message": f"Calendar event created and task {task_id} archived",
        "event_id": event_id,
        "archive_path": archive_path,
    })


def handle_publish_jira(handler, task_id):
    """POST /api/tasks/{id}/publish-jira — publish a Jira draft (Tier-2 gated)."""
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    draft = jira_publish.parse_jira_draft(task_data.get("body", ""))
    status, payload = _attempt_publish(task_id, draft)
    if status == "needs_confirm":
        cid = _emit_confirm_card("project_management", task_id)
        _json_response(handler, {
            "status": "needs_confirmation",
            "confirm_task": cid,
            "message": "First external write needs a one-time confirm — see the collab queue.",
        })
        return
    if status == "already_published":
        _json_response(handler, {"status": "ok", "message": "Already published — no new ticket created."})
        return
    if status == "ok":
        issue_key, issue_url = payload
        _json_response(handler, {
            "status": "ok",
            "message": f"Published to Jira: {issue_key}",
            "issue_key": issue_key, "issue_url": issue_url,
        })
        return
    code, msg = payload
    _error_response(handler, msg, status=code)


def handle_confirm(handler, task_id):
    """POST /api/tasks/{id}/confirm — Tier-2: record consent for an integration's
    first external write, then re-drive the blocked publish."""
    try:
        card = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    fm = card["frontmatter"]
    family = fm.get("confirm_family")
    source_task = fm.get("confirm_source_task")
    if not family or not source_task:
        _error_response(handler, "Confirm card missing confirm_family/confirm_source_task", status=400)
        return
    provider = profile_lib.provider(family)
    try:
        profile_lib.set_integration_confirmed(family, True, provider=provider)
    except Exception as e:
        _error_response(handler, f"Could not record confirmation: {e}", status=500)
        return
    try:
        src = task_lib.read_task(source_task)
    except FileNotFoundError:
        try:
            task_lib.complete_task(task_id, actor="human")
        except Exception:
            pass
        _json_response(handler, {"ok": True, "note": "confirmed; source draft no longer exists"})
        return

    # Re-drive the blocked external write, dispatched by family. Each family
    # returns the same (status, payload) shape; "already_*" statuses differ in
    # name only. The success payload differs (issue key vs message id).
    if family == "messaging":
        draft = _message_draft_from_task(source_task)
        status, payload = _attempt_send_message(source_task, draft)
        done_statuses = ("ok", "already_sent")
        success_key = "message_id"
    else:
        draft = jira_publish.parse_jira_draft(src.get("body", ""))
        status, payload = _attempt_publish(source_task, draft)
        done_statuses = ("ok", "already_published")
        success_key = "issue"

    if status in done_statuses and status != "ok":
        try:
            task_lib.complete_task(task_id, actor="human")
        except Exception:
            pass
        _json_response(handler, {"ok": True, "note": f"source already {status.split('_')[-1]}"})
        return
    if status == "needs_confirm":
        # The active provider changed between emitting this card and confirming, so
        # consent was recorded for a different provider. Leave the confirm card
        # un-completed — a retry against the new provider is expected.
        _error_response(handler, "The integration changed since this card was created — please retry to confirm again.", status=409)
        return
    if status != "ok":
        code, msg = payload if isinstance(payload, tuple) else (500, "external write failed")
        _error_response(handler, msg, status=code)
        return
    try:
        task_lib.complete_task(task_id, actor="human")
    except Exception:
        pass
    if success_key == "message_id":
        _json_response(handler, {"ok": True, "message_id": payload[0]})
    else:
        issue_key, issue_url = payload
        _json_response(handler, {"ok": True, "issue_key": issue_key, "issue_url": issue_url})


def handle_update_meeting_details(handler, task_id):
    """POST /api/tasks/{id}/meeting-details — Update meeting title and description."""
    try:
        body = _read_request_body(handler)
    except (json.JSONDecodeError, ValueError) as e:
        _error_response(handler, f"Invalid JSON body: {e}", status=400)
        return

    changes = {}
    if "meeting_title" in body:
        changes["meeting_title"] = body["meeting_title"].strip()
    if "meeting_description" in body:
        changes["meeting_description"] = body["meeting_description"].strip()

    if not changes:
        _error_response(handler, "No meeting fields to update", status=400)
        return

    try:
        task_lib.update_task(
            task_id,
            changes=changes,
            comment=f"Meeting details updated: {', '.join(changes.keys())}",
            actor="human",
        )
        _json_response(handler, {
            "status": "ok",
            "message": f"Meeting details updated for {task_id}",
        })
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
    except Exception as e:
        _error_response(handler, f"Failed to update meeting details: {e}", status=500)


def handle_open_file(handler, query_params):
    """GET /open — Open a file in Ghostty + NeoVim."""
    file_param = query_params.get("file", [None])[0]
    if not file_param:
        _error_response(handler, "Missing 'file' query parameter", status=400)
        return

    # Resolve relative paths against PM_OS_DIR (agent_output paths are relative)
    if os.path.isabs(file_param):
        filepath = file_param
    else:
        filepath = os.path.join(PM_OS_DIR, file_param)

    filepath = os.path.normpath(filepath)

    if not os.path.isfile(filepath):
        _error_response(handler, f"File not found: {file_param}", status=404)
        return

    # Open .docx files in the default app (Word) instead of NeoVim
    if filepath.endswith(".docx") or platform_lib.os_kind() == "windows":
        cmd = platform_lib.open_file_cmd(filepath)
    elif platform_lib.os_kind() == "darwin":
        cmd = [
            "open", "-na", "Ghostty.app", "--args",
            f"--command=nvim {shlex.quote(filepath)}",
        ]
    else:
        cmd = [
            "ghostty",
            f"--command=nvim {shlex.quote(filepath)}",
        ]

    subprocess.Popen(cmd, **platform_lib.process_group_kwargs())

    handler.send_response(204)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()


def handle_people_emails(handler):
    """GET /api/people/emails — Return the email cache for attendee typeahead."""
    email_cache_path = os.path.join(PM_OS_DIR, "datasets", "people", "email_cache.json")
    try:
        with open(email_cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _json_response(handler, data)
    except FileNotFoundError:
        _json_response(handler, {})
    except Exception as e:
        _error_response(handler, f"Failed to load email cache: {e}", status=500)


# ─── LangFuse API Handlers ───────────────────────────────────────────────────

def _get_langfuse():
    """Get LangFuse client, returning None if unavailable."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from langfuse_client import get_langfuse
        return get_langfuse()
    except Exception:
        return None


def handle_langfuse_health(handler):
    """GET /api/langfuse/health — Check LangFuse connectivity."""
    lf = _get_langfuse()
    if lf is None:
        _json_response(handler, {
            "status": "unavailable",
            "message": "LangFuse not configured (LANGFUSE_SECRET_KEY not set or package not installed)",
            "host": os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
        })
        return
    try:
        # Quick health check — try to fetch prompts list
        lf.api.prompts.list()
        _json_response(handler, {
            "status": "ok",
            "host": os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
        })
    except Exception as e:
        _json_response(handler, {
            "status": "error",
            "message": str(e),
            "host": os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
        }, status=503)


def handle_langfuse_prompts(handler):
    """GET /api/langfuse/prompts — List all prompts with version info (paginated fetch)."""
    lf = _get_langfuse()
    if lf is None:
        _json_response(handler, {"prompts": [], "error": "LangFuse not available"})
        return
    try:
        all_data = []
        page = 1
        while True:
            result = lf.api.prompts.list(page=page, limit=100)
            data = result.data if hasattr(result, 'data') else []
            all_data.extend(data)
            if len(data) < 100:
                break
            page += 1

        prompts = []
        for p in all_data:
            prompts.append({
                "name": p.name if hasattr(p, 'name') else str(p),
                "version": getattr(p, 'version', None) or getattr(p, 'last_version', None) or 1,
                "labels": getattr(p, 'labels', []),
                "type": getattr(p, 'type', 'text'),
                "updated_at": str(getattr(p, 'updated_at', '')),
                "tags": getattr(p, 'tags', []),
            })
        _json_response(handler, {"prompts": prompts})
    except Exception as e:
        _json_response(handler, {"prompts": [], "error": str(e)}, status=500)


def handle_langfuse_trace_stats(handler):
    """GET /api/langfuse/traces/stats — Aggregate trace statistics."""
    lf = _get_langfuse()
    if lf is None:
        _json_response(handler, {"stats": {}, "error": "LangFuse not available"})
        return
    try:
        # Fetch recent traces
        result = lf.api.trace.list(limit=100, order_by="timestamp.desc")
        traces = result.data if hasattr(result, 'data') else []

        # Aggregate by trace name
        stats = {}
        for t in traces:
            name = getattr(t, 'name', 'unknown')
            if name not in stats:
                stats[name] = {"count": 0, "success": 0, "errors": 0}
            stats[name]["count"] += 1
            level = getattr(t, 'level', 'DEFAULT')
            if level == "ERROR":
                stats[name]["errors"] += 1
            else:
                stats[name]["success"] += 1

        # Add success rates
        for name, s in stats.items():
            s["success_rate"] = round(s["success"] / s["count"] * 100, 1) if s["count"] > 0 else 0

        _json_response(handler, {"stats": stats, "total_traces": len(traces)})
    except Exception as e:
        _json_response(handler, {"stats": {}, "error": str(e)}, status=500)


def handle_task_traces(handler, task_id):
    """GET /api/tasks/{id}/traces — List all pipeline traces for a task."""
    lf = _get_langfuse()
    if lf is None:
        _json_response(handler, {"traces": [], "error": "LangFuse not available"})
        return
    try:
        # Query traces by session_id (which we set to task_id)
        result = lf.api.trace.list(session_id=task_id, order_by="timestamp.asc")
        traces = []
        for t in (result.data if hasattr(result, 'data') else []):
            # Summarize input/output for display
            input_data = getattr(t, 'input', None)
            output_data = getattr(t, 'output', None)
            input_summary = ""
            output_summary = ""
            if isinstance(input_data, dict):
                input_summary = json.dumps(input_data, default=str)[:200]
            elif isinstance(input_data, str):
                input_summary = input_data[:200]
            if isinstance(output_data, dict):
                output_summary = json.dumps(output_data, default=str)[:200]
            elif isinstance(output_data, str):
                output_summary = output_data[:200]

            traces.append({
                "trace_id": getattr(t, 'id', ''),
                "name": getattr(t, 'name', 'unknown'),
                "timestamp": str(getattr(t, 'timestamp', '')),
                "input_summary": input_summary,
                "output_summary": output_summary,
                "level": getattr(t, 'level', 'DEFAULT'),
                "metadata": getattr(t, 'metadata', {}),
                "scores": [],  # populated below
            })

        # Fetch scores via REST (the SDK score read path is broken on this
        # LangFuse version — see langfuse_client.list_scores). One pull,
        # filtered to this task's traces, then mapped back per trace.
        try:
            from langfuse_client import list_scores
            trace_ids = [t["trace_id"] for t in traces if t["trace_id"]]
            score_map = {}
            for s in list_scores(trace_ids=trace_ids):
                score_map.setdefault(s.get("traceId"), []).append({
                    "name": s.get("name", ""),
                    "value": s.get("value"),
                    "comment": s.get("comment", "") or "",
                    "data_type": s.get("dataType", "NUMERIC"),
                })
            for trace in traces:
                trace["scores"] = score_map.get(trace["trace_id"], [])
        except Exception:
            pass

        _json_response(handler, {"traces": traces, "task_id": task_id})
    except Exception as e:
        _json_response(handler, {"traces": [], "error": str(e)}, status=500)


def handle_score_trace(handler, task_id, trace_id):
    """POST /api/tasks/{id}/traces/{trace_id}/score — Score a pipeline step."""
    try:
        from langfuse_client import score_trace, _is_enabled
        if not _is_enabled():
            _error_response(handler, "LangFuse not available", status=503)
            return

        body = _read_request_body(handler)
        score_value = body.get("score")
        comment = body.get("comment", "")

        if score_value is None:
            _error_response(handler, "Missing 'score' field (0 or 1)")
            return

        result = score_trace(trace_id, "human-feedback", float(score_value), comment=comment, data_type="NUMERIC")
        _json_response(handler, {"ok": result is not None, "trace_id": trace_id, "score": score_value})
    except Exception as e:
        _error_response(handler, f"Scoring failed: {e}", status=500)


def handle_annotate_trace(handler, task_id, trace_id):
    """POST /api/tasks/{id}/traces/{trace_id}/annotation — Annotate a pipeline step."""
    try:
        from langfuse_client import score_trace, _is_enabled
        if not _is_enabled():
            _error_response(handler, "LangFuse not available", status=503)
            return

        body = _read_request_body(handler)
        comment = body.get("comment", "")
        category = body.get("category", "feedback")

        if not comment:
            _error_response(handler, "Missing 'comment' field")
            return

        result = score_trace(trace_id, "human-annotation", category, comment=comment, data_type="CATEGORICAL")
        _json_response(handler, {"ok": result is not None, "trace_id": trace_id})
    except Exception as e:
        _error_response(handler, f"Annotation failed: {e}", status=500)


# ─── Cron Job API Handlers ───────────────────────────────────────────────────

def handle_list_cron_jobs(handler):
    """GET /api/cron — List all cron jobs."""
    jobs = cron_lib.list_jobs()
    _json_response(handler, {"jobs": jobs})


def handle_list_cadence(handler):
    """GET /api/cadence — List cadence program families (read-only)."""
    try:
        payload = program_lib.build_cadence_payload()
        _json_response(handler, payload)
    except Exception as e:
        _error_response(handler, f"Failed to load cadence: {e}", status=500)


def handle_get_cron_job(handler, job_id):
    """GET /api/cron/{id} — Get a single cron job."""
    job = cron_lib.get_job(job_id)
    if job is None:
        _error_response(handler, f"Cron job not found: {job_id}", status=404)
        return
    _json_response(handler, job)


def handle_parse_cron(handler):
    """POST /api/cron/parse — NLP parse free text into structured cron job fields."""
    body = _read_request_body(handler)
    text = body.get("text", "").strip()
    if not text:
        _error_response(handler, "Missing 'text' field", status=400)
        return
    try:
        from parse_cron_input import parse_cron
        parsed = parse_cron(text)
        _json_response(handler, {"status": "ok", "parsed": parsed})
    except Exception as e:
        _error_response(handler, f"Parse failed: {e}", status=500)


def handle_confirm_cron_job(handler):
    """POST /api/cron/confirm — Save a reviewed/edited cron job."""
    body = _read_request_body(handler)
    job_data = body.get("job", body)

    name = job_data.get("name")
    cron_expr = job_data.get("cron_expr")
    cron_human = job_data.get("cron_human", "")
    task_template = job_data.get("task_template", {})
    expires = job_data.get("expires")
    auto_dispatch = job_data.get("auto_dispatch", True)
    raw_input = job_data.get("raw_input", "")

    if not name:
        _error_response(handler, "Missing job name", status=400)
        return
    if not cron_expr:
        _error_response(handler, "Missing cron expression", status=400)
        return

    try:
        job = cron_lib.create_job(
            name=name,
            cron_expr=cron_expr,
            cron_human=cron_human,
            task_template=task_template,
            expires=expires,
            auto_dispatch=auto_dispatch,
            raw_input=raw_input,
        )
        _json_response(handler, {"status": "ok", "job": job})
    except ValueError as e:
        _error_response(handler, str(e), status=400)
    except Exception as e:
        _error_response(handler, f"Failed to create job: {e}", status=500)


def handle_cron_preview(handler):
    """POST /api/cron/preview — Return next N run times for a cron expression."""
    body = _read_request_body(handler)
    cron_expr = body.get("cron_expr", "").strip()
    count = body.get("count", 5)
    if not cron_expr:
        _error_response(handler, "Missing 'cron_expr' field", status=400)
        return
    valid, err = cron_lib.validate_cron_expr(cron_expr)
    if not valid:
        _error_response(handler, f"Invalid cron expression: {err}", status=400)
        return
    runs = cron_lib.compute_next_runs(cron_expr, count=count)
    _json_response(handler, {"runs": runs, "cron_expr": cron_expr})


def handle_update_cron_job(handler, job_id):
    """POST /api/cron/{id} — Update cron job fields."""
    body = _read_request_body(handler)
    changes = body.get("changes", body)
    # Don't allow changing the ID
    changes.pop("id", None)

    job = cron_lib.update_job(job_id, changes)
    if job is None:
        _error_response(handler, f"Cron job not found: {job_id}", status=404)
        return
    _json_response(handler, {"status": "ok", "job": job})


def handle_toggle_cron_job(handler, job_id):
    """POST /api/cron/{id}/toggle — Enable/disable a cron job."""
    job = cron_lib.toggle_job(job_id)
    if job is None:
        _error_response(handler, f"Cron job not found: {job_id}", status=404)
        return
    _json_response(handler, {"status": "ok", "job": job})


def handle_run_cron_job(handler, job_id):
    """POST /api/cron/{id}/run — Execute a cron job immediately."""
    job = cron_lib.get_job(job_id)
    if job is None:
        _error_response(handler, f"Cron job not found: {job_id}", status=404)
        return
    try:
        task_id, _ = cron_lib.execute_job(job)
        _json_response(handler, {"status": "ok", "task_id": task_id, "job_id": job_id})
    except Exception as e:
        _error_response(handler, f"Execution failed: {e}", status=500)


def handle_delete_cron_job(handler, job_id):
    """POST /api/cron/{id}/delete — Delete a cron job."""
    if cron_lib.delete_job(job_id):
        _json_response(handler, {"status": "ok", "message": f"Deleted {job_id}"})
    else:
        _error_response(handler, f"Cron job not found: {job_id}", status=404)


# ─── Worker API Handlers ─────────────────────────────────────────────────────

def handle_list_workers(handler):
    """GET /api/workers — file-backed worker definitions enriched with tier,
    resolved model at current posture, and pack membership (read-only)."""
    try:
        _json_response(handler, {"workers": workers_payload()})
    except Exception as e:
        _error_response(handler, f"Failed to load workers: {e}", status=500)


# ─── Request Handler ──────────────────────────────────────────────────────────

class TaskServerHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    """HTTP request handler for the task server.

    Routes:
      /              → ui/task-board/index.html
      /api/...       → API handlers
      everything else → static files from ui/task-board/
    """

    def __init__(self, *args, **kwargs):
        # Set the static file serving directory
        super().__init__(*args, directory=UI_DIR, **kwargs)

    # Suppress default access logging to keep output clean
    def log_message(self, format, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def _route_request(self, method):
        """Central router for all requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query_params = parse_qs(parsed.query)

        # ─── LangFuse API routes ────────────────────────────────────────
        if path == "/api/langfuse/health" and method == "GET":
            handle_langfuse_health(self)
            return True

        if path == "/api/langfuse/prompts" and method == "GET":
            handle_langfuse_prompts(self)
            return True

        if path == "/api/langfuse/traces/stats" and method == "GET":
            handle_langfuse_trace_stats(self)
            return True

        # ─── Cron API routes ───────────────────────────────────────────

        # Specific cron action routes (must come before generic /api/cron/{id})
        match = re.match(r"^/api/cron/([^/]+)/toggle$", path)
        if match and method == "POST":
            handle_toggle_cron_job(self, match.group(1))
            return True

        match = re.match(r"^/api/cron/([^/]+)/run$", path)
        if match and method == "POST":
            handle_run_cron_job(self, match.group(1))
            return True

        match = re.match(r"^/api/cron/([^/]+)/delete$", path)
        if match and method == "POST":
            handle_delete_cron_job(self, match.group(1))
            return True

        if path == "/api/cron/parse" and method == "POST":
            handle_parse_cron(self)
            return True

        if path == "/api/cron/confirm" and method == "POST":
            handle_confirm_cron_job(self)
            return True

        if path == "/api/cron/preview" and method == "POST":
            handle_cron_preview(self)
            return True

        # Generic cron routes
        match = re.match(r"^/api/cron/([^/]+)$", path)
        if match:
            job_id = match.group(1)
            if method == "GET":
                handle_get_cron_job(self, job_id)
                return True
            elif method == "POST":
                handle_update_cron_job(self, job_id)
                return True

        if path == "/api/cron" and method == "GET":
            handle_list_cron_jobs(self)
            return True

        if path == "/api/cadence" and method == "GET":
            handle_list_cadence(self)
            return True

        # ─── Worker API routes ─────────────────────────────────────────

        if path == "/api/workers" and method == "GET":
            handle_list_workers(self)
            return True

        # ─── Adapt API routes ──────────────────────────────────────────
        # POST /api/adapt - run a build turn (decoupled SSE). GET stream
        # reconnects. Rail CRUD lives under /api/adaptations. Specific action
        # paths precede the generic /api/adaptations/{id} rename route.

        if path == "/api/adapt" and method == "POST":
            handle_adapt(self)
            return True

        if path == "/api/adapt/stream" and method == "GET":
            handle_adapt_stream(self, query_params)
            return True

        if path == "/api/adaptations" and method == "GET":
            handle_list_adaptations(self)
            return True

        match = re.match(r"^/api/adaptations/([^/]+)/toggle$", path)
        if match and method == "PUT":
            handle_toggle_adaptation(self, unquote(match.group(1)))
            return True

        match = re.match(r"^/api/adaptations/([^/]+)/delete$", path)
        if match and method == "POST":
            handle_delete_adaptation(self, unquote(match.group(1)))
            return True

        match = re.match(r"^/api/adaptations/([^/]+)$", path)
        if match and method == "PUT":
            handle_rename_adaptation(self, unquote(match.group(1)))
            return True

        # ─── Task trace routes ─────────────────────────────────────────

        # Match /api/tasks/{id}/traces/{trace_id}/score
        match = re.match(r"^/api/tasks/([^/]+)/traces/([^/]+)/score$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            trace_id = match.group(2)
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_score_trace(self, task_id, trace_id)
            return True

        # Match /api/tasks/{id}/traces/{trace_id}/annotation
        match = re.match(r"^/api/tasks/([^/]+)/traces/([^/]+)/annotation$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            trace_id = match.group(2)
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_annotate_trace(self, task_id, trace_id)
            return True

        # Match /api/tasks/{id}/traces
        match = re.match(r"^/api/tasks/([^/]+)/traces$", path)
        if match and method == "GET":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_task_traces(self, task_id)
            return True

        # ─── Task API routes ───────────────────────────────────────────
        if path == "/api/tasks" and method == "GET":
            handle_list_tasks(self, query_params)
            return True

        # Quick Add — capture a task in plain language (exact path; must precede
        # the /api/tasks/{id}/... patterns below).
        if path == "/api/tasks/quick-add" and method == "POST":
            handle_quick_add(self)
            return True

        # Archived/completed tasks (Activity surface)
        if path == "/api/activity" and method == "GET":
            handle_list_activity(self, query_params)
            return True

        # Shadow-judge scoreboard (Quality surface)
        if path == "/api/quality" and method == "GET":
            handle_quality(self)
            return True

        # Match /api/tasks/{id}/dispatch
        match = re.match(r"^/api/tasks/([^/]+)/dispatch$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_dispatch_task(self, task_id)
            return True

        # Match /api/tasks/{id}/react
        match = re.match(r"^/api/tasks/([^/]+)/react$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_react(self, task_id)
            return True

        # Match /api/tasks/{id}/chat — POST runs a turn (SSE), GET reloads history.
        match = re.match(r"^/api/tasks/([^/]+)/chat$", path)
        if match and method in ("POST", "GET"):
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            elif method == "GET":
                handle_get_chat(self, task_id)
            else:
                handle_chat(self, task_id)
            return True

        # Match card-action verbs: /api/tasks/{id}/{accept|reject|graduate|keep|undo}
        _card_handlers = {
            "accept": handle_accept, "reject": handle_reject,
            "graduate": handle_graduate, "keep": handle_keep, "undo": handle_undo,
            "confirm": handle_confirm,
        }
        match = re.match(r"^/api/tasks/([^/]+)/(accept|reject|graduate|keep|undo|confirm)$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                _card_handlers[match.group(2)](self, task_id)
            return True

        # Match /api/tasks/{id}/rerun
        match = re.match(r"^/api/tasks/([^/]+)/rerun$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_rerun_task(self, task_id)
            return True

        # Match /api/tasks/{id}/schedule-meeting
        match = re.match(r"^/api/tasks/([^/]+)/schedule-meeting$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_schedule_meeting(self, task_id)
            return True

        # Match /api/tasks/{id}/publish-jira
        match = re.match(r"^/api/tasks/([^/]+)/publish-jira$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_publish_jira(self, task_id)
            return True

        # Match /api/tasks/{id}/meeting-details
        match = re.match(r"^/api/tasks/([^/]+)/meeting-details$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_update_meeting_details(self, task_id)
            return True

        # Match /api/tasks/{id}/done-and-delete
        match = re.match(r"^/api/tasks/([^/]+)/done-and-delete$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_complete_and_delete_task(self, task_id)
            return True

        # Match /api/tasks/{id}/done
        match = re.match(r"^/api/tasks/([^/]+)/done$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_complete_task(self, task_id)
            return True

        # Match /api/tasks/{id}/description
        match = re.match(r"^/api/tasks/([^/]+)/description$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_update_description(self, task_id)
            return True

        # Match /api/tasks/{id}/send-message  (must precede /message)
        match = re.match(r"^/api/tasks/([^/]+)/send-message$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_send_message(self, task_id)
            return True

        # Match /api/tasks/{id}/message
        match = re.match(r"^/api/tasks/([^/]+)/message$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_update_message(self, task_id)
            return True

        # Match /api/tasks/{type}/demote — kill switch. The slot carries a
        # task_type string (may contain hyphens, e.g. "send-message"), NOT a
        # numeric id, so it skips _parse_task_id and is unquoted before use.
        match = re.match(r"^/api/tasks/([^/]+)/demote$", path)
        if match and method == "POST":
            handle_demote(self, unquote(match.group(1)))
            return True

        # Match /api/tasks/{id}/comment
        match = re.match(r"^/api/tasks/([^/]+)/comment$", path)
        if match and method == "POST":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_add_comment(self, task_id)
            return True

        # Match /api/tasks/{id}/output — GET reads, PUT writes the .md artifact.
        match = re.match(r"^/api/tasks/([^/]+)/output$", path)
        if match and method in ("GET", "PUT"):
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            elif method == "GET":
                handle_get_output(self, task_id)
            else:
                handle_save_output(self, task_id)
            return True

        # Match /api/tasks/{id}
        match = re.match(r"^/api/tasks/([^/]+)$", path)
        if match and method == "GET":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_get_task(self, task_id)
            return True

        # People email cache
        if path == "/api/people/emails" and method == "GET":
            handle_people_emails(self)
            return True

        # Open file in Ghostty + NeoVim
        if path == "/open" and method == "GET":
            handle_open_file(self, query_params)
            return True

        # ─── Profile / Config API routes ───────────────────────────────
        if path == "/api/config/autonomy" and method == "GET":
            handle_get_autonomy(self)
            return True

        if path == "/api/config/autonomy" and method == "POST":
            handle_set_autonomy(self)
            return True

        if path == "/api/profile" and method == "GET":
            handle_get_profile(self)
            return True

        if path == "/api/profile/identity" and method == "PUT":
            handle_profile_identity(self)
            return True

        if path == "/api/profile/voice" and method == "PUT":
            handle_profile_voice(self)
            return True

        if path == "/api/profile/model-posture" and method == "PUT":
            handle_profile_model_posture(self)
            return True

        if path == "/api/profile/packs" and method == "POST":
            handle_profile_packs(self)
            return True

        match = re.match(r"^/api/profile/integrations/([^/]+)$", path)
        if match and method == "POST":
            handle_profile_integration(self, match.group(1))
            return True

        # Catch-all for unrecognized /api/ routes
        if path.startswith("/api/"):
            _error_response(self, f"Unknown API endpoint: {method} {path}", status=404)
            return True

        return False  # Not an API route

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests: API routes first, then static files."""
        if self._route_request("GET"):
            return

        # Static file serving: route / to index.html
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.path = "/index.html"

        # Add CORS header to static file responses
        super().do_GET()

    def do_POST(self):
        """Handle POST requests (API only)."""
        if self._route_request("POST"):
            return
        _error_response(self, "POST not allowed for this path", status=405)

    def do_PUT(self):
        """Handle PUT requests (API only)."""
        if self._route_request("PUT"):
            return
        _error_response(self, "PUT not allowed for this path", status=405)

    def end_headers(self):
        """Inject CORS header into all responses (including static files)."""
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


# ─── Server Startup ───────────────────────────────────────────────────────────

def main():
    # Ensure the UI directory exists (server still works for API-only use)
    if not os.path.isdir(UI_DIR):
        print(f"Warning: UI directory not found at {UI_DIR}")
        print("  API endpoints will work, but static file serving will fail.")
        print(f"  Create {UI_DIR}/index.html to serve the task board UI.")
        print()

    # Start cron scheduler background thread
    scheduler = CronScheduler()
    scheduler.start()

    # Start the deterministic Cadence reconcile scheduler (separate from cron:
    # in-process, never spawns an agent).
    cadence_scheduler = CadenceScheduler()
    cadence_scheduler.start()

    server = ReusableHTTPServer(("127.0.0.1", PORT), TaskServerHandler)
    print(f"PM-OS Task Server running at http://127.0.0.1:{PORT}")
    print(f"  API:    http://127.0.0.1:{PORT}/api/tasks")
    print(f"  Cron:   http://127.0.0.1:{PORT}/api/cron")
    print(f"  UI dir: {UI_DIR}")
    print("  Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        scheduler.stop()
        cadence_scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
