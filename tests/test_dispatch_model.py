import task_dispatch


def test_resolve_task_model_uses_worker_tier(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    worker = {"name": "researcher", "tier": "deep"}
    assert task_dispatch._resolve_task_model({"id": "TASK-1"}, worker) == "opus"


def test_resolve_task_model_task_override_wins(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    worker = {"name": "scheduler", "tier": "light"}
    task = {"id": "TASK-2", "model": "opus"}
    assert task_dispatch._resolve_task_model(task, worker) == "opus"


def test_resolve_task_model_tier_override_wins(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    worker = {"name": "scheduler", "tier": "light"}
    task = {"id": "TASK-3", "tier": "deep"}
    assert task_dispatch._resolve_task_model(task, worker) == "opus"


def test_resolve_task_model_defaults_when_no_tier(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    assert task_dispatch._resolve_task_model({"id": "T"}, {"name": "x"}) == "sonnet"


def test_resolve_task_model_handles_none_worker(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    assert task_dispatch._resolve_task_model({"id": "T"}, None) == "sonnet"


def test_resolve_task_model_honors_posture_shift(monkeypatch):
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "high")
    # standard worker, high posture -> shifts up to deep -> opus
    assert task_dispatch._resolve_task_model({"id": "T"}, {"name": "w", "tier": "standard"}) == "opus"
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "low")
    # standard worker, low posture -> shifts down to light -> haiku
    assert task_dispatch._resolve_task_model({"id": "T"}, {"name": "w", "tier": "standard"}) == "haiku"


def test_list_tasks_projects_model_and_tier_override(tasks_root):
    import os, task_lib
    agent_dir = os.path.join(tasks_root, "datasets", "tasks", "agent")
    with open(os.path.join(agent_dir, "TASK-9999.md"), "w") as f:
        f.write("---\nid: TASK-9999\ntitle: Override test\nqueue: agent\n"
                "status: open\npriority: medium\nmodel: opus\ntier: deep\n---\n\nBody\n")
    rows = task_lib.list_tasks(queue="agent")
    row = next(r for r in rows if r["id"] == "TASK-9999")
    assert row["model"] == "opus"
    assert row["tier"] == "deep"
