"""Idempotently seed Magnolia's in-box default cron jobs.

datasets/cron/jobs.json is gitignored (per-person), so defaults are created at
runtime by onboarding rather than committed. Re-running is safe.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cron_lib  # noqa: E402

def _ensure_cron_store():
    """Idempotently initialize the cron store so cold/fresh-clone runs don't crash.

    cron_lib._next_cron_id() opens COUNTER_FILE in "r+" and raises FileNotFoundError
    when it's missing. On a fresh Magnolia clone datasets/cron/ ships only .gitkeep
    (jobs.json + _counter are gitignored), so the counter must be created before any
    create_job call. list_jobs()/create_job() handle a missing jobs.json gracefully
    (_load_jobs returns [] and _save_jobs makes the dir), so only the dir + counter
    need seeding here. Uses cron_lib's own path constants so it stays correct if paths change.
    """
    os.makedirs(cron_lib.CRON_DIR, exist_ok=True)
    if not os.path.exists(cron_lib.COUNTER_FILE):
        with open(cron_lib.COUNTER_FILE, "w") as fd:
            fd.write("0")


DEFAULTS = [
    {
        "name": "Doctor self-heal",
        "cron_expr": "0 9 * * 1",  # Monday 09:00 (croniter: 1=Monday)
        "cron_human": "Every Monday at 9:00am",
        "task_template": {
            "title": "Weekly Doctor check {date}",
            "queue": "agent",
            "priority": "low",
            "domain": "onboarding",
            "description": (
                "Run `python3 scripts/doctor.py detect`. If any capability is missing, "
                "degraded, or needs re-auth, surface a recommendation to fix it "
                "(invoke the workflow-doctor skill). Observe-only otherwise."
            ),
        },
    },
    {
        "name": "Weekly self-improvement",
        "cron_expr": "0 9 * * 1",  # Monday 09:00 (with the Doctor cron)
        "cron_human": "Every Monday at 9:00am",
        "task_template": {
            "title": "Feedback-loop self-improvement pass {date}",
            "queue": "agent", "priority": "medium", "domain": "ops",
            "description": (
                "Weekly self-improvement. Run `python3 scripts/eval_digest.py --days 7`, "
                "cluster failures by deliverable kind, and for the top clusters draft a machine-applicable "
                ".patch + a recommendation card each (eval-analyst worker). Propose only; nothing "
                "auto-applies."
            ),
        },
    },
    {
        "name": "Graduation ladder",
        "cron_expr": "30 9 * * 1,4",  # Mon + Thu 09:30 (twice-weekly so the ladder shows up fast)
        "cron_human": "Every Monday and Thursday at 9:30am",
        "task_template": {
            "title": "Trust-ladder graduation assessment {date}",
            "queue": "agent", "priority": "low", "domain": "ops",
            "description": (
                "Run `python3 scripts/graduation_assess.py`. Deterministic: assess each task-type's "
                "approval + judge-human agreement over the rolling window, create graduation cards "
                "for types ready to climb, and auto-demote types whose scores dropped. No analysis "
                "needed beyond running the script and reporting what it created."
            ),
        },
    },
]


def seed():
    """Create any default job not already present (matched by name). Returns count added."""
    _ensure_cron_store()
    existing = {j["name"] for j in cron_lib.list_jobs()}
    added = 0
    for d in DEFAULTS:
        if d["name"] in existing:
            continue
        cron_lib.create_job(
            name=d["name"],
            cron_expr=d["cron_expr"],
            cron_human=d["cron_human"],
            task_template=d["task_template"],
            auto_dispatch=True,
        )
        added += 1
    return added


if __name__ == "__main__":
    print(f"Seeded {seed()} default cron job(s).")
