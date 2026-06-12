import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import adaptations_lib

def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptations_lib, "STORE_DIR", str(tmp_path / "adaptations"))
    os.makedirs(adaptations_lib.STORE_DIR, exist_ok=True)
    return adaptations_lib

def test_create_then_read_roundtrips(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="Stock sentinel", session_id="sess-1")
    rec = a.read(aid)
    assert rec["name"] == "Stock sentinel"
    assert rec["claude_session_id"] == "sess-1"
    assert rec["state"] == "building"
    assert rec["status"] == "active"
    assert rec["manifest"] == []

def test_list_all_skips_tombstoned(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    keep = a.create(name="Keep", session_id="s1")
    gone = a.create(name="Gone", session_id="s2")
    a.tombstone(gone)
    ids = [r["id"] for r in a.list_all()]
    assert keep in ids and gone not in ids

def test_unowned_artifact_is_live(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    assert a.is_live("worker", "scripts/workers/scheduler.md") is True  # legacy

def test_owned_artifact_follows_state(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="X", session_id="s")
    a.add_artifact(aid, "worker", "scripts/workers/x.md", "sha1")
    assert a.is_live("worker", "scripts/workers/x.md") is False   # building
    a.set_state(aid, "on")
    assert a.is_live("worker", "scripts/workers/x.md") is True
    a.set_state(aid, "off")
    assert a.is_live("worker", "scripts/workers/x.md") is False

def test_tombstoned_owner_releases_artifact(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="X", session_id="s")
    a.add_artifact(aid, "card-type", "stock-alert", "sha")
    a.set_state(aid, "on")
    a.tombstone(aid)
    assert a.is_live("card-type", "stock-alert") is True  # unowned again

def test_is_live_any_live_owner_wins(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    a1 = a.create(name="Owner one", session_id="s1")
    a2 = a.create(name="Owner two", session_id="s2")
    a.add_artifact(a1, "card-type", "shared", "sha1")
    a.add_artifact(a2, "card-type", "shared", "sha2")
    a.set_state(a1, "on")        # a2 left in 'building'
    assert a.is_live("card-type", "shared") is True   # a live owner wins
    a.set_state(a1, "off")       # now NO active owner is on
    assert a.is_live("card-type", "shared") is False

def test_add_artifact_upserts_on_surface_ref(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="Upsert", session_id="s")
    a.add_artifact(aid, "worker", "scripts/workers/x.md", "sha1")
    rec = a.add_artifact(aid, "worker", "scripts/workers/x.md", "sha2")
    matches = [e for e in rec["manifest"]
               if e["surface"] == "worker" and e["ref"] == "scripts/workers/x.md"]
    assert len(matches) == 1
    assert matches[0]["commit"] == "sha2"
    before = len(rec["manifest"])
    rec2 = a.add_artifact(aid, "card-type", "scripts/workers/x.md", "sha3")
    assert len(rec2["manifest"]) == before + 1  # different surface -> appends
