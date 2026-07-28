"""onboard_runner.py - drive the headless onboarding session.

Sibling to adapt_runner, but for the first-run front door: it drives a headless
multi-turn `claude -p` session that invokes the `meta-onboard` skill and walks a
brand-new user through setup (identity, integrations, doctor, board spawn). The
board's onboarding room POSTs each user turn here and streams the result.

It reuses chat_runner's battle-tested pieces verbatim:
  - build_chat_cmd (with append_system_prompt + settings) for the argv,
  - normalize for stream-json -> UI events,
  - _spawn for the process-group-owning subprocess generator.

What is DIFFERENT from adapt_runner:
  - SINGLE SESSION. Onboarding is one run, not one-per-adaptation. The session
    id is persisted to a tiny state file (SESSION_STATE_PATH); the first turn
    mints + persists it (new_session), every later turn --resumes it.
  - The transcript is single-session too (onboard_transcript, no id key).
  - COMPLETION DETECTION via a literal sentinel. meta-onboard's final step prints
    the exact line `ONBOARDING_COMPLETE`; when a normalized text event carries it
    we emit a synthetic `onboarding_complete` event so the server/UI can flip the
    first-run gate and reveal the board. (meta-onboard ALSO writes the durable
    profile marker via profile_lib.mark_onboarded; the sentinel is the in-stream
    signal, the marker is the persisted gate.)

SECURITY - why the allowlist is BROAD (the opposite of chat_runner):
  ONBOARD_ALLOWED_TOOLS grants `Bash`, Read/Grep/Glob/Write/Edit,
  `mcp__claude_ai_*`, `mcp__qmd__*`, and `Skill`. Onboarding GENUINELY installs
  tools (npm install qmd), runs auth (mgc login, otter_auth), copies datasets,
  writes the profile, spins up the board, and invokes the meta-onboard Skill -
  none of which the locked-down CHAT_ALLOWED_TOOLS could do. This is no more
  dangerous than typing `onboard me` is today (same power, UI-driven). The REAL
  bound is the --settings fairway hook (scripts/hooks/onboard_settings.json,
  passed on EVERY turn): writes are confined to the repo, destructive bash is
  denied. Do NOT widen this allowlist beyond what onboarding needs.

Identity is read ONLY via profile_lib (invariant #1). All files/comments are
ASCII (invariant #8). No OS branch - _spawn (via chat_runner) owns the portable
process launch.
"""
import json
import os
import uuid

import onboard_transcript
import profile_lib

# Reuse chat_runner's argv builder, normalizer, and the process-group-owning
# subprocess generator verbatim. _spawn is referenced through THIS module
# (onboard_runner._spawn) so tests monkeypatch it here.
from chat_runner import build_chat_cmd, normalize
from chat_runner import _spawn as _chat_spawn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)

# The fairway hook settings file (Task 3). This is the REAL bound on the broad
# allowlist, so it is passed on EVERY turn (like adapt_runner passes its own).
ONBOARD_SETTINGS_PATH = os.path.join(PM_OS_DIR, "scripts", "hooks", "onboard_settings.json")

# Persisted single-session id so the second and later turns --resume the same
# claude session. Lives under logs/ (always present, unlike profile/ which only
# exists after onboarding step 0). Mockable for tests.
SESSION_STATE_PATH = os.path.join(PM_OS_DIR, "logs", "onboard_session.json")

# The literal terminal sentinel meta-onboard prints at its final step. EXACT
# string - the runner greps for it in normalized text events.
COMPLETE_SENTINEL = "ONBOARDING_COMPLETE"


def _strip_sentinel(text):
    """Remove the completion sentinel from a text event's body.

    A line that is just the sentinel disappears entirely (no leftover blank
    line); an inline sentinel within other prose has just the token removed.
    Trailing whitespace the removal leaves behind is trimmed. Returns the
    cleaned text (possibly empty if the event carried only the sentinel).
    """
    lines = text.split("\n")
    kept = []
    for line in lines:
        if line.strip() == COMPLETE_SENTINEL:
            # A sentinel-only line: drop it entirely (no blank line left).
            continue
        kept.append(line.replace(COMPLETE_SENTINEL, ""))
    return "\n".join(kept).rstrip()

