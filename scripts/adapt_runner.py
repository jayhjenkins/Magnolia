"""adapt_runner.py - drive the gated Adapt build session, capture the manifest.

Sibling to chat_runner, but un-bound from any task: it drives a headless
`claude -p` BUILD session confined to the four factory surfaces, and records
which artifacts (workers / adapters / card-types) the build produced into the
adaptation's manifest.

It reuses chat_runner's battle-tested pieces verbatim:
  - build_chat_cmd (extended with append_system_prompt + settings) for the argv,
  - normalize for stream-json -> UI events,
  - _spawn for the process-group-owning subprocess generator.

What is NEW here (vs. chat_runner.run_turn):
  - the session is a BUILD session: the build harness is re-injected every turn
    via --append-system-prompt, the locked-down ADAPT_ALLOWED_TOOLS is the
    allowlist, and --settings points at the fairway hook (the REAL enforcement),
  - it is keyed by adaptation_id (not a task id); a NEW build mints the row on
    the first result.session_id,
  - after the stream it diffs git HEAD to MAP new commits to factory surfaces
    and writes the adaptation manifest (the producer side of the ref convention
    the discovery seams consume),
  - auto-commit-to-main is the FACTORY's job (commit_and_emit_receipt); this
    runner only RECORDS the resulting commits.

Identity is read ONLY via profile_lib (invariant #1). All files/comments are
ASCII (invariant #8). Git runs through subprocess.run([...]) lists - no shell,
no OS branch (portable; the portability gate covers this file).
"""
import json
import os
import subprocess
import uuid

import adapt_harness
import adapt_tools
import adapt_transcript
import adaptations_lib
import compaction
import profile_lib

# Reuse chat_runner's argv builder, normalizer, and the process-group-owning
# subprocess generator verbatim. _spawn is referenced through THIS module
# (adapt_runner._spawn) so tests monkeypatch it here.
from chat_runner import build_chat_cmd, normalize
from chat_runner import _spawn as _chat_spawn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)

# The fairway hook settings file. Task 8 finding: this MUST be passed via
# --settings or only the weaker allowlist remains. Resolve to an absolute path
# under PM_OS_DIR (the claude subprocess runs with cwd == PM_OS_DIR, set by
# _spawn; the hook resolves relative write paths against that root).
ADAPT_SETTINGS_PATH = os.path.join(PM_OS_DIR, "scripts", "hooks", "adapt_settings.json")

# Factory-surface path prefixes (repo-relative, POSIX form as git reports them).
_WORKERS_PREFIX = "scripts/workers/"
_ADAPTERS_PREFIX = "scripts/adapters/"
_REGISTRY_REL = "ui/task-board/cardtypes/registry.json"

# How many chars of the user message become the provisional adaptation name.
_NAME_CAP = 48


# --- The spawn seam ----------------------------------------------------------

def _spawn(cmd, exit_holder=None):
    """Spawn the build session and yield its stdout lines.

    Thin pass-through to chat_runner._spawn (which owns the process group and
    sets cwd == PM_OS_DIR - required so the fairway hook resolves relative write
    paths against the repo root). Wrapped here as a module-level name so tests
    monkeypatch adapt_runner._spawn without touching chat_runner.
    """
    yield from _chat_spawn(cmd, exit_holder)


# --- Git seams (portable; mockable) ------------------------------------------

def _git(*args):
    """Run a git subcommand against the repo. No shell; portable."""
    return subprocess.run(
        ["git", "-C", PM_OS_DIR, *args], capture_output=True, text=True
    )


def _git_head():
    """Current HEAD sha, or empty string if unavailable (unborn HEAD)."""
    res = _git("rev-parse", "HEAD")
    return res.stdout.strip() if res.returncode == 0 else ""


def _git_new_commits(prev_head):
    """Shas committed since prev_head, OLDEST -> NEWEST.

    `git rev-list` lists newest-first; we reverse so the manifest records
    creation order (a later delete can then revert newest-first by reversing
    again). Empty / unavailable prev_head or no new commits -> [].
    """
    if not prev_head:
        return []
    res = _git("rev-list", "--reverse", f"{prev_head}..HEAD")
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _git_changed_paths(sha):
    """Repo-relative paths changed by commit `sha` (POSIX separators)."""
    res = _git("show", "--name-only", "--format=", sha)
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _parse_registry_card_types(text):
    """Parse the cardTypes keyset from a registry.json text blob.

    Pure (no git, no IO): takes the file contents, returns a set of cardType
    keys, or empty set if the text is missing or unparsable. Tested directly.
    """
    if not text:
        return set()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return set()
    card_types = data.get("cardTypes")
    if not isinstance(card_types, dict):
        return set()
    return set(card_types.keys())


def _registry_added_from_pair(before_text, after_text):
    """cardType keys present in `after_text` but not `before_text`.

    Pure (no git, no IO): the parse/diff core, fed registry JSON strings.
    Returns a sorted list of newly-added keys; [] if before == after.
    """
    after = _parse_registry_card_types(after_text)
    before = _parse_registry_card_types(before_text)
    return sorted(after - before)


