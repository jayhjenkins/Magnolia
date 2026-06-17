"""Tests for task_lib.create_task passthroughs."""


def test_create_task_lands_proposal_frontmatter(tasks_root):
    import task_lib
    mutation = {"op": "advance-phase", "to": "planning",
                "checkpoint": "discovery-exit", "from": "discovery"}
    tid, _ = task_lib.create_task(
        "advance?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", proposal=mutation)
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["proposal"] == mutation
    assert fm["card_type"] == "recommendation"
    assert fm["task_type"] == "cadence-propose-update"


def test_create_task_defaults_proposal_absent(tasks_root):
    import task_lib
    tid, _ = task_lib.create_task("plain", queue="human")
    fm = task_lib.read_task(tid)["frontmatter"]
    assert "proposal" not in fm or fm.get("proposal") is None
