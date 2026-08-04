"""Tests for the PM adapter READ op (fetch_status) + the free read helper.

A read of the team's system of record is NOT an external write, so it MUST NOT
go through the Tier-2 NeedsConfirmation gate. adapters.fetch_status resolves the
provider (the same outer liveness gate as get()), checks is_configured, and reads
- or graceful-degrades to None when no provider / unconfigured / adaptation off.
The point of these tests: the read path never raises NeedsConfirmation, and an
unconfigured backend yields a clean None (never fabricated data).
"""
import json
import textwrap
import types
import urllib.error
import urllib.request

import pytest

import adapters
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


def test_fetch_status_collapses_http_error_to_none(monkeypatch):
    def _raise(issue_key, root=None):
        raise RuntimeError("Jira REST API error 500: Server Error")
    fake = types.SimpleNamespace(
        is_configured=lambda root=None: True, fetch_status=_raise)
    monkeypatch.setattr(adapters, "get", lambda family, root=None: fake)
    assert adapters.fetch_status("project_management", "EPIC-1") is None


# --- Jira adapter: fetch_status wired via REST API ---------------------------

def _fake_jira_response(fields):
    """Return a context-manager that yields a fake HTTP response."""
    body = json.dumps({"fields": fields}).encode()
    class FakeResp:
        def read(self):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
    return FakeResp()


def test_jira_fetch_status_returns_data_when_configured(profile_root, monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: _fake_jira_response({
            "summary": "Alpha epic",
            "status": {"name": "In Progress"},
            "duedate": "2026-09-15",
        }))
    result = jira_adapter.fetch_status("EPIC-1", root=profile_root)
    assert result == {
        "status": "In Progress",
        "title": "Alpha epic",
        "due": "2026-09-15",
    }


def test_jira_fetch_status_handles_null_due(profile_root, monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: _fake_jira_response({
            "summary": "No deadline",
            "status": {"name": "Open"},
            "duedate": None,
        }))
    result = jira_adapter.fetch_status("EPIC-2", root=profile_root)
    assert result["due"] is None
    assert result["status"] == "Open"


def test_jira_fetch_status_returns_none_on_404(profile_root, monkeypatch):
    def _raise_404(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_404)
    assert jira_adapter.fetch_status("EPIC-GONE", root=profile_root) is None


def test_jira_fetch_status_raises_runtime_on_500(profile_root, monkeypatch):
    def _raise_500(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_500)
    with pytest.raises(RuntimeError, match="Jira REST API error 500"):
        jira_adapter.fetch_status("EPIC-1", root=profile_root)


def test_jira_fetch_status_raises_not_configured_when_unconfigured(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n")
    with pytest.raises(NotConfigured):
        jira_adapter.fetch_status("EPIC-1", root=str(tmp_path))


def test_jira_fetch_status_raises_not_configured_without_api_creds(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(textwrap.dedent("""\
        project_management:
          provider: "jira"
          jira:
            cloud_id: "acme.atlassian.net"
            project_key: "ACM"
    """))
    with pytest.raises(NotConfigured, match="api_email"):
        jira_adapter.fetch_status("EPIC-1", root=str(tmp_path))


def test_jira_fetch_status_never_stub_when_configured(profile_root, monkeypatch):
    """Regression: fetch_status must attempt an HTTP call, not raise NotConfigured."""
    called = []
    def _track(req, timeout=None):
        called.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", _track)
    result = jira_adapter.fetch_status("EPIC-1", root=profile_root)
    assert result is None
    assert called


# --- Asana adapter (still a stub) -------------------------------------------

def test_asana_fetch_status_raises_not_configured(tmp_path):
    with pytest.raises(NotConfigured):
        asana.fetch_status("EPIC-1", root=str(tmp_path))