def _registry_card_types(sha_ref):
    """Parse the cardTypes keys from the registry.json at a git ref.

    `sha_ref` is a `git show <ref>:<path>` revision spec target like "sha" or
    "sha^". Returns a set of cardType keys, or empty set if the file/ref is
    missing or unparsable.
    """
    res = _git("show", f"{sha_ref}:{_REGISTRY_REL}")
    if res.returncode != 0:
        return set()
    return _parse_registry_card_types(res.stdout)


def _git_registry_added_keys(sha):
    """cardType keys NEWLY ADDED by commit `sha`.

    Diffs the registry's cardTypes keyset between <sha>^ and <sha>. If the
    parent ref does not exist (first commit), treats every key as added.
    """
    after = _registry_card_types(sha)
    before = _registry_card_types(f"{sha}^")
    return sorted(after - before)


# --- Surface mapping ---------------------------------------------------------

def _adapter_ref(path):
    """Map a changed path to an adapter ref `<family>/<provider>`, or None.

    Only real provider modules count: scripts/adapters/<family>/<provider>.py
    where <provider> is not __init__ or a _contract module (those are plumbing,
    never a routable provider).
    """
    if not path.startswith(_ADAPTERS_PREFIX) or not path.endswith(".py"):
        return None
    rest = path[len(_ADAPTERS_PREFIX):]  # "<family>/<provider>.py"
    parts = rest.split("/")
    if len(parts) != 2:
        return None  # nested deeper / not a flat provider module
    family, filename = parts
    provider = filename[:-len(".py")]
    if provider == "__init__" or provider.startswith("_"):
        return None
    return f"{family}/{provider}"


def _capture_manifest(adaptation_id, prev_head):
    """Walk new commits since prev_head and write the adaptation's manifest.

    Returns the number of artifact entries added. Maps each changed path to a
    factory surface using the SAME ref convention the discovery seams consume:
      - scripts/workers/<name>.md -> "worker", ref = os.path.relpath(path,
        PM_OS_DIR) (the exact expression task_dispatch.load_workers uses),
      - scripts/adapters/<family>/<provider>.py -> "adapter", ref =
        "<family>/<provider>" (matching adapters.__init__.get),
      - registry.json -> "card-type", ref = each NEWLY ADDED cardType key
        (matching task_server's is_live("card-type", t.card_type)).
    add_artifact upserts on (surface, ref), so re-running a resume is idempotent.
    """
    added = 0
    for sha in _git_new_commits(prev_head):
        for path in _git_changed_paths(sha):
            if path.startswith(_WORKERS_PREFIX) and path.endswith(".md"):
                # Producer ref MUST equal the consumer's expression exactly.
                abs_path = os.path.join(PM_OS_DIR, path)
                ref = os.path.relpath(abs_path, PM_OS_DIR)
                adaptations_lib.add_artifact(adaptation_id, "worker", ref, sha)
                added += 1
            elif path.startswith(_ADAPTERS_PREFIX):
                ref = _adapter_ref(path)
                if ref:
                    adaptations_lib.add_artifact(adaptation_id, "adapter", ref, sha)
                    added += 1
            elif path == _REGISTRY_REL:
                for key in _git_registry_added_keys(sha):
                    adaptations_lib.add_artifact(adaptation_id, "card-type", key, sha)
                    added += 1
    return added


# --- Provisional naming ------------------------------------------------------

def _provisional_name(message):
    """First ~48 chars of the message, stripped; 'New adaptation' if empty."""
    text = (message or "").strip()
    if not text:
        return "New adaptation"
    return text[:_NAME_CAP].strip() or "New adaptation"


# --- The turn ----------------------------------------------------------------

