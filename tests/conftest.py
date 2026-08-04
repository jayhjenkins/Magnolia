import os
import sys
import textwrap
import pytest

# Make scripts/ importable as top-level modules (matches how scripts import each other)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture
def profile_root(tmp_path):
    """A temp PM-OS root containing a fully populated profile/ dir."""
    prof = tmp_path / "profile"
    (prof / "voice").mkdir(parents=True)
    (prof / "profile.yaml").write_text(textwrap.dedent("""\
        display_name: "Test User"
        email: "test@example.com"
        company: "Acme"
        persona: "pm"
        timezone: "America/New_York"
    """))
    (prof / "integrations.yaml").write_text(textwrap.dedent("""\
        project_management:
          provider: "jira"
          jira:
            cloud_id: "acme.atlassian.net"
            project_key: "ACM"
            component_id: "999"
            auto_label: "team_lane"
            default_assignee: "acct-123"
            api_email: "test@example.com"
            api_token: "test-token-xxx"
        transcript:
          provider: "granola"
        calendar:
          provider: "m365"
    """))
    (prof / "config.yaml").write_text(textwrap.dedent("""\
        models:
          judge: "opus"
          parser: "haiku"
          cost_posture: "balanced"
        active_skill_packs: ["core", "pm"]
        server:
          port: 8755
    """))
    (prof / "voice" / "teams.md").write_text("# Teams voice\nTight, lowercase ok.\n")
    (prof / "voice" / "email.md").write_text("# Email voice\nWarm, polished.\n")
    return str(tmp_path)


@pytest.fixture
def tasks_root(tmp_path, monkeypatch):
    """Redirect task_lib's on-disk dirs to a temp tree and seed the counter.

    Returns the temp PM-OS root. Use task_lib.create_task / update_task against it.
    """
    import task_lib
    tasks_dir = tmp_path / "datasets" / "tasks"
    archive_dir = tasks_dir / "_archive"
    for q in ("human", "agent", "collab", "waiting"):
        (tasks_dir / q).mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "_counter").write_text("0")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(task_lib, "ARCHIVE_DIR", str(archive_dir))
    # COUNTER_FILE is derived from TASKS_DIR at import time, so _next_id() would
    # otherwise read the real counter. Redirect it to the temp counter too.
    monkeypatch.setattr(task_lib, "COUNTER_FILE", str(tasks_dir / "_counter"))
    return str(tmp_path)
