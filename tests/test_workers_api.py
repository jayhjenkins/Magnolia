import task_server


def test_workers_payload_has_tier_model_packs():
    workers = task_server.workers_payload(posture="balanced")
    assert workers, "expected workers from scripts/workers/"
    by_name = {w["name"]: w for w in workers}
    # Every worker carries the enrichment keys
    for w in workers:
        assert "tier" in w and "model" in w and "packs" in w
        assert isinstance(w["packs"], list)
    # researcher is tier=deep -> balanced resolves to opus
    r = by_name["researcher"]
    assert r["tier"] == "deep"
    assert r["model"] == "opus"
    # product-analyst's skills live in the pm pack -> pm membership
    assert "pm" in by_name["product-analyst"]["packs"]
    # default (catch-all) has no skills -> no pack membership
    assert by_name["default"]["packs"] == []


def test_workers_payload_model_tracks_posture():
    low = {w["name"]: w for w in task_server.workers_payload(posture="low")}
    high = {w["name"]: w for w in task_server.workers_payload(posture="high")}
    # deep worker: low -> sonnet, high -> opus (clamped)
    assert low["researcher"]["model"] == "sonnet"
    assert high["researcher"]["model"] == "opus"
    # light worker (scheduler): low -> haiku, high -> sonnet
    assert low["scheduler"]["model"] == "haiku"
    assert high["scheduler"]["model"] == "sonnet"


def test_worker_packs_empty_when_no_manifest(tmp_path):
    # No .claude/packs.yaml under tmp root -> load_packs returns {} -> no membership
    assert task_server._worker_packs(["workflow-prd-creation"], root=str(tmp_path)) == []
