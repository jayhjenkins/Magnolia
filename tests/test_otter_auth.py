"""otter_auth.py must degrade gracefully when the optional Playwright extra is
absent - the Doctor can route users here, and a raw ModuleNotFoundError traceback
is a dead end. Mirrors otter_sync.py's guarded-import pattern."""
import pytest

import otter_auth


def test_otter_auth_imports_without_playwright():
    # The module must import on a machine without the transcript extras (the
    # common case - they're not in the core install). The guarded import leaves
    # sync_playwright defined (possibly None), never raising at import time.
    assert hasattr(otter_auth, "sync_playwright")


def test_otter_auth_exits_with_install_hint_when_playwright_missing(monkeypatch, capsys):
    monkeypatch.setattr(otter_auth, "sync_playwright", None)
    with pytest.raises(SystemExit) as exc:
        otter_auth.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "requirements-transcript.txt" in err
    assert "playwright install" in err