# Onboarding pins DEEP (opus): it runs a long, judgment-heavy concierge
# conversation with context discovery, program setup, and multi-step tool
# orchestration that benefits from stronger reasoning. Passed as a task_override
# so it wins over posture (profile_lib.resolve_model).
ONBOARD_MODEL_TIER = "deep"

# --- The HIGH-PRIVILEGE allowlist (see the module docstring for WHY) ---------
#
# BROAD on purpose - the opposite of chat_runner.CHAT_ALLOWED_TOOLS. Onboarding
# installs tools, runs auth, writes the profile, spins up the board, and invokes
# the meta-onboard Skill. The --settings fairway hook (ONBOARD_SETTINGS_PATH) is
# the REAL bound; this list just names which tool families exist. Do not widen
# beyond what onboarding needs.
ONBOARD_ALLOWED_TOOLS = [
    # Full shell: installers (npm install qmd), auth (mgc login, otter_auth),
    # cp datasets, python3 scripts/*, server spin-up. The fairway hook denies
    # destructive bash; everything else is allowed.
    "Bash",
    # Read/search the engine + write the profile, datasets, config, voice files.
    "Read", "Grep", "Glob", "Write", "Edit",
    # The claude.ai account connectors onboarding inherits + verifies (Granola,
    # M365, Jira, Pendo, Databricks, ...) and the local semantic-search engine.
    "mcp__claude_ai_*", "mcp__qmd__*",
    # Invoke the meta-onboard skill itself (and the sub-skills it calls, e.g.
    # workflow-doctor).
    "Skill",
]


def _spawn(cmd, exit_holder=None):
    """Spawn the onboarding session and yield its stdout lines.

    Thin pass-through to chat_runner._spawn (which owns the process group and
    sets cwd == PM_OS_DIR - required so the fairway hook resolves relative write
    paths against the repo root). Wrapped here as a module-level name so tests
    monkeypatch onboard_runner._spawn without touching chat_runner.
    """
    yield from _chat_spawn(cmd, exit_holder)


# --- Single-session state ----------------------------------------------------

def _read_session_id():
    """The persisted claude session id for the onboarding run, or None."""
    if not os.path.exists(SESSION_STATE_PATH):
        return None
    try:
        with open(SESSION_STATE_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("session_id") or None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _write_session_id(session_id):
    """Persist the onboarding session id (best-effort; never crashes a turn)."""
    try:
        os.makedirs(os.path.dirname(SESSION_STATE_PATH), exist_ok=True)
        with open(SESSION_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"session_id": session_id}, f)
    except OSError:
        pass


def _clear_session_id():
    """Forget the persisted onboarding session id (best-effort; never crashes).

    Called when onboarding completes so the completed/dead session is not
    --resumed by a later re-trigger; the next onboarding mints a fresh id.
    """
    try:
        if os.path.exists(SESSION_STATE_PATH):
            os.remove(SESSION_STATE_PATH)
    except OSError:
        pass


# --- The harness -------------------------------------------------------------

_HARNESS = """\
# Magnolia Onboarding - headless concierge harness

You are Magnolia, running HEADLESS behind the board's onboarding room to set up a
brand-new user. THIS HARNESS IS YOUR ONLY OPERATING NOTE for this session.

- Invoke the `meta-onboard` skill and follow it end to end. It is your script:
  identity, integrations, doctor pass, board spin-up, voice, packs, close.
- You are talking to a NON-TECHNICAL first-time user through a chat panel. Be the
  warm concierge meta-onboard describes - plain language, no jargon, no git, no
  model IDs.
- Browser-auth steps happen OUTSIDE this chat (a sign-in window pops up for
  Granola / M365 / qmd). NARRATE them in plain language: "a sign-in window just
  opened - finish it there and come back and tell me when you're done," then wait
  for the user before continuing. Never claim you can click it for them.
- Onboarding runs in ONE direction: do every step (identity through packs) here
  in this chat, then finish. NEVER open the board mid-flow, link it, or tell the
  user to "go look at it and come back" - there is no going back and forth. The
  board stays hidden behind this room until the very end.
- When onboarding is FULLY complete, meta-onboard's final step sets the
  completion marker and prints the literal line ONBOARDING_COMPLETE. Do not print
  that line yourself before onboarding is genuinely done - it reveals the board
  (the room runs the reveal automatically; you never open it yourself).

## Output discipline
ASCII-safe output everywhere (hyphen, not em-dash; straight quotes, not smart
quotes) - this text is runtime output and garbles on Windows terminals otherwise.
"""


