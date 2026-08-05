"""Tests for the PM adapter READ op (fetch_status) + the free read helper.

A read of the team's system of record is NOT an external write, so it MUST NOT
go through the Tier-2 NeedsConfirmation gate. adapters.fetch_status resolves the
provider (the same outer liveness gate as get()), checks is_configured, and reads
- or graceful-degrades to None when no provider / unconfigured / adaptation off.
The point of these tests: the read path never raises NeedsConfirmation, and an
unconfigured backend yields a clean None (never fabricated data).
"""
import types

import pytest

import adapters
import jira_publish
from adapters.project_management import asana
from adapters.project_management import jira as jira_adapter
from adapters.project_management._contract import NotConfigured


# --- The free read helper on adapters/__init__.py ----------------------------

def test_fetch_status_returns_none_when_provider_none(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n")
    assert adapters.fetch_status(
        "project_management", "EPIC-1", root=str(tmp_path)) is None


def test_fetch_status_returns_none_when_unconfigured(tmp_path, monkeypatch):
    # Provider is jira but no creds -> is_configured False -> clean None.
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n")
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", lambda *a, **k: True)
    assert adapters.fetch_status(
        "project_management", "EPIC-1", root=str(tmp_path)) is None


def test_fetch_status_returns_fact_for_configured_provider(monkeypatch):
    fact = {"status": "Done", "title": "Reconcile epic", "due": "2026-09-15"}
    fake = types.SimpleNamespace(
        is_configured=lambda root=None: True,
        fetch_status=lambda issue_key, root=None: fact,
    )
    monkeypatch.setattr(adapters, "get", lambda family, root=None: fake)

    def boom(*a, **k):
        raise AssertionError("a read must never raise NeedsConfirmation (Tier-2)")
    monkeypatch.setattr(adapters, "_is_confirmed", boom)

    out = adapters.fetch_status("project_management", "EPIC-1")
    assert out == fact


def test_fetch_status_collapses_provider_not_configured_to_none(monkeypatch):
    from adapters.project_management._contract import NotConfigured

    def _raise(issue_key, root=None):
        raise NotConfigured("read op not wired")
    fake = types.SimpleNamespace(
        is_configured=lambda root=None: True, fetch_status=_raise)
    monkeypatch.setattr(adapters, "get", lambda family, root=None: fake)
    assert adapters.fetch_status("project_management", "EPIC-1") is None


def test_fetch_status_none_provider_does_not_check_confirmation(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n")

    def boom(*a, **k):
        raise AssertionError("reads never touch the Tier-2 gate")
    monkeypatch.setattr(adapters, "_is_confirmed", boom)
    assert adapters.fetch_status(
        "project_management", "EPIC-1", root=str(tmp_path)) is None


def test_fetch_status_collapses_runtime_error_to_none(monkeypatch):
    def _raise(issue_key, root=None):
        raise RuntimeError("Claude session timed out")
    fake = types.SimpleNamespace(
        is_configured=lambda root=None: True, fetch_status=_raise)
    monkeypatch.setattr(adapters, "get", lambda family, root=None: fake)
    assert adapters.fetch_status("project_management", "EPIC-1") is None


# --- Jira adapter: fetch_status delegates to jira_publish.fetch_issue --------

def test_jira_fetch_status_delegates_to_fetch_issue(profile_root, monkeypatch):
    fact = {"status": "In Progress", "title": "Alpha epic", "due": "2026-09-15"}
    monkeypatch.setattr(jira_publish, "fetch_issue", lambda key: fact)
    result = jira_adapter.fetch_status("EPIC-1", root=profile_root)
    assert result == fact


def test_jira_fetch_status_returns_none_from_fetch_issue(profile_root, monkeypatch):
    monkeypatch.setattr(jira_publish, "fetch_issue", lambda key: None)
    assert jira_adapter.fetch_status("EPIC-GONE", root=profile_root) is None


def test_jira_fetch_status_raises_not_configured_when_unconfigured(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n")
    with pytest.raises(NotConfigured):
        jira_adapter.fetch_status("EPIC-1", root=str(tmp_path))


# --- jira_publish.fetch_issue output parsing ---------------------------------

def test_fetch_issue_parses_full_result(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "JIRA_READ:In Progress|Build the feed|2026-09-15|2026-08-01|2026-09-15")
    result = jira_publish.fetch_issue("VNT-123")
    assert result == {
        "status": "In Progress", "title": "Build the feed",
        "due": "2026-09-15", "ea_date": "2026-08-01", "ga_date": "2026-09-15",
    }


def test_fetch_issue_parses_none_due(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "JIRA_READ:Done|Ship it|none|none|none")
    result = jira_publish.fetch_issue("VNT-123")
    assert result == {
        "status": "Done", "title": "Ship it",
        "due": None, "ea_date": None, "ga_date": None,
    }


def test_fetch_issue_parses_with_ea_ga_dates(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "JIRA_READ:In Development|Community Feed|none|2026-07-25|2026-08-30")
    result = jira_publish.fetch_issue("VNT-42411")
    assert result["ea_date"] == "2026-07-25"
    assert result["ga_date"] == "2026-08-30"
    assert result["due"] is None


def test_fetch_issue_handles_missing_date_fields(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "JIRA_READ:Open|Old issue|2026-12-01")
    result = jira_publish.fetch_issue("VNT-100")
    assert result["status"] == "Open"
    assert result["ea_date"] is None
    assert result["ga_date"] is None


def test_fetch_issue_returns_none_for_not_found(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "JIRA_READ:NOT_FOUND")
    assert jira_publish.fetch_issue("VNT-999") is None


def test_fetch_issue_raises_on_unparseable(monkeypatch):
    monkeypatch.setattr(jira_publish, "_run_jira_read_session",
                        lambda key: "some random output with no marker")
    with pytest.raises(RuntimeError, match="Could not parse"):
        jira_publish.fetch_issue("VNT-123")


# --- Asana adapter (still a stub) -------------------------------------------

def test_asana_fetch_status_raises_not_configured(tmp_path):
    with pytest.raises(NotConfigured):
        asana.fetch_status("EPIC-1", root=str(tmp_path))
