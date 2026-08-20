"""Tests for harness_lib — builder functions and output parsers."""
import json
import pytest

import harness_lib
import platform_lib
import profile_lib


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _default_claude(monkeypatch):
    """Default every test to claude harness + known binary path."""
    monkeypatch.setattr(profile_lib, "harness", lambda root=None: "claude")
    monkeypatch.setattr(platform_lib, "resolve_claude", lambda path=None: "/usr/bin/claude")
    monkeypatch.setattr(platform_lib, "resolve_codex", lambda path=None: "/usr/bin/codex")


def _set_codex(monkeypatch):
    monkeypatch.setattr(profile_lib, "harness", lambda root=None: "codex")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. build_oneshot_cmd
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildOneshotCmd:

    def test_claude_default(self):
        cmd, h = harness_lib.build_oneshot_cmd("hello", "sonnet")
        assert h == "claude"
        assert cmd == ["/usr/bin/claude", "-p", "hello",
                       "--model", "sonnet", "--output-format", "json"]

    def test_codex_default(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, h = harness_lib.build_oneshot_cmd("hello", "gpt-5.6-terra")
        assert h == "codex"
        assert cmd[0] == "/usr/bin/codex"
        assert cmd[1] == "exec"
        assert "hello" in cmd[2]  # prompt is the third arg (may have system prefix)
        assert "-m" in cmd
        assert "gpt-5.6-terra" in cmd
        assert "--json" in cmd

    def test_claude_allowed_tools(self):
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", allowed_tools="Read,Bash")
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Bash"

    def test_claude_max_turns(self):
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", max_turns=5)
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "5"

    def test_claude_system_prompt(self):
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", system_prompt="Be brief")
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "Be brief"

    def test_claude_append_system_prompt(self):
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", append_system_prompt="Extra")
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Extra"

    def test_claude_permission_mode(self):
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", permission_mode="bypassPermissions")
        assert "--permission-mode" in cmd
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"

    def test_codex_system_prompt_embedded(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("my prompt", "m",
                                               system_prompt="Be brief")
        # prompt is cmd[2]; system prompt should be wrapped and prepended
        assert "[SYSTEM]" in cmd[2]
        assert "Be brief" in cmd[2]
        assert "[/SYSTEM]" in cmd[2]
        assert "my prompt" in cmd[2]

    def test_codex_append_system_prompt_embedded(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("my prompt", "m",
                                               append_system_prompt="Extra")
        assert "[SYSTEM]" in cmd[2]
        assert "Extra" in cmd[2]
        assert "my prompt" in cmd[2]

    def test_codex_bypass_permissions(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m",
                                               permission_mode="bypassPermissions")
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        # no -s flag when bypass is on
        assert "-s" not in cmd

    def test_codex_sandbox_readonly_default(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m")
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "read-only"

    def test_codex_sandbox_workspace_write_with_bash(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", allowed_tools="Bash,Read")
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "workspace-write"

    def test_codex_sandbox_workspace_write_with_write_tool(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", allowed_tools="Write")
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "workspace-write"

    def test_codex_sandbox_workspace_write_with_edit_tool(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m", allowed_tools="Edit")
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "workspace-write"

    def test_codex_falls_back_to_bare_name(self, monkeypatch):
        _set_codex(monkeypatch)
        monkeypatch.setattr(platform_lib, "resolve_codex", lambda path=None: None)
        cmd, _ = harness_lib.build_oneshot_cmd("p", "m")
        assert cmd[0] == "codex"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. build_streaming_cmd
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildStreamingCmd:

    def test_claude_new_session(self):
        cmd, h = harness_lib.build_streaming_cmd(
            "sid-1", "hello", "sonnet", new_session=True)
        assert h == "claude"
        assert "--session-id" in cmd
        assert "sid-1" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd

    def test_claude_resume(self):
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid-1", "hello", "sonnet", new_session=False)
        assert "--resume" in cmd
        assert "sid-1" in cmd
        assert "--session-id" not in cmd

    def test_claude_append_system_prompt(self):
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid", "msg", "m", append_system_prompt="Extra")
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "Extra"

    def test_claude_settings(self):
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid", "msg", "m", settings="/path/settings.json")
        assert "--settings" in cmd
        idx = cmd.index("--settings")
        assert cmd[idx + 1] == "/path/settings.json"

    def test_claude_allowed_tools(self):
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid", "msg", "m", allowed_tools="Read,Bash")
        assert "--allowedTools" in cmd

    def test_codex_new_session(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, h = harness_lib.build_streaming_cmd(
            "sid-1", "hello", "gpt-5.6-terra", new_session=True)
        assert h == "codex"
        assert cmd[0] == "/usr/bin/codex"
        assert cmd[1] == "exec"
        # For new session, message is the third arg (no "resume" subcommand)
        assert "hello" in cmd[2]
        assert "--json" in cmd

    def test_codex_resume(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid-1", "hello", "m", new_session=False)
        assert cmd[1] == "exec"
        assert cmd[2] == "resume"
        assert cmd[3] == "sid-1"
        assert "hello" in cmd[4]

    def test_codex_append_system_prompt_embedded(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_streaming_cmd(
            "sid", "msg", "m", new_session=True, append_system_prompt="Extra")
        # embedded in message, not as flag
        assert "--append-system-prompt" not in cmd
        assert "[SYSTEM]" in cmd[2]
        assert "Extra" in cmd[2]
        assert "msg" in cmd[2]

    def test_codex_workspace_write_sandbox(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_streaming_cmd("s", "m", "model", new_session=True)
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "workspace-write"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. build_background_cmd
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildBackgroundCmd:

    def test_claude_cmd_structure(self):
        cmd, sid, h = harness_lib.build_background_cmd(
            "do work", "sonnet", "Read,Bash", "10")
        assert h == "claude"
        assert sid  # non-empty UUID
        assert cmd[0] == "claude"
        assert cmd[1] == "do work"
        assert "--model" in cmd
        assert "--allowedTools" in cmd
        assert "--max-turns" in cmd
        assert "--permission-mode" in cmd
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"
        assert "--session-id" in cmd
        idx2 = cmd.index("--session-id")
        assert cmd[idx2 + 1] == sid

    def test_claude_uses_provided_session_id(self):
        cmd, sid, _ = harness_lib.build_background_cmd(
            "p", "m", "t", "5", session_id="my-sid")
        assert sid == "my-sid"
        assert "my-sid" in cmd

    def test_codex_cmd_structure(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, sid, h = harness_lib.build_background_cmd(
            "do work", "gpt-5.6-terra", "Read,Bash", "10")
        assert h == "codex"
        assert sid  # non-empty UUID
        assert cmd[0] == "/usr/bin/codex"
        assert cmd[1] == "exec"
        assert cmd[2] == "do work"
        assert "-m" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "-C" in cmd
        idx = cmd.index("-C")
        assert cmd[idx + 1] == "."

    def test_codex_uses_provided_session_id(self, monkeypatch):
        _set_codex(monkeypatch)
        _, sid, _ = harness_lib.build_background_cmd(
            "p", "m", "t", "5", session_id="my-sid")
        assert sid == "my-sid"

    def test_returns_three_tuple(self):
        result = harness_lib.build_background_cmd("p", "m", "t", "5")
        assert len(result) == 3
        cmd, sid, h = result
        assert isinstance(cmd, list)
        assert isinstance(sid, str)
        assert isinstance(h, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. build_hermetic_cmd
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildHermeticCmd:

    def test_claude_no_tools_no_settings(self):
        cmd, h = harness_lib.build_hermetic_cmd("prompt", "sonnet")
        assert h == "claude"
        assert cmd[0] == "/usr/bin/claude"
        assert "--tools" in cmd
        idx_t = cmd.index("--tools")
        assert cmd[idx_t + 1] == ""
        assert "--setting-sources" in cmd
        idx_s = cmd.index("--setting-sources")
        assert cmd[idx_s + 1] == ""
        assert "--max-turns" in cmd
        idx_m = cmd.index("--max-turns")
        assert cmd[idx_m + 1] == "2"

    def test_claude_with_system_prompt(self):
        cmd, _ = harness_lib.build_hermetic_cmd("p", "m", system_prompt="Be terse")
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "Be terse"

    def test_claude_without_system_prompt_no_flag(self):
        cmd, _ = harness_lib.build_hermetic_cmd("p", "m")
        assert "--system-prompt" not in cmd

    def test_codex_hermetic_flags(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, h = harness_lib.build_hermetic_cmd("prompt", "gpt-5.6-terra")
        assert h == "codex"
        assert "--ephemeral" in cmd
        assert "--ignore-rules" in cmd
        assert "--ignore-user-config" in cmd
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "read-only"
        assert "--json" in cmd

    def test_codex_with_system_prompt_embedded(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_hermetic_cmd("my prompt", "m",
                                                system_prompt="Be terse")
        # system prompt embedded in effective_prompt (cmd[2])
        assert "[SYSTEM]" in cmd[2]
        assert "Be terse" in cmd[2]
        assert "my prompt" in cmd[2]
        # no --system-prompt flag
        assert "--system-prompt" not in cmd

    def test_codex_without_system_prompt_no_wrapping(self, monkeypatch):
        _set_codex(monkeypatch)
        cmd, _ = harness_lib.build_hermetic_cmd("plain prompt", "m")
        assert cmd[2] == "plain prompt"
        assert "[SYSTEM]" not in cmd[2]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. unwrap_oneshot_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnwrapOneshotResult:

    def test_claude_extracts_result_field(self):
        stdout = json.dumps({"result": "hello world", "is_error": False})
        assert harness_lib.unwrap_oneshot_result(stdout, "claude") == "hello world"

    def test_claude_returns_raw_text_when_not_json(self):
        assert harness_lib.unwrap_oneshot_result("raw text", "claude") == "raw text"

    def test_claude_returns_none_for_empty(self):
        assert harness_lib.unwrap_oneshot_result("", "claude") is None
        assert harness_lib.unwrap_oneshot_result(None, "claude") is None
        assert harness_lib.unwrap_oneshot_result("   ", "claude") is None

    def test_codex_extracts_from_item_completed(self):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "the answer"}
            }),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        stdout = "\n".join(events)
        assert harness_lib.unwrap_oneshot_result(stdout, "codex") == "the answer"

    def test_codex_returns_last_agent_message(self):
        events = [
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "first"}
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "second"}
            }),
        ]
        stdout = "\n".join(events)
        assert harness_lib.unwrap_oneshot_result(stdout, "codex") == "second"

    def test_codex_ignores_non_agent_message_items(self):
        events = [
            json.dumps({
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "ls"}
            }),
        ]
        stdout = "\n".join(events)
        assert harness_lib.unwrap_oneshot_result(stdout, "codex") is None

    def test_codex_returns_none_for_empty(self):
        assert harness_lib.unwrap_oneshot_result("", "codex") is None
        assert harness_lib.unwrap_oneshot_result(None, "codex") is None

    def test_codex_skips_malformed_lines(self):
        lines = [
            "not json at all",
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "ok"}
            }),
        ]
        stdout = "\n".join(lines)
        assert harness_lib.unwrap_oneshot_result(stdout, "codex") == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. normalize_stream_event
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeStreamEvent:

    # -- Claude events -------------------------------------------------------

    def test_claude_text_block(self):
        raw = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 1
        assert out[0]["kind"] == "text"
        assert out[0]["text"] == "hello"
        assert out[0]["role"] == "assistant"

    def test_claude_thinking_block(self):
        raw = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "hmm"}]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 1
        assert out[0]["kind"] == "think"
        assert out[0]["text"] == "hmm"

    def test_claude_tool_use_block(self):
        raw = {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/foo/bar.py"},
            }]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 1
        assert out[0]["kind"] == "tool_step"
        assert out[0]["verb"] == "Read"
        assert out[0]["target"] == "/foo/bar.py"

    def test_claude_tool_use_target_fallback(self):
        # When file_path is missing, falls through to other target keys
        raw = {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls -la"},
            }]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert out[0]["target"] == "ls -la"

    def test_claude_ask_user_question(self):
        raw = {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "AskUserQuestion",
                "input": {"questions": ["What color?"]},
            }]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 2
        assert out[0]["kind"] == "tool_step"
        assert out[1]["kind"] == "ask"
        assert out[1]["questions"] == ["What color?"]

    def test_claude_exit_plan_mode(self):
        raw = {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "ExitPlanMode",
                "input": {"plan": "Step 1, Step 2"},
            }]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 2
        assert out[0]["kind"] == "tool_step"
        assert out[1]["kind"] == "plan"
        assert out[1]["body"] == "Step 1, Step 2"

    def test_claude_result_event(self):
        raw = {
            "type": "result",
            "session_id": "s1",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.05,
            "permission_denials": ["Read"],
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 1
        assert out[0]["kind"] == "result"
        assert out[0]["session_id"] == "s1"
        assert out[0]["usage"]["input_tokens"] == 100
        assert out[0]["cost"] == 0.05
        assert out[0]["permission_denials"] == ["Read"]

    def test_claude_result_event_defaults(self):
        raw = {"type": "result"}
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert out[0]["usage"] == {}
        assert out[0]["cost"] is None
        assert out[0]["session_id"] is None
        assert out[0]["permission_denials"] == []

    def test_claude_noise_system(self):
        assert harness_lib.normalize_stream_event(
            {"type": "system"}, "claude") == []

    def test_claude_noise_user(self):
        assert harness_lib.normalize_stream_event(
            {"type": "user"}, "claude") == []

    def test_claude_noise_non_dict(self):
        assert harness_lib.normalize_stream_event("garbage", "claude") == []
        assert harness_lib.normalize_stream_event(None, "claude") == []

    def test_claude_multiple_content_blocks(self):
        raw = {
            "type": "assistant",
            "message": {"content": [
                {"type": "thinking", "thinking": "think"},
                {"type": "text", "text": "respond"},
            ]},
        }
        out = harness_lib.normalize_stream_event(raw, "claude")
        assert len(out) == 2
        assert out[0]["kind"] == "think"
        assert out[1]["kind"] == "text"

    # -- Codex events --------------------------------------------------------

    def test_codex_thread_started(self):
        raw = {"type": "thread.started", "thread_id": "t-123"}
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert len(out) == 1
        assert out[0]["kind"] == "session_start"
        assert out[0]["session_id"] == "t-123"

    def test_codex_item_completed_agent_message(self):
        raw = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "done"},
        }
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert len(out) == 1
        assert out[0]["kind"] == "text"
        assert out[0]["text"] == "done"
        assert out[0]["role"] == "assistant"

    def test_codex_item_completed_command_execution(self):
        raw = {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "ls -la"},
        }
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert len(out) == 1
        assert out[0]["kind"] == "tool_step"
        assert out[0]["verb"] == "command_execution"
        assert out[0]["target"] == "ls -la"

    def test_codex_item_started_command_execution(self):
        raw = {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "cat foo.py"},
        }
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert len(out) == 1
        assert out[0]["kind"] == "tool_step"
        assert out[0]["target"] == "cat foo.py"

    def test_codex_item_started_non_command(self):
        raw = {
            "type": "item.started",
            "item": {"type": "agent_message"},
        }
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert out == []

    def test_codex_turn_completed(self):
        raw = {
            "type": "turn.completed",
            "usage": {"input_tokens": 200, "output_tokens": 100},
        }
        out = harness_lib.normalize_stream_event(raw, "codex")
        assert len(out) == 1
        assert out[0]["kind"] == "result"
        assert out[0]["usage"]["input_tokens"] == 200
        assert out[0]["cost"] is None
        assert out[0]["session_id"] is None
        assert out[0]["permission_denials"] == []

    def test_codex_noise_turn_started(self):
        assert harness_lib.normalize_stream_event(
            {"type": "turn.started"}, "codex") == []

    def test_codex_noise_non_dict(self):
        assert harness_lib.normalize_stream_event("garbage", "codex") == []

    def test_codex_item_completed_unknown_type(self):
        raw = {
            "type": "item.completed",
            "item": {"type": "unknown_thing"},
        }
        assert harness_lib.normalize_stream_event(raw, "codex") == []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. requires_claude_fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequiresClaudeFallback:

    def test_claude_with_mcp_no_fallback(self):
        assert harness_lib.requires_claude_fallback("claude", requires_mcp=True) is False

    def test_codex_without_mcp_no_fallback(self):
        assert harness_lib.requires_claude_fallback("codex", requires_mcp=False) is False

    def test_codex_with_mcp_needs_fallback(self):
        assert harness_lib.requires_claude_fallback("codex", requires_mcp=True) is True

    def test_claude_without_mcp_no_fallback(self):
        assert harness_lib.requires_claude_fallback("claude", requires_mcp=False) is False

    def test_default_requires_mcp_is_false(self):
        assert harness_lib.requires_claude_fallback("codex") is False
