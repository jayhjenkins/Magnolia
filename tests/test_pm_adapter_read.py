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
    # Stub a fully-configured provider module; the helper returns the fact and
    # the Tier-2 NeedsConfirmation path is NEVER hit (a read is free).
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
    # A configured provider whose read path is unwired raises NotConfigured (a
    # RuntimeError). The free read helper must degrade to None, never propagate it.
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


# --- The providers -----------------------------------------------------------

def test_jira_fetch_status_raises_not_configured_when_unconfigured(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n")
    with pytest.raises(NotConfigured):
        jira_adapter.fetch_status("EPIC-1", root=str(tmp_path))


def test_jira_fetch_status_is_honest_stub_when_configured(profile_root):
    # Configured but the read path is not wired -> NotConfigured with a clear
    # message rather than fabricated data. (Honest seam, graceful degrade.)
    with pytest.raises(NotConfigured):
        jira_adapter.fetch_status("EPIC-1", root=profile_root)


def test_asana_fetch_status_raises_not_configured(tmp_path):
    with pytest.raises(NotConfigured):
        asana.fetch_status("EPIC-1", root=str(tmp_path))
