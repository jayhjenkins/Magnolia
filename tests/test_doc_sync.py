"""Hermetic tests for doc_sync.py's profile/legacy config overlay (Task 20 follow-up).

These tests never touch a real profile or a real sync_config.yaml. They:
  - monkeypatch ``doc_sync._profile_doc_sync`` so no real profile is read, and
  - monkeypatch ``doc_sync.CONFIG_PATH`` so no real sync_config.yaml is read.

Covered cases:
  1. disabled  -> legacy values unchanged (overlay does not fire)
  2. enabled   -> profile's onedrive_root/sharepoint_site win; legacy-only keys
                  (tenant url, doc root, sync_paths) still come from the YAML
  3. enabled + absent config + empty onedrive_root -> clean SystemExit (guard)
  4. enabled + absent config + populated onedrive_root -> minimal config built
"""
import os
import textwrap
from pathlib import Path

import pytest

import doc_sync


LEGACY_YAML = textwrap.dedent("""\
    onedrive_root: "/tmp/legacy-od"
    sharepoint_site: "Legacy-Site"
    sharepoint_tenant_url: "https://legacy.sharepoint.com"
    sharepoint_doc_root: "/personal/legacy/Documents"
    sync_enabled: true
    sync_paths:
      - datasets/product/**/*.md
    sync_exclude:
      - datasets/product/drafts/*.md
""")


def _write_config(tmp_path, text=LEGACY_YAML):
    cfg = tmp_path / "sync_config.yaml"
    cfg.write_text(text)
    return cfg


def test_disabled_legacy_unchanged(tmp_path, monkeypatch):
    """Profile doc_sync disabled -> overlay does not fire; legacy values stand."""
    monkeypatch.setattr(doc_sync, "_profile_doc_sync", lambda: {})
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", cfg)

    config = doc_sync.load_config()

    assert config["onedrive_root"] == os.path.expanduser("/tmp/legacy-od")
    assert config["sharepoint_site"] == "Legacy-Site"
    assert config["sharepoint_tenant_url"] == "https://legacy.sharepoint.com"
    assert config["sharepoint_doc_root"] == "/personal/legacy/Documents"
    assert config["sync_paths"] == ["datasets/product/**/*.md"]


def test_enabled_overlay_wins(tmp_path, monkeypatch):
    """Profile enabled -> profile onedrive_root/sharepoint_site override the YAML,
    while legacy-only keys still come from the YAML."""
    monkeypatch.setattr(
        doc_sync,
        "_profile_doc_sync",
        lambda: {
            "onedrive_root": "/tmp/od-acme",
            "sharepoint_site": "PM-OS",
            "enabled": True,
        },
    )
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", cfg)

    config = doc_sync.load_config()

    # Overlay won for the fields the profile carries.
    assert config["onedrive_root"] == os.path.expanduser("/tmp/od-acme")
    assert config["sharepoint_site"] == "PM-OS"
    # Legacy-only keys still come from the YAML.
    assert config["sharepoint_tenant_url"] == "https://legacy.sharepoint.com"
    assert config["sharepoint_doc_root"] == "/personal/legacy/Documents"
    assert config["sync_paths"] == ["datasets/product/**/*.md"]
    assert config["sync_exclude"] == ["datasets/product/drafts/*.md"]


def test_enabled_absent_config_empty_root_exits(tmp_path, monkeypatch):
    """Profile enabled, no sync_config.yaml, onedrive_root still empty (fresh
    install before Doctor) -> fail loud with SystemExit, not a relative path."""
    monkeypatch.setattr(
        doc_sync,
        "_profile_doc_sync",
        lambda: {"onedrive_root": "", "sharepoint_site": "PM-OS", "enabled": True},
    )
    missing = tmp_path / "does-not-exist" / "sync_config.yaml"
    assert not missing.exists()
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", missing)

    with pytest.raises(SystemExit):
        doc_sync.load_config()


def test_enabled_absent_config_populated_root_minimal(tmp_path, monkeypatch):
    """Profile enabled, no sync_config.yaml, onedrive_root populated -> build a
    minimal config from the profile (no crash)."""
    monkeypatch.setattr(
        doc_sync,
        "_profile_doc_sync",
        lambda: {
            "onedrive_root": "/tmp/od-acme",
            "sharepoint_site": "PM-OS",
            "enabled": True,
        },
    )
    missing = tmp_path / "does-not-exist" / "sync_config.yaml"
    assert not missing.exists()
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", missing)

    config = doc_sync.load_config()

    assert config["onedrive_root"] == os.path.expanduser("/tmp/od-acme")
    assert config["sharepoint_site"] == "PM-OS"
    assert config["sync_enabled"] is True
    # onedrive_dir composes onedrive_root / sharepoint_site without crashing.
    assert doc_sync.onedrive_dir(config) == Path("/tmp/od-acme") / "PM-OS"


# ── word_status + `urls` CLI (Word publish: menu-only) ───────────────────────
# word_status is a pure lookup: never raises, never writes. "Exists" means the
# mapped .docx is on disk (not the manifest, which carries stale paths).

