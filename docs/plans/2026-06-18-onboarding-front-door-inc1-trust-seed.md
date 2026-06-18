# Onboarding Front Door — Increment 1: trust_seed + detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A pure, unit-tested `scripts/trust_seed.py` that (a) detects Claude Code login + previously-authorized connectors from `~/.claude.json`, and (b) seeds Layer-2 folder trust (trust dialog + qmd MCP enablement + CLAUDE.md external-includes approval) for the Magnolia repo path — plus a one-shot spike confirming whether a headless `claude -p` run even needs folder trust.

**Architecture:** `trust_seed.py` is a standalone library with a thin CLI (mirrors `doctor.py`). It reads/patches `~/.claude.json` (resolved via a mockable `claude_config_path()` seam using `os.path.expanduser`). Detection is read-only; seeding mutates only the target `projects[<abs path>]` entry and preserves everything else. No OS branches (JSON is OS-agnostic) → passes the portability gate. The installer (Inc 4) calls this after `claude login`.

**Tech Stack:** Python 3 stdlib (`json`, `os`, `tempfile`), pytest with `monkeypatch`/`tmp_path`.

**Increment roadmap (this epic):** Inc 1 = trust_seed + detection (this doc). Inc 2 = `magnolia` launcher. Inc 3 = first-run gate + onboarding UI room + `onboard_runner`. Inc 4 = `install.sh`/`install.ps1` curl bootstrap. Each is its own PR off `main`; later increments are planned after the prior merges (the Inc 1 spike result feeds Inc 3/4).

---

## Task 0: Spike — does a headless `claude -p` run need folder trust? (investigation, no code)

**Why:** The design hinges on whether Layer-2 seeding is *required* for the onboarding agent or merely *nice-to-have* for later interactive use. This repo runs headless `claude -p` (granola_sync, jira_publish) despite `hasTrustDialogAccepted: false`, suggesting the trust dialog gates only the interactive TUI.

**Step 1:** In a throwaway temp dir that is NOT in `~/.claude.json` `projects`, run a trivial headless probe:

```bash
TMPD=$(mktemp -d); cd "$TMPD"
claude -p "Reply with exactly: OK" --max-turns 1 --output-format json 2>&1 | tail -5
cd - >/dev/null; rm -rf "$TMPD"
```

Expected to learn: does it run, prompt for trust, or error? Record the observed behavior.

**Step 2:** Append a 3-4 line findings note to `docs/plans/2026-06-18-onboarding-front-door-design.md` under the "Open spike" paragraph: confirmed (headless ignores trust) / refuted (headless needs trust) / inconclusive. This decides whether Layer-2 seeding is required-for-onboarding or insurance-only. Either way Inc 1 still ships `trust_seed.py` (qmd enablement must be seeded regardless — `settings.local.json` is gitignored).

**Step 3: Commit** the findings note.

```bash
git add docs/plans/2026-06-18-onboarding-front-door-design.md
git commit -m "docs(onboarding): record headless-trust spike finding (Inc 1)"
```

---

## Task 1: `read_state` — detection (login + connectors)

**Files:**
- Create: `scripts/trust_seed.py`
- Test: `tests/test_trust_seed.py`

**Step 1: Write the failing tests**

```python
import json
import trust_seed


def test_read_state_missing_file(tmp_path):
    missing = tmp_path / "nope.json"
    st = trust_seed.read_state(path=str(missing))
    assert st == {"logged_in": False, "connectors": []}


def test_read_state_logged_in_with_connectors(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "x"},
        "claudeAiMcpEverConnected": ["claude.ai Jira", "claude.ai Granola"],
    }))
    st = trust_seed.read_state(path=str(cfg))
    assert st["logged_in"] is True
    assert st["connectors"] == ["claude.ai Jira", "claude.ai Granola"]


def test_read_state_not_logged_in(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": None}))
    st = trust_seed.read_state(path=str(cfg))
    assert st["logged_in"] is False
    assert st["connectors"] == []
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/jayjenkins/dev/pm-os-onboarding && python3 -m pytest tests/test_trust_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trust_seed'`.

**Step 3: Write minimal implementation**

