# Onboarding Front Door — Increment 2: the `magnolia` launcher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A `scripts/magnolia.py` launcher so a teammate runs one command to boot the board and land in the browser: `magnolia` (start server if needed → ensure reboot-persistence → open browser), `magnolia update` (git pull the engine), `magnolia doctor` (run capability detection). The `magnolia`-on-PATH shim is Inc 4's job; until then the entry point is `python3 scripts/magnolia.py`.

**Architecture:** Thin orchestration over existing libs — `server_lib` (start/url/is_running/default_cmd), `persist_lib` (is_installed/install), `platform_lib` (open_url — the OS seam), `profile_lib`. The launcher is profile-AGNOSTIC: it always just starts the server and opens the browser; the server's first-run gate (Inc 3) decides whether to serve onboarding or the board. No OS branches in this file (they live in `platform_lib`) → passes the portability gate. All side-effecting calls go through module functions + a `_run` subprocess seam so tests mock them.

**Tech Stack:** Python 3 stdlib (`argparse`, `subprocess`), pytest with `monkeypatch`.

**Increment roadmap:** Inc 1 = trust_seed ✅ (PR #43). **Inc 2 = this.** Inc 3 = first-run gate + onboarding UI room + `onboard_runner`. Inc 4 = `install.sh`/`install.ps1` curl bootstrap (wires `magnolia` onto PATH). All stacked on `feat/onboarding-front-door`.

---

## Task 1: `launch()` — start server if needed, ensure persistence, open browser

**Files:**
- Create: `scripts/magnolia.py`
- Test: `tests/test_magnolia.py`

**Step 1: Write the failing tests** (mock every seam; no real server/browser)

```python
import magnolia


def _patch(monkeypatch, *, running):
    calls = {"start": 0, "install": 0, "opened": None}
    monkeypatch.setattr(magnolia.server_lib, "is_running", lambda *a, **k: running)
    monkeypatch.setattr(magnolia.server_lib, "start", lambda *a, **k: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(magnolia.server_lib, "url", lambda *a, **k: "http://localhost:8742")
    monkeypatch.setattr(magnolia.server_lib, "default_cmd", lambda: ["python", "task_server.py"])
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: True)
    monkeypatch.setattr(magnolia.persist_lib, "install", lambda **k: calls.__setitem__("install", calls["install"] + 1))
    monkeypatch.setattr(magnolia.platform_lib, "open_url", lambda u: calls.__setitem__("opened", u))
    return calls


def test_launch_starts_server_when_not_running(monkeypatch):
    calls = _patch(monkeypatch, running=False)
    res = magnolia.launch()
    assert calls["start"] == 1
    assert calls["opened"] == "http://localhost:8742"
    assert res["url"] == "http://localhost:8742"
    assert res["started"] is True


def test_launch_skips_start_when_already_running(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    res = magnolia.launch()
    assert calls["start"] == 0           # did not double-start
    assert calls["opened"] == "http://localhost:8742"   # still opens browser
    assert res["started"] is False


def test_launch_installs_persistence_when_absent(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: False)
    magnolia.launch()
    assert calls["install"] == 1


def test_launch_can_skip_browser(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    magnolia.launch(open_browser=False)
    assert calls["opened"] is None
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/jayjenkins/dev/pm-os-onboarding && python3 -m pytest tests/test_magnolia.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magnolia'`.

**Step 3: Write minimal implementation**

```python
"""magnolia.py - the one-command launcher.

`magnolia`         start the board (if not running), ensure it relaunches on
                   reboot, and open it in the browser.
`magnolia update`  pull the latest engine (git pull, fast-forward only).
`magnolia doctor`  run capability detection and print the summary.

Thin orchestration over server_lib / persist_lib / platform_lib (the OS seam) /
profile_lib. Profile-agnostic: it always starts the server and opens the browser;
the server's first-run gate decides whether to serve onboarding or the board. No
OS branches live here - they belong in platform_lib.
"""
import os
import subprocess
import sys

import platform_lib
import persist_lib
import profile_lib
import server_lib

PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(PM_OS_DIR, "logs", "task-server.log")


def launch(open_browser=True):
    """Start the board if needed, ensure reboot-persistence, open the browser.

    Idempotent: a board already running is reused, not double-started."""
    started = False
    if not server_lib.is_running():
        server_lib.start(cmd=server_lib.default_cmd())
        started = True
    if not persist_lib.is_installed():
        persist_lib.install(program=server_lib.default_cmd(),
                            working_dir=PM_OS_DIR, log_path=LOG_PATH)
    url = server_lib.url()
    if open_browser:
        platform_lib.open_url(url)
    return {"started": started, "url": url}
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_magnolia.py -v`
Expected: PASS (4).

**Step 5: Commit**

```bash
git add scripts/magnolia.py tests/test_magnolia.py
git commit -m "feat(onboarding): magnolia.launch boots board + opens browser (Inc 2)"
```

---

## Task 2: `update()` — git pull the engine (fast-forward only)

**Files:**
- Modify: `scripts/magnolia.py`
- Test: `tests/test_magnolia.py`

**Step 1: Write the failing tests** (mock the `_run` subprocess seam)

```python
def test_update_runs_ff_only_pull_in_repo(monkeypatch):
    seen = {}
    monkeypatch.setattr(magnolia, "_run",
                        lambda cmd: seen.update(cmd=cmd) or (0, "Already up to date.\n"))
    res = magnolia.update()
    assert seen["cmd"][:3] == ["git", "-C", magnolia.PM_OS_DIR]
    assert "pull" in seen["cmd"] and "--ff-only" in seen["cmd"]
    assert res["status"] == "ok"
    assert "up to date" in res["output"].lower()


def test_update_reports_failure(monkeypatch):
    monkeypatch.setattr(magnolia, "_run", lambda cmd: (1, "fatal: not possible to fast-forward\n"))
    res = magnolia.update()
    assert res["status"] == "failed"
    assert "fast-forward" in res["output"]
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_magnolia.py -k update -v`
Expected: FAIL — `AttributeError: module 'magnolia' has no attribute 'update'`.

**Step 3: Write minimal implementation** (append to `scripts/magnolia.py`)

```python
def _run(cmd):
    """Mockable seam: run a command, return (returncode, combined_output)."""
    p = subprocess.run(cmd, cwd=PM_OS_DIR, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def update():
    """Pull the latest engine, fast-forward only (never auto-merge over local edits)."""
    rc, out = _run(["git", "-C", PM_OS_DIR, "pull", "--ff-only"])
    return {"status": "ok" if rc == 0 else "failed", "output": out}
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_magnolia.py -k update -v`
Expected: PASS (2).

**Step 5: Commit**

```bash
git add scripts/magnolia.py tests/test_magnolia.py
git commit -m "feat(onboarding): magnolia update (ff-only git pull) (Inc 2)"
```

---

## Task 3: `doctor()` — run capability detection

**Files:**
- Modify: `scripts/magnolia.py`
- Test: `tests/test_magnolia.py`

**Step 1: Write the failing test**

```python
def test_doctor_runs_detection(monkeypatch):
    seen = {}
    monkeypatch.setattr(magnolia, "_run",
                        lambda cmd: seen.update(cmd=cmd) or (0, '{"capabilities": {}}'))
    res = magnolia.doctor()
    assert seen["cmd"][0] == sys.executable
    assert seen["cmd"][1].endswith("doctor.py")
    assert seen["cmd"][2] == "detect"
    assert res["status"] == "ok"
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_magnolia.py -k doctor -v`
Expected: FAIL — no `doctor` attribute.

**Step 3: Write minimal implementation** (append)

```python
def doctor():
    """Run capability detection (scripts/doctor.py detect) and return its output."""
    rc, out = _run([sys.executable, os.path.join(PM_OS_DIR, "scripts", "doctor.py"), "detect"])
    return {"status": "ok" if rc == 0 else "failed", "output": out}
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_magnolia.py -k doctor -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/magnolia.py tests/test_magnolia.py
git commit -m "feat(onboarding): magnolia doctor runs capability detection (Inc 2)"
```

---

## Task 4: `_main` CLI dispatch

**Files:**
- Modify: `scripts/magnolia.py`
- Test: `tests/test_magnolia.py`

**Step 1: Write the failing tests** (dispatch routing only; mock the three handlers)

```python
def test_main_no_subcommand_launches(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "launch", lambda **k: hit.setdefault("launch", True) or {"url": "x", "started": True})
    assert magnolia._main([]) == 0
    assert hit.get("launch") is True


def test_main_update_routes_to_update(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "update", lambda: hit.setdefault("update", True) or {"status": "ok", "output": ""})
    assert magnolia._main(["update"]) == 0
    assert hit.get("update") is True


def test_main_doctor_routes_to_doctor(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "doctor", lambda: hit.setdefault("doctor", True) or {"status": "ok", "output": ""})
    assert magnolia._main(["doctor"]) == 0
    assert hit.get("doctor") is True


def test_main_update_failure_returns_nonzero(monkeypatch):
    monkeypatch.setattr(magnolia, "update", lambda: {"status": "failed", "output": "boom"})
    assert magnolia._main(["update"]) == 1
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_magnolia.py -k main -v`
Expected: FAIL — no `_main`.

**Step 3: Write minimal implementation** (append)

```python
def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="magnolia",
                                description="Boot the Magnolia board and open it.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("update", help="pull the latest engine (fast-forward only)")
    sub.add_parser("doctor", help="run capability detection")
    args = p.parse_args(argv)

    if args.cmd == "update":
        res = update()
        print(res["output"].rstrip())
        return 0 if res["status"] == "ok" else 1
    if args.cmd == "doctor":
        res = doctor()
        print(res["output"].rstrip())
        return 0 if res["status"] == "ok" else 1
    res = launch()
    print(f"Magnolia is live at {res['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_magnolia.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add scripts/magnolia.py tests/test_magnolia.py
git commit -m "feat(onboarding): magnolia CLI dispatch (launch/update/doctor) (Inc 2)"
```

---

## Task 5: Run all five gates green

**Step 1: Run the gates**

```bash
cd /Users/jayjenkins/dev/pm-os-onboarding
python3 -m pytest -q
python3 scripts/card_schema.py        # -> registry.json OK
python3 -m pytest tests/test_engine_no_jay.py -q
python3 scripts/portability_gate.py   # -> portability OK
python3 scripts/program_schema.py     # -> programtypes OK
```
Expected: all green. `magnolia.py` has no person literals and no OS branches → de-personalization + portability pass.

**Step 2: Do NOT open a new PR.** This increment stacks onto the existing `feat/onboarding-front-door` branch / PR #43. Just push:

```bash
git push
```

---

## Notes for the executor

- Worktree `/Users/jayjenkins/dev/pm-os-onboarding`, branch `feat/onboarding-front-door`. NEVER `git checkout` another branch.
- `magnolia.py` is **Tier-1** — it starts a local server, opens a local browser, and runs `git pull` / local detection. No external-system writes; no Tier-2 confirm.
- Mock every seam in tests — never start a real server, open a real browser, or run a real `git pull`.
- If the full suite dirties `profile.example/capabilities.json`, that bug is already fixed on this branch; if it reappears, restore with `git checkout -- profile.example/capabilities.json` and flag it. Do not stage it.
- ASCII runtime output (hyphen, not em-dash) per invariant #8.