def build_harness_prompt():
    """Return the onboarding harness system-prompt text (pure, stable, ASCII)."""
    return _HARNESS


# --- The turn ----------------------------------------------------------------

def run_turn(message):
    """Run one onboarding turn: drive the headless meta-onboard session, yield.

    Generator yielding normalized events (think / tool_step / text / result),
    plus a synthetic `onboarding_complete` event the moment a text event carries
    the ONBOARDING_COMPLETE sentinel (meta-onboard's terminal signal). The raw
    sentinel is STRIPPED from the user-visible/persisted text before that text
    event is yielded or logged (so it never paints into the prose or the durable
    transcript); a text event that carried ONLY the sentinel is dropped. Every
    user-visible event is appended to the single-session transcript for
    reconnect/replay.

    SINGLE SESSION: the FIRST turn (no persisted session id) mints a fresh id,
    sends `message` with the harness + broad allowlist + the fairway --settings,
    new_session=True, and persists the id on the result. Every LATER turn
    --resumes that id (new_session=False), re-injecting the harness (not sticky).
    """
    harness = build_harness_prompt()
    tools = ",".join(ONBOARD_ALLOWED_TOOLS)
    model = profile_lib.resolve_model(None, task_override=ONBOARD_MODEL_TIER)

    existing_sid = _read_session_id()
    new_session = not existing_sid
    if new_session:
        # claude --session-id requires a canonical hyphenated UUID.
        sid = str(uuid.uuid4())
        # A genuinely new onboarding run starts from a clean log: clear any stale
        # transcript (incl. a prior run's onboarding_complete) BEFORE the first
        # append. A resuming turn (session id present) skips this and keeps its
        # accruing log. First-ever run resets an empty log (harmless).
        onboard_transcript.reset()
    else:
        sid = existing_sid

    cmd = build_chat_cmd(
        session_id=sid,
        message=message,
        model=model,
        new_session=new_session,
        allowed_tools=tools,
        append_system_prompt=harness,
        settings=ONBOARD_SETTINGS_PATH,
    )

    completed = False  # guard so the synthetic complete event fires at most once
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

            # Drop blank thinking rows (mirrors chat_runner / adapt_runner).
            if kind == "think" and not (event.get("text") or "").strip():
                continue

            if kind == "result":
                # Metadata for the UI - yield, don't log. Persist the session id
                # (falling back to the minted --session-id sid) so the next turn
                # resumes; mirrors chat_runner's `result_sid or sid`. Skip the
                # write once onboarding has completed - the session is dead and
                # was just cleared, so persisting would resurrect it.
                if new_session and not completed:
                    _write_session_id(event.get("session_id") or sid)
                yield event
                continue

            # A text event carrying the terminal sentinel: strip the sentinel
            # from the user-visible/persisted text BEFORE persisting or yielding
            # (so the raw word ONBOARDING_COMPLETE never paints into the prose or
            # lands in the durable transcript), while still detecting completion
            # and firing the single-shot synthetic event below. If stripping
            # leaves only whitespace, the event carried nothing but the sentinel,
            # so we drop it entirely - but still fire completion.
            sentinel_here = (not completed and kind == "text"
                             and COMPLETE_SENTINEL in (event.get("text") or ""))
            if sentinel_here:
                cleaned = _strip_sentinel(event.get("text") or "")
                if cleaned:
                    event = dict(event)
                    event["text"] = cleaned
                    onboard_transcript.append_event(event)
                    yield event
                # else: the event was sentinel-only -> persist/yield nothing.
            else:
                # think / tool_step / text: persist then yield.
                onboard_transcript.append_event(event)
                yield event

            # Terminal sentinel detected -> emit the synthetic completion event
            # once (persisted + yielded) so the server flips the gate.
            if sentinel_here:
                completed = True
                complete_evt = {
                    "kind": "onboarding_complete",
                    "role": "system",
                    "text": "Onboarding complete - your board is live.",
                }
                onboard_transcript.append_event(dict(complete_evt))
                # Clear the completed/dead session so a re-trigger is a fresh
                # session + fresh log (a new run --session-id, not a --resume of
                # the dead one). The transcript reset happens on that next new run.
                _clear_session_id()
                yield complete_evt
