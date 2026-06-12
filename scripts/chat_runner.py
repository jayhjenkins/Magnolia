"""Task-aware Claude chat panel runner.

Built incrementally across Tasks 4-7:
  - Task 4 (this file): pure builders — the `claude -p` argv and the
    de-personalized task-context system prompt for a NEW session's first turn.
  - Task 5: stream-json normalization.
  - Task 6: run_turn persistence (subprocess + file writes).
  - Task 7: locked-down tool allowlist (CHAT_ALLOWED_TOOLS).

The builder functions here are PURE: no subprocess, no file writes. Identity is
read ONLY through profile_lib (invariant #1) — never a hardcoded person literal.

`run_turn` (Task 6) is the impure orchestration seam: it reads the task, resolves
resume-vs-new, spawns `claude` behind the mockable `_spawn` seam, normalizes each
stdout line, persists tagged turns to the sidecar transcript, updates task
frontmatter, and yields each normalized event for the SSE route to stream.
"""
import datetime
import json
import os
import signal
import subprocess
import uuid

import platform_lib
import profile_lib
import task_lib
import chat_transcript

# Reuse dispatch's process-group teardown verbatim so chat and background runs
# share one battle-tested kill path (SIGTERM → wait(timeout=5) → SIGKILL the
# whole group). The import is side-effect-free: task_dispatch's module body is
# only imports + constant assignments, and the server already imports it.
from task_dispatch import _kill_process_group

# Repo root — the cwd `claude` runs in, mirroring task_dispatch.PM_OS_DIR.
PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─── Tier-2 safety boundary: the chat session's tool allowlist ───────────────
#
# This is the load-bearing Tier-2 gate for the chat panel (invariant #5).
# The dispatcher runs `claude` with bypassPermissions, so Claude's permission
# MODE is NOT the gate — the gate is that the chat session is simply never
# GRANTED any external-write tool. The chat can read/search/draft/edit LOCAL
# artifacts; when the operator says "send it", the FRONTEND surfaces a confirm
# and calls the existing gated board endpoint (e.g. /api/tasks/{id}/send-message),
# which runs the Tier-2 publish() confirm. So: chat prepares/drafts, the board
# publishes. The headless session can NEVER itself fire an external write.
#
# Therefore this allowlist DELIBERATELY EXCLUDES:
#   - the broad `mcp__*` wildcard (would grant every MCP write — Jira/Teams/
#     Slack/M365/calendar/Asana sends),
#   - every external-write MCP tool (createIssue, sendMessage, createEvent, …),
#   - `Bash(*)` / unrestricted Bash (could `curl`/`gh`/`mail` an external send).
# Bash is scoped to read-only git inspection subcommands only.
#
# Err on the side of fewer tools. To add a tool, prove it cannot write to the
# outside world.
CHAT_ALLOWED_TOOLS = [
    # Local artifact read / search / draft / edit — the whole point of the panel.
    "Read", "Grep", "Glob", "Write", "Edit",
    # Read-only git inspection only — narrowly scoped so Bash cannot shell out
    # to send (no plain `Bash`, no `Bash(*)`).
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)", "Bash(git status:*)",
    # The local task CLI. task.sh is a thin wrapper over task_cli.py, whose
    # ENTIRE surface (add/list/show/update/done/agent:*/inbox) is local task-file
    # mutation — it has NO external-write path (no http/requests/adapters/
    # publish). So this is the one non-git Bash exemption: it lets the chat act
    # on its OWN task (mark done, change status/priority) without dead-ending on
    # an approval the headless session can never grant. It cannot breach the
    # Tier-2 boundary because the wrapper physically cannot reach outside.
    "Bash(./scripts/task.sh:*)",
    # Read-only semantic search over the local PM-OS corpus (qmd). These query/
    # fetch only — qmd has no write surface.
    "mcp__qmd__query", "mcp__qmd__get", "mcp__qmd__multi_get", "mcp__qmd__status",
]


