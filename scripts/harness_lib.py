"""Harness abstraction layer for CLI dispatch.

Every headless LLM call goes through one of the builder functions here.
Each builder returns a plain list[str] (the subprocess argv) for the
active harness. The builders are PURE: no subprocess, no file writes.
Callers own their own subprocess seam (preserving existing monkeypatch
points).

Supported harnesses:
  - claude (default): Claude CLI (claude -p)
  - codex: OpenAI Codex CLI (codex exec)
"""
import json
import platform_lib
import profile_lib


def _active(harness=None, root=None):
    return harness or profile_lib.harness(root)


# ---------------------------------------------------------------------------
# Pattern E: one-shot headless
# ---------------------------------------------------------------------------

def build_oneshot_cmd(prompt, model, harness=None, root=None,
                      allowed_tools=None, max_turns=None,
                      append_system_prompt=None, system_prompt=None,
                      permission_mode=None):
    """Build argv for a one-shot headless call (sentinel, judge, etc.).

    Returns (cmd_list, harness_name).
    """
    h = _active(harness, root)
    if h == "codex":
        return _codex_oneshot(prompt, model, allowed_tools, max_turns,
                              append_system_prompt, system_prompt,
                              permission_mode), h
    return _claude_oneshot(prompt, model, allowed_tools, max_turns,
                           append_system_prompt, system_prompt,
                           permission_mode), h


def _claude_oneshot(prompt, model, allowed_tools, max_turns,
                    append_system_prompt, system_prompt, permission_mode):
    cmd = [platform_lib.resolve_claude(), "-p", prompt,
           "--model", model, "--output-format", "json"]
    if system_prompt is not None:
        cmd += ["--system-prompt", system_prompt]
    if append_system_prompt is not None:
        cmd += ["--append-system-prompt", append_system_prompt]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    if permission_mode is not None:
        cmd += ["--permission-mode", permission_mode]
    if allowed_tools is not None:
        cmd += ["--allowedTools", allowed_tools]
    return cmd


def _codex_oneshot(prompt, model, allowed_tools, max_turns,
                   append_system_prompt, system_prompt, permission_mode):
    effective_prompt = prompt
    if system_prompt:
        effective_prompt = f"[SYSTEM]\n{system_prompt}\n[/SYSTEM]\n\n{prompt}"
    if append_system_prompt:
        effective_prompt = f"[SYSTEM]\n{append_system_prompt}\n[/SYSTEM]\n\n{effective_prompt}"
    binary = platform_lib.resolve_codex() or "codex"
    cmd = [binary, "exec", effective_prompt, "-m", model, "--json"]
    sandbox = "-s"
    if permission_mode == "bypassPermissions":
        cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        sandbox = None
    if sandbox:
        if allowed_tools and ("Bash" in allowed_tools or "Write" in allowed_tools
                              or "Edit" in allowed_tools):
            cmd += ["-s", "workspace-write"]
        else:
            cmd += ["-s", "read-only"]
    return cmd


# ---------------------------------------------------------------------------
# Pattern B/C/D: streaming chat (chat panel, Adapt, onboard)
# ---------------------------------------------------------------------------

def build_streaming_cmd(session_id, message, model, new_session=False,
                        allowed_tools=None, append_system_prompt=None,
                        settings=None, harness=None, root=None):
    """Build argv for a streaming chat session.

    Returns (cmd_list, harness_name).
    """
    h = _active(harness, root)
    if h == "codex":
        return _codex_streaming(session_id, message, model, new_session,
                                allowed_tools, append_system_prompt), h
    return _claude_streaming(session_id, message, model, new_session,
                             allowed_tools, append_system_prompt, settings), h


def _claude_streaming(session_id, message, model, new_session,
                      allowed_tools, append_system_prompt, settings):
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
    if allowed_tools is not None:
        cmd += ["--allowedTools", allowed_tools]
    return cmd


def _codex_streaming(session_id, message, model, new_session,
                     allowed_tools, append_system_prompt):
    effective_message = message
    if append_system_prompt:
        effective_message = f"[SYSTEM]\n{append_system_prompt}\n[/SYSTEM]\n\n{message}"
    binary = platform_lib.resolve_codex() or "codex"
    if new_session:
        cmd = [binary, "exec", effective_message, "-m", model, "--json"]
    else:
        cmd = [binary, "exec", "resume", session_id, effective_message,
               "-m", model, "--json"]
    cmd += ["-s", "workspace-write"]
    return cmd


# ---------------------------------------------------------------------------
# Pattern A: background task dispatch
# ---------------------------------------------------------------------------

def build_background_cmd(prompt, model, tools_str, max_turns,
                         session_id=None, harness=None, root=None):
    """Build argv for a background task dispatch (interactive mode).

    Returns (cmd_list, session_id, harness_name). For Codex, session_id is
    a placeholder -- the real thread_id comes from the response.
    """
    h = _active(harness, root)
    if h == "codex":
        import uuid
        sid = session_id or str(uuid.uuid4())
        cmd = _codex_background(prompt, model)
        return cmd, sid, h
    import uuid
    sid = session_id or str(uuid.uuid4())
    cmd = _claude_background(prompt, model, tools_str, max_turns, sid)
    return cmd, sid, h


def _claude_background(prompt, model, tools_str, max_turns, sid):
    return [
        "claude",
        prompt,
        "--model", model,
        "--allowedTools", tools_str,
        "--max-turns", max_turns,
        "--permission-mode", "bypassPermissions",
        "--session-id", sid,
    ]


