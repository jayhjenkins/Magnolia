"""Jira custom-field ids and board conventions come from the profile, never the engine.

Every instance-specific Jira value (custom field ids, lane label) lives in
profile/integrations.yaml -> project_management.jira and is read through
profile_lib (invariant #1). A field whose id is empty is simply omitted.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest
from ruamel.yaml import YAML

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "profile.example", "integrations.yaml")

FIELD_KEYS = ("epic_name", "ga_date", "ea_date", "spec_reference",
              "client_commitment", "release_notes", "severity")

FULL_MAP = {
    "epic_name": "customfield_90001",
    "ga_date": "customfield_90002",
    "ea_date": "customfield_90003",
    "spec_reference": "customfield_90004",
    "client_commitment": "customfield_90005",
    "release_notes": "customfield_90006",
}


def _write_jira_profile(tmp_path, extra=""):
    prof = tmp_path / "profile"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "integrations.yaml").write_text(textwrap.dedent("""\
        project_management:
          provider: "jira"
          jira:
            cloud_id: "acme.atlassian.net"
            project_key: "ACM"
            email: "me@acme.com"
            api_token: "s3cret"
        """) + extra)
    return str(tmp_path)


# ─── profile_lib ────────────────────────────────────────────────────────────

def test_jira_fields_defaults_to_empty_ids(profile_root):
    import profile_lib
    fields = profile_lib.jira_fields(root=profile_root)
    assert set(FIELD_KEYS) <= set(fields)
    assert all(fields[k] == "" for k in FIELD_KEYS)


def test_jira_fields_reads_profile_map(tmp_path):
    import profile_lib
    root = _write_jira_profile(tmp_path, (
        "    fields:\n"
        "      epic_name: \"customfield_1\"\n"
        "      ga_date: \"customfield_2\"\n"
        "      ea_date: \"\"\n"))
    fields = profile_lib.jira_fields(root=root)
    assert fields["epic_name"] == "customfield_1"
    assert fields["ga_date"] == "customfield_2"
    assert fields["ea_date"] == ""
    assert fields["release_notes"] == ""


def test_jira_fields_empty_when_provider_not_jira(tmp_path):
    import profile_lib
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n  jira:\n    fields:\n      ga_date: customfield_2\n")
    fields = profile_lib.jira_fields(root=str(tmp_path))
    assert all(fields[k] == "" for k in FIELD_KEYS)


def test_jira_public_config_redacts_secrets(tmp_path):
    import profile_lib
    root = _write_jira_profile(tmp_path)
    cfg = profile_lib.jira_public_config(root=root)
    assert cfg["project_key"] == "ACM"
    assert "api_token" not in cfg and "email" not in cfg
    assert "s3cret" not in json.dumps(cfg)
    assert set(FIELD_KEYS) <= set(cfg["fields"])


def test_cli_jira_config_prints_redacted_json():
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "profile_lib.py"), "--jira-config"],
        capture_output=True, text=True, check=True).stdout
    cfg = json.loads(out)
    assert "api_token" not in cfg
    assert "fields" in cfg


def test_template_declares_empty_field_map():
    with open(TEMPLATE, encoding="utf-8") as f:
        jira = YAML(typ="safe").load(f)["project_management"]["jira"]
    assert set(FIELD_KEYS) <= set(jira["fields"])
    assert all(v == "" for v in jira["fields"].values())
    assert "unlabeled_lane" in jira


# ─── jira_publish ───────────────────────────────────────────────────────────

@pytest.fixture
def jp(monkeypatch):
    import jira_publish
    monkeypatch.setattr(jira_publish, "JIRA_COMPONENT_ID", "999")
    monkeypatch.setattr(jira_publish, "JIRA_DEFAULT_ASSIGNEE", "acct-1")
    monkeypatch.setattr(jira_publish, "JIRA_FIELDS", dict(FULL_MAP))
    return jira_publish


def _feature_draft(issue_type):
    return {
        "type": issue_type, "summary": "S", "description": "D", "labels": [],
        "feature_name": "Big Thing", "gtm_date": "2026-12-01", "ea_date": "2026-11-01",
        "spec_reference": "https://spec", "client_commitment": "Yes",
        "release_notes": "Required",
    }


def test_epic_gets_epic_name_and_dates(jp):
    f = jp._build_additional_fields(_feature_draft("Epic"))
    assert f["customfield_90001"] == "Big Thing"
    assert f["customfield_90002"] == "2026-12-01"
    assert f["customfield_90003"] == "2026-11-01"
    assert f["customfield_90004"] == "https://spec"
    assert f["customfield_90005"] == ["Yes"]
    assert f["customfield_90006"] == {"value": "Required"}


def test_feature_omits_epic_name_but_keeps_dates(jp):
    """The Feature create screen rejects Epic Name; dates/commitment still apply."""
    f = jp._build_additional_fields(_feature_draft("Feature"))
    assert "customfield_90001" not in f
    assert f["customfield_90002"] == "2026-12-01"
    assert f["customfield_90005"] == ["Yes"]


def test_unmapped_fields_are_omitted_not_guessed(jp, monkeypatch):
    monkeypatch.setattr(jp, "JIRA_FIELDS", {k: "" for k in FIELD_KEYS})
    f = jp._build_additional_fields(_feature_draft("Epic"))
    assert not [k for k in f if k.startswith("customfield_")]
    assert "" not in f


def test_empty_component_is_omitted(jp, monkeypatch):
    monkeypatch.setattr(jp, "JIRA_COMPONENT_ID", "")
    f = jp._build_additional_fields({"type": "Bug", "summary": "S", "description": "D"})
    assert "components" not in f


def test_mcp_prompt_uses_profile_field_ids(jp):
    p = jp.build_claude_prompt(_feature_draft("Feature"))
    assert "customfield_90002" in p
    assert "customfield_90001" not in p


def test_engine_has_no_literal_customfield_ids():
    import re
    src = open(os.path.join(ROOT, "scripts", "jira_publish.py"), encoding="utf-8").read()
    assert not re.search(r"customfield_\d+", src)
    assert "AI DLC" not in src


def test_rest_edit_maps_dates_through_profile(jp):
    sent = {}

    class C:
        def edit_issue(self, key, fields):
            sent.update(fields)
            return key, "u"

    jp._update_rest(C(), {"issue_key": "ACM-1", "action": "edit",
                          "ea_date": "2026-01-01", "ga_date": "2026-02-01"})
    assert sent == {"customfield_90003": "2026-01-01", "customfield_90002": "2026-02-01"}


def test_rest_edit_skips_unmapped_dates(jp, monkeypatch):
    monkeypatch.setattr(jp, "JIRA_FIELDS", {k: "" for k in FIELD_KEYS})
    sent = {}

    class C:
        def edit_issue(self, key, fields):
            sent.update(fields)
            return key, "u"

    jp._update_rest(C(), {"issue_key": "ACM-1", "action": "edit",
                          "summary": "New", "ea_date": "2026-01-01"})
    assert sent == {"summary": "New"}


def test_rest_fetch_requests_and_maps_configured_dates(jp):
    asked = {}

    class C:
        def get_issue(self, key, fields=None):
            asked["fields"] = fields
            return {"summary": "T", "status": {"name": "Open"}, "duedate": None,
                    "customfield_90003": "2026-01-01", "customfield_90002": "2026-02-01"}

    r = jp._fetch_rest(C(), "ACM-1")
    assert "customfield_90003" in asked["fields"] and "customfield_90002" in asked["fields"]
    assert r["ea_date"] == "2026-01-01" and r["ga_date"] == "2026-02-01"


def test_rest_fetch_without_date_map(jp, monkeypatch):
    monkeypatch.setattr(jp, "JIRA_FIELDS", {k: "" for k in FIELD_KEYS})
    asked = {}

    class C:
        def get_issue(self, key, fields=None):
            asked["fields"] = fields
            return {"summary": "T", "status": {"name": "Open"}}

    r = jp._fetch_rest(C(), "ACM-1")
    assert asked["fields"] == ["summary", "status", "duedate"]
    assert r["ea_date"] is None and r["ga_date"] is None


def test_read_prompt_names_configured_ids_only(jp, monkeypatch):
    p = jp.build_read_prompt("ACM-1")
    assert "customfield_90003 is the EA date" in p
    monkeypatch.setattr(jp, "JIRA_FIELDS", {k: "" for k in FIELD_KEYS})
    p = jp.build_read_prompt("ACM-1")
    assert "customfield" not in p


def test_lane_hint_comes_from_profile(jp, monkeypatch):
    monkeypatch.setattr(jp, "JIRA_AUTO_LABEL", "team_lane")
    monkeypatch.setattr(jp, "JIRA_PRODUCT_AREA", "Team Lane")
    monkeypatch.setattr(jp, "JIRA_UNLABELED_LANE", "everything else")
    assert jp.lane_hint(["team_lane"]) == " (Team Lane lane)"
    assert jp.lane_hint([]) == " (everything else lane)"
    assert jp.lane_hint(["other"]) == ""
    monkeypatch.setattr(jp, "JIRA_AUTO_LABEL", "")
    monkeypatch.setattr(jp, "JIRA_UNLABELED_LANE", "")
    assert jp.lane_hint([]) == ""
    assert jp.lane_hint(["team_lane"]) == ""
