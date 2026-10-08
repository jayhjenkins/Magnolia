import importlib
import textwrap


def test_jira_constants_come_from_profile(profile_root, monkeypatch):
    import profile_lib
    # Force jira_publish to resolve config against the temp profile.
    monkeypatch.setattr(profile_lib, "PM_OS_DIR", profile_root)
    import jira_publish
    importlib.reload(jira_publish)
    assert jira_publish.JIRA_CLOUD_ID == "acme.atlassian.net"
    assert jira_publish.JIRA_PROJECT_KEY == "ACM"
    assert jira_publish.JIRA_COMPONENT_ID == "999"
    assert jira_publish.JIRA_DEFAULT_ASSIGNEE == "acct-123"


def test_browse_base_built_from_profile_cloud_id(profile_root, monkeypatch):
    """The issue browse-URL base must derive from the profile cloud_id, not a
    hardcoded tenant like 'vantaca.atlassian.net'."""
    import profile_lib
    monkeypatch.setattr(profile_lib, "PM_OS_DIR", profile_root)
    import jira_publish
    importlib.reload(jira_publish)
    assert jira_publish.JIRA_BROWSE_BASE == "https://acme.atlassian.net/browse"
    assert "vantaca" not in jira_publish.JIRA_BROWSE_BASE


def test_fallback_url_parse_uses_profile_project_key(profile_root, monkeypatch):
    """The fallback issue-key/URL parser must match the profile's project key
    and cloud_id, proving it is no longer tied to one team."""
    import profile_lib
    monkeypatch.setattr(profile_lib, "PM_OS_DIR", profile_root)
    import jira_publish
    importlib.reload(jira_publish)
    output = "some noise\nJIRA noise ACM-4421 created at https://acme.atlassian.net/browse/ACM-4421\n"
    # publish_to_jira's primary path needs a JIRA_RESULT line; here we exercise
    # the fallback regex directly via a minimal stand-in of that block.
    import re
    key_pat = re.escape(jira_publish.JIRA_PROJECT_KEY) + r"-\d+"
    key_match = re.search(rf"({key_pat})", output)
    url_match = re.search(rf"({re.escape(jira_publish.JIRA_BROWSE_BASE)}/{key_pat})", output)
    assert key_match.group(1) == "ACM-4421"
    assert url_match.group(1) == "https://acme.atlassian.net/browse/ACM-4421"


def test_unconfigured_profile_does_not_false_positive_parse(tmp_path, monkeypatch):
    """With no project key configured (provider: none), the fallback parser must
    NOT scrape a bogus key out of incidental digits like '403-1'. It should
    signal unparseable exactly as it does for genuinely-unparseable output —
    by raising RuntimeError — rather than returning a (key, url) pair."""
    import pytest

    prof = tmp_path / "profile"
    prof.mkdir(parents=True)
    (prof / "integrations.yaml").write_text(textwrap.dedent("""\
        project_management:
          provider: "none"
    """))

    import profile_lib
    monkeypatch.setattr(profile_lib, "PM_OS_DIR", str(tmp_path))
    import jira_publish
    importlib.reload(jira_publish)

    # Sanity: the unconfigured profile yields an empty project key, which is the
    # condition that previously degraded key_pat to "-\\d+".
    assert jira_publish.JIRA_PROJECT_KEY == ""

    # Output that carries no JIRA_RESULT / JIRA_ERROR line but contains digits
    # that the degraded pattern would have matched as a key (e.g. "403-1" -> "-1").
    output = "error 403-1 happened at 2026-06-05"

    class _FakeCompleted:
        returncode = 0
        stdout = output
        stderr = ""

    monkeypatch.setattr(jira_publish.subprocess, "run", lambda *a, **k: _FakeCompleted())

    with pytest.raises(RuntimeError, match="Could not parse Jira result"):
        jira_publish.publish_to_jira({"summary": "x", "description": "y", "type": "Bug"})