def run_turn(adaptation_id, message):
    """Run one Adapt build turn: drive the gated session, capture the manifest.

    Generator yielding normalized events (think / tool_step / text / result),
    plus an `adaptation` event when a NEW build's row is minted, and a `notice`
    when the context window is getting full. Every user-visible event is also
    appended to the adaptation's event log for reconnect/replay.

    adaptation_id is None  -> NEW build: mint a session UUID, send `message`
      with the harness + ADAPT_ALLOWED_TOOLS + --settings, new_session=True. On
      the first result.session_id, create the row (state "building") and yield
      an `adaptation` event so the UI adds the rail row immediately.
    adaptation_id given    -> RESUME/edit: read the record, --resume its stored
      claude_session_id, re-inject the harness (not sticky), new_session=False.

    After the stream: diff git HEAD to map new commits to surfaces and write the
    manifest; if the manifest grew, flip state building -> off. If the context
    window is full, yield a compact `notice`; if the turn shipped, best-effort
    fire ONE follow-up /compact turn (housekeeping; failures swallowed, not
    streamed). Auto-commit-to-main is the FACTORY's job - this only records it.
    """
    harness = adapt_harness.build_harness_prompt()
    tools = ",".join(adapt_tools.ADAPT_ALLOWED_TOOLS)
    model = profile_lib.resolve_model(None)

    if adaptation_id is None:
        new_session = True
        sid = str(uuid.uuid4())
        provisional = _provisional_name(message)
    else:
        new_session = False
        rec = adaptations_lib.read(adaptation_id)
        sid = rec.get("claude_session_id")
        provisional = None

    cmd = build_chat_cmd(
        session_id=sid,
        message=message,
        model=model,
        new_session=new_session,
        allowed_tools=tools,
        append_system_prompt=harness,
        settings=ADAPT_SETTINGS_PATH,
    )

    # Snapshot HEAD before the turn so we can diff what the build committed.
    prev_head = _git_head()

    # Pre-id event window (new build): the row's id is only known once the first
    # result.session_id arrives, so events emitted before that are BUFFERED and
    # flushed to the log the instant the id is minted. They are still yielded
    # live the whole time (live_runs.tail handles the live stream); the buffer
    # only ensures the durable log is complete for a later reconnect/replay.
    # On a resume the id is known up front, so nothing buffers.
    pre_id_buffer = []
    current_id = adaptation_id
    shipped = False  # set True if this turn produced manifest artifacts
    result_usage = {}

    def _persist(event):
        """Append to the event log if the id exists yet; otherwise buffer."""
        if current_id is not None:
            adapt_transcript.append_event(current_id, event)
        else:
            pre_id_buffer.append(event)

    exit_holder = {}
    for line in _spawn(cmd, exit_holder):
        if not line or not line.strip():
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        for event in normalize(raw):
            kind = event.get("kind")

            # Drop blank thinking rows (mirrors chat_runner).
            if kind == "think" and not (event.get("text") or "").strip():
                continue

            if kind == "result":
                result_usage = event.get("usage") or {}
                # Fall back to the minted session id (the one we passed via
                # --session-id) if the result carries none - mirrors
                # chat_runner.run_turn's `result_sid or sid`. Without this a
                # result lacking session_id would never create the row, silently
                # dropping the buffered pre-id events and the manifest.
                result_sid = event.get("session_id") or sid
                # NEW build: mint the row on the first result, then flush the
                # pre-id buffer into the real log and announce the row so the UI
                # can add the rail entry immediately.
                if adaptation_id is None and current_id is None and result_sid:
                    current_id = adaptations_lib.create(provisional, result_sid)
                    adapt_evt = {
                        "kind": "adaptation",
                        "adaptation_id": current_id,
                        "name": provisional,
                        "state": "building",
                    }
                    adapt_transcript.append_event(current_id, adapt_evt)
                    for buffered in pre_id_buffer:
                        adapt_transcript.append_event(current_id, buffered)
                    pre_id_buffer = []
                    yield adapt_evt
                # The result itself is metadata for the UI - yield, don't log.
                yield event
                continue

            # think / tool_step / text: persist (or buffer) then yield.
            _persist(event)
            yield event

    # --- Post-turn: manifest capture ----------------------------------------
    # If a NEW build never produced a session_id (claude died before any
    # result), there is no row to attach a manifest to - nothing to capture.
    if current_id is not None:
        added = _capture_manifest(current_id, prev_head)
        if added > 0:
            shipped = True
            # Build finished and produced artifacts -> ready to toggle on.
            try:
                rec = adaptations_lib.read(current_id)
                if rec.get("state") == "building":
                    adaptations_lib.set_state(current_id, "off")
                    # Announce the flip so a live UI client (which only saw the
                    # earlier state: "building" event) updates the rail dot /
                    # toggle without re-reading the record. Persist it too so a
                    # reconnect/replay shows the final state.
                    flip_evt = {
                        "kind": "adaptation",
                        "adaptation_id": current_id,
                        "name": rec.get("name"),
                        "state": "off",
                    }
                    try:
                        adapt_transcript.append_event(current_id, dict(flip_evt))
                    except Exception:
                        pass
                    yield flip_evt
            except Exception:
                pass

    # --- Compaction signal --------------------------------------------------
    if compaction.should_recommend_compact(result_usage, model=model):
        notice = {
            "kind": "notice",
            "role": "notice",
            "text": ("This build session is getting long - consider running "
                     "/compact to keep it lean for the next turn."),
        }
        if current_id is not None:
            try:
                adapt_transcript.append_event(current_id, dict(notice))
            except Exception:
                pass
        yield notice

    # --- Post-ship housekeeping: one best-effort /compact turn --------------
    # Keep the session lean after a ship. This is HOUSEKEEPING: its events are
    # NOT streamed to the UI, and every failure is swallowed (a failed compact
    # must never fail the build that already succeeded).
    if shipped and current_id is not None:
        try:
            rec = adaptations_lib.read(current_id)
            compact_sid = rec.get("claude_session_id")
            if compact_sid:
                compact_cmd = build_chat_cmd(
                    session_id=compact_sid,
                    message=compaction.compact_turn_message(),
                    model=model,
                    new_session=False,
                    allowed_tools=tools,
                    append_system_prompt=harness,
                    settings=ADAPT_SETTINGS_PATH,
                )
                for _line in _spawn(compact_cmd, {}):
                    pass  # drain; do not parse, do not yield
        except Exception:
            pass
