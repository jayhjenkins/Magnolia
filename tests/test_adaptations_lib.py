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
    # A freshly-created keying row has built nothing yet: it is `pending`,
    # not `building`. It becomes visible only once a build lands a manifest.
    assert rec["state"] == "pending"
    assert rec["status"] == "active"
    assert rec["manifest"] == []

def test_list_all_skips_tombstoned(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    # Promote off pending so the rows are visible in list_all.
    keep = a.create(name="Keep", session_id="s1")
    a.set_state(keep, "off")
    gone = a.create(name="Gone", session_id="s2")
    a.set_state(gone, "off")
    a.tombstone(gone)
    ids = [r["id"] for r in a.list_all()]
    assert keep in ids and gone not in ids

def test_list_all_excludes_pending_but_read_returns_them(tmp_path, monkeypatch):
    """Pending keying rows are HIDDEN from list_all (so they never appear in the
    rail or feed is_live), but read(id) STILL returns them - adapt_runner reads
    the pending row by id to promote it once a build lands."""
    a = _fresh(tmp_path, monkeypatch)
    pend = a.create(name="Just a clarifying chat that built nothing", session_id="s")
    assert a.read(pend)["state"] == "pending"          # read still returns it
    assert pend not in [r["id"] for r in a.list_all()]  # but list_all hides it
    # Once promoted off pending it appears in list_all.
    a.set_state(pend, "off")
    assert pend in [r["id"] for r in a.list_all()]

def test_set_state_accepts_pending(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="X", session_id="s")
    a.set_state(aid, "off")
    a.set_state(aid, "pending")  # pending is a valid lifecycle value
    assert a.read(aid)["state"] == "pending"

def test_unowned_artifact_is_live(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    assert a.is_live("worker", "scripts/workers/scheduler.md") is True  # legacy

def test_owned_artifact_follows_state(tmp_path, monkeypatch):
    a = _fresh(tmp_path, monkeypatch)
    aid = a.create(name="X", session_id="s")
    a.add_artifact(aid, "worker", "scripts/workers/x.md", "sha1")
    a.set_state(aid, "off")  # promote off pending so the row is a visible owner
    assert a.is_live("worker", "scripts/workers/x.md") is False   # off
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
    a.set_state(a1, "on")        # a2 left pending (hidden, not an owner yet)
    a.set_state(a2, "off")       # a2 now a visible owner, but off
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