def build_chat_cmd(session_id, message, model, new_session=False, allowed_tools=None,
                   append_system_prompt=None, settings=None):
    """Build the `claude` argv for a chat turn.

    The prompt MUST stay the first positional arg: --allowedTools is variadic
    and would otherwise swallow a trailing prompt (verified in the CLI spike,
    see docs/plans/notes/claude-cli-verification.md). --allowedTools therefore
    goes LAST so nothing trails it.

    new_session=True  -> start a fresh session with --session-id <id>.
    new_session=False -> resume an existing session with --resume <id>.

    The Adapt build session (scripts/adapt_runner.py) reuses this builder via two
    optional middle flags (both default None, so existing chat callers are
    unaffected and the prompt-first / --allowedTools-last invariant holds):
      - append_system_prompt: re-injects the build harness every turn (the slash
        steering is not sticky across resumed turns).
      - settings: an absolute path to a --settings file (the Adapt fairway hook
        lives there; it is the REAL enforcement, so it must be passed).
    """
    tools = allowed_tools or ",".join(CHAT_ALLOWED_TOOLS)
    session_flag = "--session-id" if new_session else "--resume"
    cmd = [
        "claude",
        message,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        session_flag, session_id,
    ]
    if append_system_prompt is not None:
        cmd += ["--append-system-prompt", append_system_prompt]
    if settings is not None:
        cmd += ["--settings", settings]
    # --allowedTools stays LAST (variadic; nothing may trail it).
    cmd += ["--allowedTools", tools]
    return cmd


def _task_context_block(task, *, include_description=True, heading="## Task context"):
    """Build the bulleted task-context block (pure; no framing, no message).

    Always-present fields first, then any truthy optional ones. ``heading`` lets
    callers label it ("## Task context" for a fresh session vs. "## Current task
    state" on resume). ``include_description=False`` drops the (potentially long)
    body — used on resume, where the body already lives in session history and
    only the volatile metadata is worth re-sending each turn.
    """
    lines = [
        heading,
        f"- id: {task.get('id', '(none)')}",
        f"- title: {task.get('title', '(untitled)')}",
        f"- status: {task.get('status', '(unknown)')}",
        f"- priority: {task.get('priority', '(unset)')}",
        f"- queue: {task.get('queue', '(unset)')}",
    ]
    optional = [
        ("project", task.get("project")),
        ("domain", task.get("domain")),
        ("due", task.get("due")),
    ]
    if include_description:
        optional.append(("description", task.get("description") or task.get("body")))
    for label, value in optional:
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


# How much card body to embed inline. Generous (the first-turn prompt is cached),
# but capped so a giant artifact can't blow up the prompt — past the cap we tell
# the agent to read the file for the rest.
_CARD_BODY_CAP = 6000


def _card_body_block(body, *, current=False):
    """The card's actual contents, embedded inline so the agent can answer
    directly instead of spending a tool call to find what's right in front of
    the user. Returns a labelled block (trailing blank line). When the card has
    no body, returns a read-the-file instruction rather than nothing — so the
    agent never guesses about a card it hasn't actually looked at.
    """
    label = "### Card contents (current)" if current else "### Card contents"
    body = (body or "").strip()
    if not body:
        return (
            f"{label}\n"
            "(This card has no written body — read the task file and any linked "
            "artifacts before answering rather than guessing.)\n\n"
        )
    if len(body) > _CARD_BODY_CAP:
        body = body[:_CARD_BODY_CAP].rstrip() + "\n\n…(truncated)"
    return (
        f"{label}\n{body}\n\n"
        "If you need more than what's shown here, read the task file and any "
        "linked artifacts before answering.\n\n"
    )


# The shared "what you can and can't do from this chat" boundary, reused by the
# fresh-session framing and the background→live handoff so both stay in sync.
def _capability_boundary(name):
    return (
        f"What you can and can't do from here: you can read, search, and "
        f"draft/edit this task's local artifacts, and run the local task CLI "
        f"(./scripts/task.sh) to update or complete THIS task. You CANNOT perform "
        f"external writes (sending messages, filing tickets, scheduling) or run "
        f"other shell commands from this chat — those are gated behind buttons on "
        f"the task detail. If asked for one, say so plainly and point to the "
        f"button; never claim {name} can 'approve it in the terminal'."
    )


