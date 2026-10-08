"""otter_classify: provider-neutral metadata, profile-driven identity + domains."""
import os

import pytest

import otter_classify as oc


@pytest.fixture
def prof(monkeypatch):
    """Stub the profile reads otter_classify makes."""
    cfg = {"config": {}, "provider": "granola", "name": "Pat Rivera"}
    monkeypatch.setattr(oc.profile_lib, "config", lambda root=None: cfg["config"])
    monkeypatch.setattr(oc.profile_lib, "display_name", lambda root=None: cfg["name"])
    monkeypatch.setattr(oc.profile_lib, "transcript_config",
                        lambda root=None: {"provider": cfg["provider"],
                                           "target": "datasets/meetings/", "external_feed": False})
    return cfg


def _granola_txt(tmp_path, attendees="Ann Lee, Bob Ng"):
    f = tmp_path / "2026-10-01_10-00_Weekly sync_abcd1234.txt"
    hdr = "# Weekly sync\nDate: 2026-10-01T10:00:00Z\n"
    if attendees:
        hdr += f"Attendees: {attendees}\n"
    f.write_text(hdr + "\nAnn: “hello”\nBob: hi\n", encoding="utf-8")
    return f


# ── Granola header parsing ──────────────────────────────────────────────────

def test_granola_attendees_header_becomes_participants(tmp_path, prof):
    md = oc.extract_metadata(_granola_txt(tmp_path), speech_id="g-uuid-1",
                             downloaded_state={"g-uuid-1": {"title": "Weekly sync"}})
    assert md["participants"] == ["Ann Lee", "Bob Ng"]
    assert md["date"] == "2026-10-01"
    assert md["time"] == "10-00"


def test_provider_neutral_id_in_front_matter(tmp_path, prof):
    md = oc.extract_metadata(_granola_txt(tmp_path), speech_id="g-uuid-1")
    fm = oc.build_front_matter(md, "general")
    assert 'transcript_id: "g-uuid-1"' in fm
    assert 'transcript_provider: "granola"' in fm
    assert "otter_id" not in fm                     # not an Otter meeting


def test_otter_meetings_keep_otter_id_for_back_compat(tmp_path, prof):
    prof["provider"] = "otter"
    f = tmp_path / "x.txt"
    f.write_text("# T\nDate: 2026-10-01 09:30\n\n[00:00:05] Ann: hi\n", encoding="utf-8")
    md = oc.extract_metadata(f, speech_id="otter-123")
    fm = oc.build_front_matter(md, "general")
    assert 'otter_id: "otter-123"' in fm and 'transcript_id: "otter-123"' in fm
    assert md["participants"] == ["Ann"]


def test_legacy_metadata_with_only_otter_id_still_renders(prof):
    fm = oc.build_front_matter({"title": "T", "otter_id": "old-1"}, "general")
    assert 'otter_id: "old-1"' in fm and 'transcript_id: "old-1"' in fm


# ── identity comes from the profile, never a hardcoded person ───────────────

def test_manual_file_operator_name_from_profile(tmp_path, prof):
    f = tmp_path / "pat-and-sam-sync.txt"
    f.write_text("notes\n", encoding="utf-8")
    md = oc.extract_metadata(f)
    assert md["participants"][0] == "Pat Rivera"
    assert "Sam" in md["participants"]
    assert "Pat" not in md["participants"]          # first name not double-counted


def test_no_hardcoded_person_in_source():
    src = open(os.path.join(os.path.dirname(oc.__file__), "otter_classify.py"),
               encoding="utf-8").read()
    assert "Jay Jenkins" not in src
    assert '"jay"' not in src


# ── domains: profile-driven, else generic + discovered product areas ────────

def test_generic_domains_when_no_profile_and_no_product_areas(tmp_path, prof):
    tax = oc.domain_taxonomy(meetings_dir=tmp_path)
    assert {"recruiting", "product", "leadership", "strategy", "customer", "general"} == set(tax)


def test_existing_product_area_folders_become_domains(tmp_path, prof):
    for sub in ("payments", "home", "2026-09"):
        (tmp_path / "product" / sub).mkdir(parents=True)
    tax = oc.domain_taxonomy(meetings_dir=tmp_path)
    assert "product/payments" in tax and "product/home" in tax
    assert "product/2026-09" not in tax             # month folders aren't areas
    assert "product" not in tax


def test_profile_meeting_domains_win(tmp_path, prof):
    prof["config"] = {"meeting_domains": {"sales": "deal reviews", "eng": "engineering syncs"}}
    tax = oc.domain_taxonomy(meetings_dir=tmp_path)
    assert set(tax) == {"sales", "eng", "general"}
    assert tax["sales"] == "deal reviews"


def test_classify_accepts_profile_domain_and_rejects_unknown(tmp_path, prof, monkeypatch):
    import subprocess
    prof["config"] = {"meeting_domains": {"sales": "deal reviews"}}
    monkeypatch.setattr(oc, "MEETINGS_DIR", tmp_path)
    monkeypatch.setattr(oc.profile_lib, "resolve_model", lambda *a, **k: "haiku")
    monkeypatch.setattr(oc.profile_lib, "harness", lambda root=None: "claude")
    reply = {"v": "sales"}
    monkeypatch.setattr(oc.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout='{"result": "%s"}' % reply["v"], stderr=""))
    assert oc.classify_domain("Deal review", "x") == "sales"
    reply["v"] = "product/payments"                 # not in this profile's taxonomy
    assert oc.classify_domain("Deal review", "x") == "general"


def test_keyword_fallback_uses_discovered_product_area(tmp_path, prof, monkeypatch):
    (tmp_path / "product" / "payments").mkdir(parents=True)
    monkeypatch.setattr(oc, "MEETINGS_DIR", tmp_path)
    assert oc._keyword_classify("Payments standup") == "product/payments"
    assert oc._keyword_classify("Candidate interview") == "recruiting"
    assert oc._keyword_classify("Team standup") == "leadership"
    assert oc._keyword_classify("random chat") == "general"


# ── file move ───────────────────────────────────────────────────────────────

def test_classify_and_move_replaces_existing_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "MEETINGS_DIR", tmp_path / "m")
    src = tmp_path / "a.txt"
    src.write_text("new", encoding="utf-8")
    dest_dir = tmp_path / "m" / "general" / "2026-10"
    dest_dir.mkdir(parents=True)
    (dest_dir / "a.txt").write_text("old", encoding="utf-8")
    calls = []
    real_replace = os.replace
    monkeypatch.setattr(oc.os, "replace", lambda s, d: calls.append((s, d)) or real_replace(s, d))
    out = oc.classify_and_move(src, "general", "2026-10-01")
    assert calls and out.read_text(encoding="utf-8") == "new"