```python
"""trust_seed.py - detect Claude Code login/connectors and seed Layer-2 folder
trust for the Magnolia repo in ~/.claude.json. Pure stdlib, OS-agnostic (JSON
patch — no platform branches). Detection is read-only; seeding mutates only the
target projects[<abs path>] entry and preserves everything else.
"""
import json
import os
import tempfile


def claude_config_path():
    """Mockable seam: absolute path to the user's ~/.claude.json."""
    return os.path.expanduser(os.path.join("~", ".claude.json"))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_state(path=None):
    """Read-only detection. Returns {logged_in, connectors}. Absent/garbled
    config reads as a fresh, never-logged-in user."""
    data = _load(path or claude_config_path())
    if not isinstance(data, dict):
        return {"logged_in": False, "connectors": []}
    connectors = data.get("claudeAiMcpEverConnected") or []
    if not isinstance(connectors, list):
        connectors = []
    return {"logged_in": bool(data.get("oauthAccount")), "connectors": connectors}
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trust_seed.py -v`
Expected: PASS (3 passed).

**Step 5: Commit**

```bash
git add scripts/trust_seed.py tests/test_trust_seed.py
git commit -m "feat(onboarding): trust_seed.read_state detection (Inc 1)"
```

---

## Task 2: `seed_trust` — patch the project entry (create-if-absent)

**Files:**
- Modify: `scripts/trust_seed.py`
- Test: `tests/test_trust_seed.py`

**Step 1: Write the failing tests**

```python
def test_seed_trust_creates_project_entry(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"accountUuid": "x"}, "projects": {}}))
    res = trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    assert res["status"] == "seeded"
    data = json.loads(cfg.read_text())
    entry = data["projects"]["/repo/Magnolia"]
    assert entry["hasTrustDialogAccepted"] is True
    assert "qmd" in entry["enabledMcpjsonServers"]
    assert entry["hasClaudeMdExternalIncludesApproved"] is True


def test_seed_trust_preserves_existing_keys_and_other_projects(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "x"},
        "theme": "dark",
        "projects": {
            "/other": {"hasTrustDialogAccepted": True},
            "/repo/Magnolia": {"lastCost": 1.23, "enabledMcpjsonServers": ["foo"]},
        },
    }))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark"                       # top-level preserved
    assert data["projects"]["/other"] == {"hasTrustDialogAccepted": True}
    entry = data["projects"]["/repo/Magnolia"]
    assert entry["lastCost"] == 1.23                     # sibling key preserved
    assert set(entry["enabledMcpjsonServers"]) == {"foo", "qmd"}  # qmd added, foo kept


def test_seed_trust_idempotent(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {}}))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    entry = json.loads(cfg.read_text())["projects"]["/repo/Magnolia"]
    assert entry["enabledMcpjsonServers"] == ["qmd"]     # no duplicate


def test_seed_trust_skips_when_config_absent(tmp_path):
    missing = tmp_path / "nope.json"
    res = trust_seed.seed_trust("/repo/Magnolia", path=str(missing))
    assert res["status"] == "skipped"
    assert not missing.exists()                          # never creates the file
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trust_seed.py -k seed_trust -v`
Expected: FAIL — `AttributeError: module 'trust_seed' has no attribute 'seed_trust'`.

**Step 3: Write minimal implementation** (append to `scripts/trust_seed.py`)

```python
def _atomic_write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def seed_trust(project_path, path=None):
    """Seed Layer-2 folder trust for project_path. Mutates ONLY that project
    entry; preserves all other config. If ~/.claude.json is absent (login not
    done) we skip gracefully rather than fabricate it. Returns a result dict."""
    cfg_path = path or claude_config_path()
    data = _load(cfg_path)
    if not isinstance(data, dict):
        return {"status": "skipped", "reason": "no ~/.claude.json (run claude login first)"}
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(project_path, {})
    entry["hasTrustDialogAccepted"] = True
    entry["hasClaudeMdExternalIncludesApproved"] = True
    enabled = entry.get("enabledMcpjsonServers") or []
    if "qmd" not in enabled:
        enabled = enabled + ["qmd"]
    entry["enabledMcpjsonServers"] = enabled
    _atomic_write(cfg_path, data)
    return {"status": "seeded", "project": project_path}
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trust_seed.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add scripts/trust_seed.py tests/test_trust_seed.py
git commit -m "feat(onboarding): trust_seed.seed_trust patches project entry (Inc 1)"
```