# The "don't rebuild Magnolia from this chat" boundary, reused by the fresh-session
# framing and the background→live handoff so both stay in sync. This is a behavioral
# guardrail, not a tool lock: the chat carries Write/Edit (to draft this task's
# artifacts), so it CAN edit engine files — and a headless session with a task's
# context baked in builds the engine badly (it produces janky, half-wired changes).
# So we intercept any request to change Magnolia ITSELF and redirect to the real
# build path — `/magnolia-build` in Claude Code on the Magnolia folder — where the
# brainstorm→plan→build→verify loop produces a far better result. It's role-agnostic
# and carries no person/team identity (invariant #1 spirit).
def _engine_change_boundary():
    return (
        "Where you are: this chat is the FRONT END of Magnolia — a place to work "
        "the task in front of you, not to rebuild Magnolia itself. You can do this "
        "task and draft/edit its artifacts freely. But if you're asked to change "
        "how Magnolia WORKS — add a new worker type, add a new card type, add an "
        "adapter or integration, or build any new capability into the system "
        "itself (anything beyond executing THIS task and its artifacts) — do NOT "
        "attempt it from here. A headless chat with one task's context baked in "
        "builds the engine badly, and past in-chat attempts produced broken, "
        "half-wired changes. Instead, take the idea seriously and point the way: "
        "say something like \"Great idea — to change what Magnolia can do, open "
        "Claude Code on the Magnolia folder, run /magnolia-build, and describe the "
        "feature you want to build.\" That build path gives a far better result. "
        "You can talk the idea through, but the building happens there, not here."
    )


def build_context_prompt(task, body, user_message):
    """Build the first-turn system/context prompt for a NEW chat session.

    Only the FIRST turn of a new session gets this prompt: it frames the
    assistant as the operator's chief of staff, embeds the task context AND the
    card's actual body (so it answers directly without searching), plus the
    operator's message. Resumed sessions get a lighter prompt (see
    ``build_resume_prompt``) — Claude Code owns the resumed conversation.

    Identity is read ONLY via profile_lib (display_name / company) — never a
    hardcoded person literal (invariant #1). The framing is role-agnostic: the
    engine isn't product-specific, so it says "getting things done", not work of
    any one discipline.
    """
    name = profile_lib.display_name()
    company = profile_lib.company()

    task_block = _task_context_block(task)
    body_block = _card_body_block(body)

    where = f" at {company}" if company else ""
    return (
        f"You are {name}'s chief of staff{where}, working inside Magnolia — their "
        f"right hand for getting things done. You're in a live, interactive "
        f"session with {name}, who has this task card open in front of them right "
        f"now. Your job is to help them get caught up on it and move it forward — "
        f"to DO the work, not just advise.\n\n"
        f"How you work:\n"
        f"- Lead with the answer or the action; keep it concise and warm. You "
        f"have the card's contents below and tools to go deeper — use them so you "
        f"genuinely understand the task before you respond, and never make {name} "
        f"wait while you hunt for something already in front of you.\n"
        f"- When {name} asks something loose (\"should I do this?\", \"what's your "
        f"take?\"), assume they mean THIS task, give a real, reasoned opinion, "
        f"then offer to take the next step.\n"
        f"- You can update, complete, and re-prioritize this task and draft or "
        f"edit its artifacts directly. When asked, just do it — then say briefly "
        f"what you did.\n"
        f"- Ask a clarifying question only when you're genuinely blocked and "
        f"can't make a sensible assumption.\n\n"
        f"{_capability_boundary(name)}\n\n"
        f"{_engine_change_boundary()}\n\n"
        f"{task_block}\n\n"
        f"{body_block}"
        f"## {name}'s message\n"
        f"{user_message}"
    )