def _codex_background(prompt, model):
    binary = platform_lib.resolve_codex() or "codex"
    return [
        binary, "exec", prompt,
        "-m", model,
        "--dangerously-bypass-approvals-and-sandbox",
        "-C", ".",
    ]


# ---------------------------------------------------------------------------
# Pattern F: hermetic text completion (no tools, no hooks)
# ---------------------------------------------------------------------------

def build_hermetic_cmd(prompt, model, system_prompt=None,
                       harness=None, root=None):
    """Build argv for a hermetic text completion (pure text, no tools).

    Returns (cmd_list, harness_name).
    """
    h = _active(harness, root)
    if h == "codex":
        return _codex_hermetic(prompt, model, system_prompt), h
    return _claude_hermetic(prompt, model, system_prompt), h


def _claude_hermetic(prompt, model, system_prompt):
    cmd = [
        platform_lib.resolve_claude(), "-p", prompt,
        "--model", model,
        "--max-turns", "2",
        "--tools", "",
        "--setting-sources", "",
    ]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    return cmd


def _codex_hermetic(prompt, model, system_prompt):
    effective_prompt = prompt
    if system_prompt:
        effective_prompt = f"[SYSTEM]\n{system_prompt}\n[/SYSTEM]\n\n{prompt}"
    binary = platform_lib.resolve_codex() or "codex"
    return [
        binary, "exec", effective_prompt,
        "-m", model,
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "-s", "read-only",
        "--json",
    ]


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def unwrap_oneshot_result(stdout_text, harness_name):
    """Extract the assistant's text from a one-shot response.

    Claude wraps in {"result": "..."}. Codex emits JSONL with the final
    agent_message in an item.completed event.
    """
    if harness_name == "codex":
        return _codex_unwrap_oneshot(stdout_text)
    return _claude_unwrap_oneshot(stdout_text)


def _claude_unwrap_oneshot(stdout_text):
    text = (stdout_text or "").strip()
    if not text:
        return None
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict) and "result" in envelope:
            return envelope["result"]
    except (json.JSONDecodeError, ValueError):
        pass
    return text


def _codex_unwrap_oneshot(stdout_text):
    text = (stdout_text or "").strip()
    if not text:
        return None
    last_message = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if (event.get("type") == "item.completed"
                and isinstance(event.get("item"), dict)
                and event["item"].get("type") == "agent_message"):
            last_message = event["item"].get("text", "")
    return last_message


# ---------------------------------------------------------------------------
# Stream normalization (for chat/Adapt/onboard streaming)
# ---------------------------------------------------------------------------

_TARGET_KEYS = ("file_path", "path", "pattern", "command", "query", "url", "description")


def normalize_stream_event(raw_event, harness_name):
    """Normalize one raw stream event to internal UI events.

    Returns a list of normalized event dicts (think, tool_step, text, result,
    ask, plan, session_start). Empty list for noise events.
    """
    if harness_name == "codex":
        return _codex_normalize(raw_event)
    return _claude_normalize(raw_event)


def _claude_normalize(raw_event):
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
                name = block.get("name", "")
                if name == "AskUserQuestion":
                    out.append({
                        "kind": "ask",
                        "role": "assistant",
                        "questions": tool_input.get("questions") or [],
                    })
                elif name == "ExitPlanMode":
                    out.append({
                        "kind": "plan",
                        "role": "assistant",
                        "body": tool_input.get("plan") or "",
                    })
        return out
    if etype == "result":
        return [{
            "kind": "result",
            "usage": raw_event.get("usage", {}),
            "cost": raw_event.get("total_cost_usd"),
            "session_id": raw_event.get("session_id"),
            "permission_denials": raw_event.get("permission_denials") or [],
        }]
    return []


def _codex_normalize(raw_event):
    if not isinstance(raw_event, dict):
        return []
    etype = raw_event.get("type")

    if etype == "thread.started":
        return [{
            "kind": "session_start",
            "session_id": raw_event.get("thread_id", ""),
        }]

    if etype == "item.completed":
        item = raw_event.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            return [{
                "kind": "text",
                "role": "assistant",
                "text": item.get("text", ""),
            }]
        if itype == "command_execution":
            return [{
                "kind": "tool_step",
                "role": "assistant",
                "verb": "command_execution",
                "target": item.get("command", ""),
            }]
        return []

    if etype == "item.started":
        item = raw_event.get("item") or {}
        if item.get("type") == "command_execution":
            return [{
                "kind": "tool_step",
                "role": "assistant",
                "verb": "command_execution",
                "target": item.get("command", ""),
            }]
        return []

    if etype == "turn.completed":
        usage = raw_event.get("usage") or {}
        return [{
            "kind": "result",
            "usage": usage,
            "cost": None,
            "session_id": None,
            "permission_denials": [],
        }]

    return []


# ---------------------------------------------------------------------------
# MCP fallback helper
# ---------------------------------------------------------------------------

def requires_claude_fallback(harness_name, requires_mcp=False):
    """True when this call should fall back to Claude despite the active harness.

    Codex has no cloud MCP support. Dispatch sites that need MCP tools
    (Granola, Jira, M365, Pendo) pass requires_mcp=True; when the active
    harness is codex, this returns True so the caller can fall back to Claude.
    """
    return harness_name == "codex" and requires_mcp
