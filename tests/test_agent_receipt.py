"""agent:receipt — for a deterministic, no-decision agent run (e.g. a
cron-dispatched script), archive the source task immediately instead of
leaving it in the 'awaiting human review' bucket, and emit an informational
receipt card recording what happened.
"""
import argparse


def _receipt(task_lib):
    """Receipts from this flow are auto-archived, so they only show up via
    list_archived(), which (like list_tasks()) projects a fixed field
    whitelist — full custom fields require reading the file directly."""
    return [t for t in task_lib.list_archived() if t.get("card_type") == "receipt"]


def test_agent_receipt_archives_source_task(tasks_root):
    import task_lib, task_cli
    tid, _ = task_lib.create_task("Trust-ladder graduation assessment", queue="agent",
                                  domain="ops", creator="cron")
    args = argparse.Namespace(task_id=tid, summary="Ran: 0 graduation cards created, no demotions")
    task_cli.cmd_agent_receipt(args)

    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["status"] == "done"
    assert fm["agent_status"] == "complete"


def test_agent_receipt_not_in_review_bucket(tasks_root):
    import task_lib, task_cli
    tid, _ = task_lib.create_task("Trust-ladder graduation assessment", queue="agent",
                                  domain="ops", creator="cron")
    args = argparse.Namespace(task_id=tid, summary="Ran: 0 graduation cards created, no demotions")
    task_cli.cmd_agent_receipt(args)

    inbox = task_lib.get_inbox()
    assert all(t["id"] != tid for t in inbox["agent_completed"])


def test_agent_receipt_emits_informational_card(tasks_root):
    import task_lib, task_cli
    tid, _ = task_lib.create_task("Trust-ladder graduation assessment", queue="agent",
                                  domain="ops", creator="cron")
    summary = "Ran: 0 graduation cards created, no demotions"
    args = argparse.Namespace(task_id=tid, summary=summary)
    task_cli.cmd_agent_receipt(args)

    receipts = _receipt(task_lib)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "done"  # informational — auto-archived, nothing to Keep/Undo

    fm = task_lib.read_task(receipts[0]["id"])["frontmatter"]
    assert fm["receipt_kind"] == "cron-run"
    assert fm["receipt_summary"] == summary
    assert fm["source_task"] == tid
