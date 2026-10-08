"""Granola transcript adapter.

Delegates to transcript_sync._run_granola so the headless structured-error
contract and its test monkeypatch hold (mirrors otter.py). granola_sync.main
returns a result dict (it records failures in its status file rather than
raising), so a reported error/skip is passed through, not masked as "ok"."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def sync(root=None) -> dict:
    import transcript_sync
    try:
        result = transcript_sync._run_granola(root)
    except Exception as e:
        return {"status": "error", "provider": "granola", "error": str(e)}
    if isinstance(result, dict) and result.get("status") in ("error", "skipped"):
        return {**result, "provider": "granola"}
    return {"status": "ok", "provider": "granola"}
