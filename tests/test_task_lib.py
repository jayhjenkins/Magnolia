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


def test_create_send_message_stamps_attachments(tasks_root):
    import task_lib
    tid, _ = task_lib.create_task(
        "send digest", queue="collab", task_type="send-message",
        message_channel="Email", message_to="x@y.com", message_body="body",
        attachments=["datasets/programs/artifacts/PROG-1/w-priorities-v1.md"])
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["attachments"] == ["datasets/programs/artifacts/PROG-1/w-priorities-v1.md"]


def test_create_send_message_attachments_default_empty(tasks_root):
    import task_lib
    tid, _ = task_lib.create_task(
        "send", queue="collab", task_type="send-message",
        message_channel="Email", message_to="x@y.com", message_body="b")
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["attachments"] == []