def _word_env(tmp_path, monkeypatch):
    """Point doc_sync at a temp repo + temp OneDrive root; return (repo, od_site)."""
    repo = tmp_path / "repo"
    od = tmp_path / "od"
    (repo / "datasets" / "product").mkdir(parents=True)
    od.mkdir()
    cfg = {
        "onedrive_root": str(od),
        "sharepoint_site": "PM-OS",
        "sharepoint_tenant_url": "https://tenant.sharepoint.com",
        "sharepoint_doc_root": "/personal/u/Documents",
        "sync_enabled": True,
        "sync_paths": [],
        "sync_exclude": [],
    }
    monkeypatch.setattr(doc_sync, "PM_OS_DIR", repo)
    monkeypatch.setattr(doc_sync, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(doc_sync, "_try_load_config", lambda: dict(cfg))
    monkeypatch.setattr(doc_sync, "MANIFEST_PATH", tmp_path / "manifest.json")
    return repo, od / "PM-OS"


def test_word_status_unconfigured_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_sync, "_try_load_config", lambda: None)
    md = tmp_path / "x.md"
    md.write_text("# x")
    assert doc_sync.word_status(str(md)) == {"exists": False, "url": None, "docx_path": None}


@pytest.mark.parametrize("profile", [
    {},                                                    # no profile, no sync_config.yaml
    {"enabled": True, "onedrive_root": "", "sharepoint_site": "PM-OS"},  # enabled, root unset
])
def test_unconfigured_lookups_are_silent(tmp_path, monkeypatch, capsys, profile):
    """Read-only lookups on an unconfigured install print nothing and don't exit
    (load_config's CLI messaging must not leak into the server's stdout)."""
    monkeypatch.setattr(doc_sync, "_profile_doc_sync", lambda: dict(profile))
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", tmp_path / "missing" / "sync_config.yaml")
    md = tmp_path / "x.md"
    md.write_text("# x")
    assert doc_sync._try_load_config() is None
    assert doc_sync.is_configured() is False
    assert doc_sync.word_status(str(md)) == {"exists": False, "url": None, "docx_path": None}
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_load_config_cli_still_prints_and_exits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doc_sync, "_profile_doc_sync", lambda: {})
    monkeypatch.setattr(doc_sync, "CONFIG_PATH", tmp_path / "missing" / "sync_config.yaml")
    with pytest.raises(SystemExit):
        doc_sync.load_config()
    assert "Config not found" in capsys.readouterr().out


def test_word_status_uses_preloaded_config(tmp_path, monkeypatch):
    """A caller-supplied config is used as-is; the config is not re-read."""
    repo, site = _word_env(tmp_path, monkeypatch)
    cfg = doc_sync._try_load_config()
    def _no_reload():
        raise AssertionError("config re-read despite preloaded config")
    monkeypatch.setattr(doc_sync, "_try_load_config", _no_reload)
    md = repo / "datasets" / "product" / "brief.md"
    md.write_text("# b")
    (site / "product").mkdir(parents=True)
    (site / "product" / "brief.docx").write_bytes(b"PK")
    st = doc_sync.word_status(str(md), config=cfg)
    assert st["exists"] is True


def test_script_dir_is_resolved():
    assert doc_sync.SCRIPT_DIR.is_absolute()
    assert doc_sync.SCRIPT_DIR == doc_sync.SCRIPT_DIR.resolve()
    assert doc_sync.PM_OS_DIR == doc_sync.SCRIPT_DIR.parent


def test_word_status_missing_docx(tmp_path, monkeypatch):
    repo, _site = _word_env(tmp_path, monkeypatch)
    md = repo / "datasets" / "product" / "brief.md"
    md.write_text("# b")
    st = doc_sync.word_status(str(md))
    assert st == {"exists": False, "url": None, "docx_path": None}
    assert not (tmp_path / "manifest.json").exists()  # never writes


def test_word_status_existing_docx(tmp_path, monkeypatch):
    repo, site = _word_env(tmp_path, monkeypatch)
    md = repo / "datasets" / "product" / "brief.md"
    md.write_text("# b")
    docx = site / "product" / "brief.docx"
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"PK")
    st = doc_sync.word_status(str(md))
    assert st["exists"] is True
    assert st["docx_path"] == str(docx)
    assert st["url"] == (
        "https://tenant.sharepoint.com/:w:/r/personal/u/Documents/PM-OS/product/brief.docx?web=1")
    assert not (tmp_path / "manifest.json").exists()


def test_word_status_path_outside_repo_never_raises(tmp_path, monkeypatch):
    _word_env(tmp_path, monkeypatch)
    st = doc_sync.word_status("/definitely/not/in/repo.md")
    assert st == {"exists": False, "url": None, "docx_path": None}


def test_urls_cli_lists_only_existing_docx(tmp_path, monkeypatch, capsys):
    import json
    repo, site = _word_env(tmp_path, monkeypatch)
    folder = repo / "datasets" / "product"
    (folder / "a.md").write_text("# a")
    (folder / "b.md").write_text("# b")
    (site / "product").mkdir(parents=True)
    (site / "product" / "a.docx").write_bytes(b"PK")
    called = []
    monkeypatch.setattr(doc_sync, "sync_one", lambda p: called.append(p))
    monkeypatch.setattr(doc_sync.sys, "argv", ["doc_sync.py", "urls", str(folder), "--json"])
    with pytest.raises(SystemExit) as ei:
        doc_sync.main()
    assert ei.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["folder"] == str(folder)
    assert [f["file"] for f in out["files"]] == ["a.md"]
    assert out["files"][0]["url"].endswith("/PM-OS/product/a.docx?web=1")
    assert called == []  # read-only: never pushes
    assert not (site / "product" / "b.docx").exists()