def build_resume_prompt(task, user_message, *, first_interactive=False, body=None):
    """Build the prompt for a RESUMED session's turn.

    Two shapes:

    - **first_interactive** (the handoff): a human has just opened a chat on a
      session a background worker created. The session is still in autonomous
      "execute the assignment" mode, so we re-orient it ONCE — background → live
      chief of staff — AND re-inject the CURRENT body so it catches the user up
      with real content, not just metadata. (Without this, the agent knows the
      title but not what the card actually says — the starvation that the
      metadata-only refresh below caused on its own.)

    - **steady state** (default): the session already carries its framing and
      the body from the handoff/first turn, so we DON'T replay either. We only
      re-inject the compact, CURRENT task-state block — volatile fields
      (status / priority / queue / due) can change between turns, and giving
      them inline means the model reads live state for free instead of spending
      a tool call to re-fetch metadata we already hold.

    Identity via profile_lib only (invariant #1).
    """
    name = profile_lib.display_name()
    if first_interactive:
        state_block = _task_context_block(
            task, include_description=False, heading="## Current task state"
        )
        return (
            f"[Handoff] Until now you've been working this task on your own, in "
            f"the background. {name} has just opened it and started talking to "
            f"you directly — so you're moving from autonomous background work to "
            f"a live, interactive conversation. From here you're {name}'s chief "
            f"of staff for this task: help them get caught up on what it is and "
            f"what you've done so far, and get things done together. Be "
            f"proactive, concise, and willing to take a clear position; you "
            f"already know this task, so answer directly instead of re-searching "
            f"for the basics, and reach for your tools only to go deeper or to "
            f"act. Ask only when you're genuinely blocked.\n\n"
            f"{_capability_boundary(name)}\n\n"
            f"{_engine_change_boundary()}\n\n"
            f"{state_block}\n\n"
            f"{_card_body_block(body, current=True)}"
            f"## {name}'s message\n"
            f"{user_message}"
        )
    state_block = _task_context_block(
        task, include_description=False, heading="## Current task state"
    )
    return (
        f"{state_block}\n"
        f"(These are the task's current values — they may have changed since the "
        f"last turn; trust them over anything earlier in the conversation.)\n\n"
        f"## {name}'s message\n"
        f"{user_message}"
    )


# Order in which we probe a tool_use's input dict for a human-readable target.
_TARGET_KEYS = ("file_path", "path", "pattern", "command", "query", "url", "description")


def normalize(raw_event):
    """Map one raw `claude --output-format stream-json` event to a list of
    normalized UI events.

    The four UI kinds are: ``think``, ``tool_step``, ``text``, ``result``.
    An ``assistant`` event with multiple content blocks yields multiple rows,
    in order. Uninteresting or unknown events (``system``, ``user``/tool_result,
    ``rate_limit_event``, anything else) yield ``[]``. Pure: no I/O, never
    raises on missing/malformed ``message``/``content``.
    """
    if not isinstance(raw_event, dict):
        return []

    etype = raw_event.get("type")

    if etype == "assistant":
        out = []
        message = raw_event.get("message") or {}
        content = message.get("content") or []
        if not isinstance(content, list):
            return []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                out.append({
                    "kind": "text",
                    "role": "assistant",
                    "text": block.get("text", ""),
                })
            elif btype == "thinking":
                out.append({
                    "kind": "think",
                    "role": "assistant",
                    "text": block.get("thinking", ""),
                })
            elif btype == "tool_use":
                tool_input = block.get("input") or {}
                target = ""
                for key in _TARGET_KEYS:
                    value = tool_input.get(key)
                    if value:
                        target = str(value)
                        break
                out.append({
                    "kind": "tool_step",
                    "role": "assistant",
                    "verb": block.get("name", ""),
                    "target": target,
                })
        return out

    if etype == "result":
        return [{
            "kind": "result",
            "usage": raw_event.get("usage", {}),
            "cost": raw_event.get("total_cost_usd"),
            "session_id": raw_event.get("session_id"),
            # Every tool the session was NOT allowed to run lands here (verified
            # against the real CLI). run_turn turns a non-empty list into a human
            # `notice` so the user isn't left with the model's misleading
            # "approve in the terminal" narration.
            "permission_denials": raw_event.get("permission_denials") or [],
        }]

    # system, user/tool_result, rate_limit_event, and anything else: noise.
    return []


# ─── Task 6: orchestration seam ──────────────────────────────────────────────

def _blocked_tool_notice(denials):
    """Build the human "I can't do that from here" message from a result event's
    ``permission_denials``, or ``None`` when nothing was blocked.

    A denial isn't a failure: the chat is deliberately sandboxed to drafting +
    local task ops (invariant #5), so anything reaching the outside world is
    denied — and the headless session has no terminal to grant approval on. So
    we don't echo the model's misleading "approve in the terminal" line; we name
    what it tried and point to the task-detail action buttons (the gated,
    one-tap-confirm path: agent drafts → board publishes).
    """
    if not denials:
        return None
    first = denials[0] if isinstance(denials[0], dict) else {}
    tool = first.get("tool_name") or "that"
    tool_input = first.get("tool_input") or {}
    target = ""
    if isinstance(tool_input, dict):
        target = tool_input.get("command") or tool_input.get("url") or tool_input.get("file_path") or ""
    tried = f"run `{target}`" if target else f"use {tool}"
    return (
        f"I just tried to {tried} for you, but I can't do that from this chat — "
        f"it needs an approval this panel can't grant (there's no terminal here "
        f"to say yes to it). From here I can read, search, and draft on this "
        f"task. For anything that reaches outside that — sending a message, "
        f"filing a ticket, scheduling — use the action buttons on the task detail "
        f"to the left; they run it with a one-tap confirm. Just click the button "
        f"this time and I'll pick up from there."
    )