---

## Task 3: thin CLI (`detect` / `seed <path>`) for the installer

**Files:**
- Modify: `scripts/trust_seed.py`
- Test: `tests/test_trust_seed.py`

**Step 1: Write the failing test** (CLI via subprocess to exercise `__main__`)

```python
import subprocess, sys, os

def test_cli_detect_prints_json(tmp_path, monkeypatch):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "claudeAiMcpEverConnected": ["claude.ai Jira"]}))
    script = os.path.join(os.path.dirname(trust_seed.__file__), "trust_seed.py")
    out = subprocess.check_output(
        [sys.executable, script, "detect", "--path", str(cfg)], text=True)
    parsed = json.loads(out)
    assert parsed["logged_in"] is True
    assert parsed["connectors"] == ["claude.ai Jira"]


def test_cli_seed_reports_status(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {}}))
    script = os.path.join(os.path.dirname(trust_seed.__file__), "trust_seed.py")
    out = subprocess.check_output(
        [sys.executable, script, "seed", "/repo/Magnolia", "--path", str(cfg)], text=True)
    assert json.loads(out)["status"] == "seeded"
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_trust_seed.py -k cli -v`
Expected: FAIL — CLI prints nothing / `SystemExit` (no `__main__`).

**Step 3: Write minimal implementation** (append to `scripts/trust_seed.py`)

```python
def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="trust_seed")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect"); d.add_argument("--path", default=None)
    s = sub.add_parser("seed"); s.add_argument("project_path"); s.add_argument("--path", default=None)
    args = p.parse_args(argv)
    if args.cmd == "detect":
        print(json.dumps(read_state(path=args.path)))
    else:
        print(json.dumps(seed_trust(args.project_path, path=args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_trust_seed.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add scripts/trust_seed.py tests/test_trust_seed.py
git commit -m "feat(onboarding): trust_seed CLI detect/seed for the installer (Inc 1)"
```

---

## Task 4: Run all five gates green, then open the PR

**Step 1: Run the gates**

```bash
cd /Users/jayjenkins/dev/pm-os-onboarding
python3 -m pytest -q
python3 scripts/card_schema.py        # → registry.json OK
python3 -m pytest tests/test_engine_no_jay.py -q
python3 scripts/portability_gate.py   # → portability OK
python3 scripts/program_schema.py     # → programtypes OK
```
Expected: pytest all green; the four named gates print their OK lines. (`trust_seed.py` has no person literals and no OS branches → de-personalization + portability gates pass.)

**Step 2: Push the branch and open the PR** (merge authority = PRs for Jay)

```bash
git push -u origin feat/onboarding-front-door
gh pr create --base main --title "Onboarding front door — Inc 1: trust_seed + detection" \
  --body "First increment of the onboarding front-door epic (design: docs/plans/2026-06-18-onboarding-front-door-design.md). Adds scripts/trust_seed.py: read-only detection of Claude login + previously-authorized connectors, and Layer-2 folder-trust seeding (trust dialog + qmd enablement + external-includes) that mutates only the target projects[] entry. Pure stdlib, fully unit-tested, no external writes (Tier-1). Includes the headless-trust spike finding. 🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Notes for the executor

- Work in the `/Users/jayjenkins/dev/pm-os-onboarding` worktree (branch `feat/onboarding-front-door`). Never `git checkout` another branch; inspect history with `git show`/`git diff` only.
- `trust_seed.py` is **Tier-1** — it writes only to the local `~/.claude.json`, never to an external system. No Tier-2 confirm needed.
- Do NOT run `seed` against the real `~/.claude.json` during tests — every test uses a `tmp_path` config. (Patching the live file races a running Claude Code session; the installer does it in Inc 4, in the install phase, by design.)
- Keep runtime output ASCII (hyphen, not em-dash) per invariant #8.
