import json
import chat_runner as cr


def _load_fixture():
    with open("tests/fixtures/stream_json_sample.jsonl") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_normalize_maps_event_kinds_from_real_fixture():
    raw = _load_fixture()
    events = [e for r in raw for e in cr.normalize(r)]
    kinds = {e["kind"] for e in events}
    assert "text" in kinds          # assistant prose
    assert "tool_step" in kinds     # a tool_use mapped
    assert "think" in kinds         # a thinking block mapped
    # result carries usage + cost so we can measure cold-open cost
    result = [e for e in events if e["kind"] == "result"]
    assert result and "usage" in result[0] and "cost" in result[0]


def test_tool_step_has_verb_and_target():
    raw = _load_fixture()
    steps = [e for r in raw for e in cr.normalize(r) if e.get("kind") == "tool_step"]
    assert steps, "expected at least one tool_step from the fixture"
    s = steps[0]
    assert s["verb"]                 # e.g. "Read"
    assert "target" in s             # may be a path/pattern/etc.


def test_system_and_tool_result_events_yield_nothing():
    assert cr.normalize({"type": "system", "subtype": "init", "session_id": "x"}) == []
    assert cr.normalize({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "out"}]}}) == []


def test_missing_content_does_not_raise():
    assert cr.normalize({"type": "assistant"}) == []
    assert cr.normalize({"type": "assistant", "message": {}}) == []


def test_result_carries_permission_denials():
    # A real headless result event lists every tool the session was NOT allowed
    # to run in `permission_denials` (verified against the CLI). normalize must
    # surface it so run_turn can tell the user what got blocked.
    raw = {
        "type": "result", "subtype": "success", "session_id": "s", "usage": {},
        "permission_denials": [
            {"tool_name": "Bash", "tool_use_id": "t1",
             "tool_input": {"command": "./scripts/task.sh inbox"}},
        ],
    }
    out = cr.normalize(raw)
    assert len(out) == 1 and out[0]["kind"] == "result"
    assert out[0]["permission_denials"][0]["tool_name"] == "Bash"


def test_result_without_denials_defaults_to_empty_list():
    out = cr.normalize({"type": "result", "session_id": "s", "usage": {}})
    assert out[0]["permission_denials"] == []


def test_ask_user_question_also_emits_ask_event():
    # The Adapt build chat renders AskUserQuestion as a "Magnolia asks" card.
    # normalize keeps the normal tool_step AND adds a richer `ask` event carrying
    # the questions array. chat.js ignores unknown kinds, so the chat panel is
    # unaffected.
    raw = {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "AskUserQuestion", "input": {
                "questions": [
                    {"question": "Which store?",
                     "options": [{"label": "Shopify", "description": "Your main store"},
                                 {"label": "Etsy", "description": "The craft shop"}]},
                ],
            }},
        ]},
    }
    out = cr.normalize(raw)
    kinds = [e["kind"] for e in out]
    assert kinds == ["tool_step", "ask"]
    ask = out[1]
    assert ask["questions"][0]["question"] == "Which store?"
    assert len(ask["questions"][0]["options"]) == 2


def test_ask_user_question_missing_questions_defaults_empty():
    raw = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}]},
    }
    out = cr.normalize(raw)
    assert out[-1]["kind"] == "ask" and out[-1]["questions"] == []


def test_exit_plan_mode_also_emits_plan_event():
    # ExitPlanMode renders as "The build" card — a prose plan + Approve/Adjust.
    raw = {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "ExitPlanMode",
             "input": {"plan": "1. Add a Shopify adapter\n2. Add a worker\n3. Done"}},
        ]},
    }
    out = cr.normalize(raw)
    kinds = [e["kind"] for e in out]
    assert kinds == ["tool_step", "plan"]
    assert out[1]["body"].startswith("1. Add a Shopify adapter")


def test_exit_plan_mode_missing_plan_defaults_empty():
    raw = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {}}]},
    }
    out = cr.normalize(raw)
    assert out[-1]["kind"] == "plan" and out[-1]["body"] == ""