def _now_iso():
    """Current UTC time as an ISO-8601 'Z' string (mirrors task_lib._now_iso)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chat_env():
    """Env for the claude subprocess.

    Thin seam over the cross-platform launch abstraction: strips every
    CLAUDE*/CMUX_CLAUDE* var (so the child doesn't detect a nested session) and
    fixes PATH per-OS so `claude` resolves under cron/headless.
    """
    return platform_lib.headless_claude_env()


def _spawn(cmd, exit_holder=None):
    """Spawn `claude` and yield its stdout lines, owning the process lifecycle.

    Thin, monkeypatchable seam so run_turn's logic is testable without invoking
    the real CLI. stderr is discarded; we iterate stdout line-by-line so the SSE
    route can stream events as they arrive.

    Lifecycle ownership (C1): the child is launched in its OWN session/process
    group (via platform_lib.process_group_kwargs()) so the whole `claude` process TREE can be
    killed as a unit. A try/finally guarantees teardown even when the consumer
    closes the generator early — the normal SSE case, where the browser
    disconnects and the generator receives GeneratorExit at the `yield`. On any
    exit we close the pipe and, if the process is still running, SIGTERM →
    wait → SIGKILL the group via the shared dispatch helper. This is what
    prevents orphaned/zombie `claude` trees under the long-lived server.

    The subprocess return code is written into ``exit_holder['returncode']``
    (when a dict is supplied) so run_turn can detect abnormal termination (I1).
    """
    if cmd and cmd[0] == "claude":
        cmd = [platform_lib.resolve_claude()] + list(cmd[1:])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=PM_OS_DIR,
        env=_chat_env(),
        **platform_lib.process_group_kwargs(),  # own process group for clean kill (C1)
    )
    try:
        yield from proc.stdout
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        # Normal end-of-stream: reap. Early close / mid-stream: kill the group.
        if proc.poll() is None:
            _kill_process_group(proc)
        if exit_holder is not None:
            exit_holder["returncode"] = proc.returncode


def _persist_chat_session(task_id, session_id):
    """Best-effort: record the resumable chat session id + last-active on the task.

    Mirrors task_dispatch._persist_session_id — persistence must never crash a
    turn that is already streaming, so swallow everything. Folds the
    chat_last_active stamp into the SAME read-modify-write as the session fields
    (I2): on the new-session path we already know we're rewriting frontmatter,
    so there's no reason to do a second full cycle just to touch last-active.
    """
    try:
        task_lib.update_task(task_id, {
            "claude_session_id": session_id,
            "session_origin": "human_chat",
            "chat_last_active": _now_iso(),
        })
    except Exception:
        pass


def _touch_last_active(task_id):
    """Best-effort: stamp chat_last_active after a turn completes."""
    try:
        task_lib.update_task(task_id, {"chat_last_active": _now_iso()})
    except Exception:
        pass


def run_turn(task_id, message):
    """Run one chat turn for a task: resolve session, run claude, persist, yield.

    Generator. Yields normalized event dicts (think / tool_step / text / result)
    in stream order. Each yielded non-result event is also appended to the task's
    sidecar transcript, stamped with this turn's run_id and origin="chat".

    Session resolution:
      - new session (no claude_session_id): send the full first-turn context
        prompt, mint a fresh session id, pass new_session=True. On the result
        event, persist the session id + session_origin="human_chat" so the next
        turn resumes.
      - resume: send the message as-is against the existing session id.

    The user message is persisted FIRST (its original text, not the context
    wrapper), tagged post_run — True once the agent has already had a first pass
    on this task (a session exists OR agent_status == "complete").
    """
    task = task_lib.read_task(task_id)
    fm = task["frontmatter"] or {}
    body = task.get("body") or ""

    existing_sid = fm.get("claude_session_id")
    new_session = not existing_sid
    # First interactive turn on a session a BACKGROUND WORKER created: the
    # dispatcher stamps session_origin (e.g. "background_agent"); chat flips it to
    # "human_chat" after the handoff. So "resume + origin != human_chat" == the
    # one moment we re-orient background → live.
    first_interactive = bool(existing_sid and fm.get("session_origin") != "human_chat")
    # post_run marks the user msg as a follow-up AFTER the agent's first pass.
    post_run = bool(fm.get("agent_status") == "complete" or existing_sid)

    run_id = uuid.uuid4().hex

    # Resolve the chat model the same way dispatch does (model > tier override).
    model = profile_lib.resolve_model(None, task_override=fm.get("model") or fm.get("tier"))

    if new_session:
        # claude --session-id requires a canonical hyphenated UUID (verified in
        # the CLI spike: a bare uuid4().hex is rejected with "Must be a valid UUID").
        minted_sid = str(uuid.uuid4())
        sid = minted_sid
        sent_message = build_context_prompt(fm, body, message)
    else:
        minted_sid = None
        sid = existing_sid
        # Resume: re-inject current task state inline (no tool call to re-fetch).
        # On the FIRST interactive turn, also re-orient background → live and
        # re-inject the body for the catch-up. The ORIGINAL message is persisted.
        sent_message = build_resume_prompt(
            fm, message, first_interactive=first_interactive, body=body
        )

    # 1) Persist the USER turn first — the operator's ORIGINAL message.
    chat_transcript.append_event(task_id, {
        "role": "user",
        "kind": "text",
        "text": message,
        "run_id": run_id,
        "origin": "chat",
        "post_run": post_run,
    })

    cmd = build_chat_cmd(
        session_id=sid,
        message=sent_message,
        model=model,
        new_session=new_session,
    )

    result_sid = None
    saw_result = False
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

            # Drop blank thinking rows — never persist or yield empty think.
            if kind == "think" and not (event.get("text") or "").strip():
                continue

            if kind == "result":
                # Metadata: yield it for the UI, but do not append as a message.
                saw_result = True
                result_sid = event.get("session_id") or result_sid
                if new_session and result_sid:
                    _persist_chat_session(task_id, result_sid)
                # A blocked tool (headless can't prompt for approval) → surface a
                # human notice BEFORE the result finalizes the turn, and persist
                # it so a transcript reload shows it (parallels the error path).
                notice_text = _blocked_tool_notice(event.get("permission_denials"))
                if notice_text:
                    notice = {
                        "kind": "notice",
                        "role": "notice",
                        "text": notice_text,
                        "run_id": run_id,
                        "origin": "chat",
                    }
                    try:
                        chat_transcript.append_event(task_id, dict(notice))
                    except Exception:
                        pass
                    yield notice
                yield event
                continue

            # think / tool_step / text: stamp, persist, yield.
            event["run_id"] = run_id
            event["origin"] = "chat"
            chat_transcript.append_event(task_id, event)
            yield event

    # New session: ensure the resumable id is on the task even if the result
    # carried no session_id (fall back to the minted id). This call also stamps
    # chat_last_active in the SAME write (I2), so the new-session path needs no
    # separate _touch_last_active.
    if new_session and not result_sid:
        _persist_chat_session(task_id, minted_sid)

    # I1: a clean run ALWAYS ends with a `result` event. If we never saw one —
    # claude died, was killed, or the stream ended abnormally (a non-zero exit
    # is a strong corroborating signal but not required) — surface a final,
    # recoverable error event so the SSE layer can emit an error frame and the
    # frontend can offer retry. Persist it too so a transcript reload shows it.
    if not saw_result:
        error_event = {
            "kind": "error",
            "role": "error",
            "text": "The assistant run ended unexpectedly. You can retry.",
            "run_id": run_id,
            "origin": "chat",
        }
        try:
            chat_transcript.append_event(task_id, dict(error_event))
        except Exception:
            pass
        yield error_event

    # Resume path: stamp last-active. On the FIRST interactive turn (the handoff
    # from a background-worker session), also flip session_origin to "human_chat"
    # in the SAME write — so the heavy re-orientation preamble fires exactly once
    # and every later turn falls through to the light steady-state refresh.
    if not new_session:
        if first_interactive:
            try:
                task_lib.update_task(task_id, {
                    "session_origin": "human_chat",
                    "chat_last_active": _now_iso(),
                })
            except Exception:
                pass
        else:
            _touch_last_active(task_id)
