"""Per-adaptation event-log persistence for the Adapt build session.

Stores an adaptation's build stream as one normalized JSON event per line in a
sidecar `.events.jsonl` file under the adaptations store. This is the durable
log that live_runs.tail replays for reconnect: adapt_runner appends every
user-visible event, and the SSE handler (Task 10) reads it back to catch a
reconnecting client up before tailing the live run.

Mirrors scripts/chat_transcript.py's dumb-persistence shape (one JSON line per
event, stamp ts if absent, skip malformed lines on read) but keyed by
adaptation id and co-located with the adaptation record rather than tasks.

No LLM, no network - pure file append/read. ASCII-safe, portable (pathlib +
os.path only, no OS branch).
"""
import datetime
import json
import os

# Co-locate the event log with the adaptation records so a build's transcript
# lives beside its manifest. STORE_DIR mirrors adaptations_lib.STORE_DIR; it is
# resolved independently (not imported) so a test can monkeypatch either module
# without coupling them, and is rebound by tests pointing at a temp dir.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
STORE_DIR = os.path.join(PM_OS_DIR, "datasets", "adaptations")


def _log_path(adaptation_id):
    """Resolve the sidecar event-log path for an adaptation."""
    return os.path.join(STORE_DIR, f"{adaptation_id}.events.jsonl")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(adaptation_id, event):
    """Append one build event to the adaptation's event log.

    Shallow-copies the event, stamping `ts` if absent, then appends it as one
    JSON line. Creates the store dir if needed. Returns the stamped event.
    """
    e = dict(event)
    if "ts" not in e:
        e["ts"] = _now_iso()
    path = _log_path(adaptation_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=str) + "\n")
    return e


def read_events(adaptation_id):
    """Read all build events for an adaptation.

    Returns [] if the log doesn't exist. Parses each non-empty line, skipping
    malformed lines rather than crashing.
    """
    path = _log_path(adaptation_id)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return events
