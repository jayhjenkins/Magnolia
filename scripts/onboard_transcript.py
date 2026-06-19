"""Single-session onboarding transcript persistence.

Stores the onboarding run's stream as one normalized JSON event per line in a
`.jsonl` file. This is the durable log that live_runs.tail replays for reconnect:
onboard_runner appends every user-visible event, and the SSE handler reads it
back to catch a reconnecting client up before tailing the live run.

Mirrors scripts/chat_transcript.py / scripts/adapt_transcript.py's
dumb-persistence shape (one JSON line per event, stamp ts if absent, skip
malformed lines on read) but SINGLE-SESSION: onboarding is one run, so there is
no id key. Adds reset() so a re-run starts from a clean log.

Stored under <repo>/logs/ rather than the profile - the profile dir may not
exist yet on the FIRST onboarding turn (meta-onboard creates it at step 0), and
logs/ is always present. STORE is a module-level path so tests can monkeypatch
it at a tmp location.

No LLM, no network - pure file append/read. ASCII-safe, portable (os.path only,
no OS branch).
"""
import datetime
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
# logs/ is always present (it predates the live profile), so the first
# onboarding turn can persist before profile/ exists.
STORE = os.path.join(PM_OS_DIR, "logs", "onboard_transcript.jsonl")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(event):
    """Append one onboarding event to the transcript.

    Shallow-copies the event, stamping `ts` if absent, then appends it as one
    JSON line. Creates the store dir if needed. Returns the stamped event.
    """
    e = dict(event)
    if "ts" not in e:
        e["ts"] = _now_iso()
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=str) + "\n")
    return e


def read_events():
    """Read all onboarding events.

    Returns [] if the log doesn't exist. Parses each non-empty line, skipping
    malformed lines rather than crashing.
    """
    if not os.path.exists(STORE):
        return []
    events = []
    with open(STORE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return events


def reset():
    """Clear the transcript so a fresh onboarding run starts from a clean log.

    Safe when no file exists (a no-op).
    """
    if os.path.exists(STORE):
        os.remove(STORE)
