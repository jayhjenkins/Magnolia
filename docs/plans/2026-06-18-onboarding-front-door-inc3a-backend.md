# Onboarding Front Door — Increment 3a: onboarding backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The server-side half of in-UI onboarding: a completion-marker gate, an `onboard_runner` that drives the existing `meta-onboard` skill via a multi-turn headless `claude -p` (mirroring `adapt_runner`), its transcript store, a fairway hook bounding the broad-privilege agent, and the SSE route + first-run gate in `task_server.py`. No browser UI yet (that's Inc 3b) — everything here is unit-testable by mocking the `_spawn` seam and the SSE handler.

**Architecture:** Mirror the proven Adapt pattern. `onboard_runner.run_turn(message)` reuses `chat_runner.build_chat_cmd(...)` with an onboarding harness (`--append-system-prompt`), a BROAD allowlist (`Bash` + `mcp__claude_ai_*` + Read/Write/Edit — onboarding genuinely installs tools, runs auth, writes profile), bounded by a `--settings` fairway hook (`scripts/hooks/onboard_settings.json`). Turns persist via `onboard_transcript` (mirror `chat_transcript`). The server streams it through the existing `live_runs` + `_stream_live_run` substrate. The first-run gate serves onboarding until `profile_lib.onboarding_complete()` is true; completion is an explicit `onboarded: true` marker written by `meta-onboard`'s final step (NOT mere `profile/` existence, which step 0 creates early). A startup migration stamps the marker on already-populated legacy profiles so existing installs are never re-gated.

**Tech Stack:** Python 3 stdlib, pytest (mock `_spawn` like `test_adapt_runner.py`; FakeHandler SSE like `test_chat_route.py`), ruamel.yaml (already a dep) for the config flag.

**Increment roadmap:** Inc 1 ✅, Inc 2 ✅, Inc 4 ✅. **Inc 3a = this (backend).** Inc 3b = onboarding room UI + board reveal + live e2e on :8743 (next, after 3a review). All on `feat/onboarding-front-door` / PR #43.

**Security note (read first):** the onboarding agent is HIGH-PRIVILEGE — the opposite of the locked-down chat panel (`CHAT_ALLOWED_TOOLS`). It needs broad `Bash` + MCP. That is no more dangerous than `onboard me` is today (same power, UI-driven), but it MUST be bounded by the fairway hook (Task 4), exactly as Adapt bounds its build agent. Brief subagents accordingly.

---

## Task 1: profile completion marker — `onboarding_complete` / `mark_onboarded` / legacy migration

**Files:**
- Modify: `scripts/profile_lib.py`
- Test: `tests/test_profile_onboarding_gate.py`

**Step 1: Write the failing tests** (use a temp `root` so the real profile is never touched)

```python
import os
import profile_lib


def _mk_live_profile(root, *, identity_name="", onboarded=None):
    import shutil
    src = os.path.join(profile_lib.PM_OS_DIR, "profile.example")
    dst = os.path.join(root, "profile")
    shutil.copytree(src, dst)
    if identity_name or onboarded is not None:
        import ruamel.yaml
        y = ruamel.yaml.YAML()
        # config.yaml carries the onboarded flag
        cfgp = os.path.join(dst, "config.yaml")
        with open(cfgp) as fh: cfg = y.load(fh) or {}
        if onboarded is not None: cfg["onboarded"] = onboarded
        with open(cfgp, "w") as fh: y.dump(cfg, fh)
        if identity_name:
            pp = os.path.join(dst, "profile.yaml")
            with open(pp) as fh: prof = y.load(fh) or {}
            prof["display_name"] = identity_name
            with open(pp, "w") as fh: y.dump(prof, fh)


def test_not_complete_when_no_live_profile(tmp_path):
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is False


def test_complete_when_marker_set(tmp_path):
    _mk_live_profile(str(tmp_path), onboarded=True)
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_not_complete_when_profile_exists_without_marker(tmp_path):
    _mk_live_profile(str(tmp_path), onboarded=False)
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is False


def test_mark_onboarded_sets_the_flag(tmp_path):
    _mk_live_profile(str(tmp_path))
    profile_lib.mark_onboarded(root=str(tmp_path))
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_legacy_migration_stamps_populated_profile(tmp_path):
    # An existing install: real identity, no onboarded flag -> migration marks it.
    _mk_live_profile(str(tmp_path), identity_name="Real Person")
    changed = profile_lib.migrate_legacy_onboarded(root=str(tmp_path))
    assert changed is True
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_legacy_migration_skips_when_no_live_profile(tmp_path):
    assert profile_lib.migrate_legacy_onboarded(root=str(tmp_path)) is False
```

**Step 2: Run to verify fail**

Run: `cd /Users/jayjenkins/dev/pm-os-onboarding && python3 -m pytest tests/test_profile_onboarding_gate.py -v`
Expected: FAIL — helpers undefined.

**Step 3: Implement** (add to `scripts/profile_lib.py`; reuse its existing YAML load/atomic-write helpers — match how `config()` reads and how other writers persist config.yaml. Inspect the file first for the existing config read + write idiom and follow it.)

```python
def profile_is_live(root=None):
    """True if the live profile/ dir exists (not the profile.example fallback)."""
    root = root or PM_OS_DIR
    return os.path.isdir(os.path.join(root, "profile"))


def onboarding_complete(root=None):
    """True once onboarding has finished: the live profile exists AND its config
    carries `onboarded: true`. NOT mere profile/ existence (meta-onboard creates
    profile/ early, at step 0)."""
    if not profile_is_live(root):
        return False
    return bool(config(root=root).get("onboarded"))


def mark_onboarded(root=None):
    """Stamp `onboarded: true` into the live profile config (the completion
    marker). Called by meta-onboard's final step."""
    _set_config_value("onboarded", True, root=root)   # use the file's existing config writer


def migrate_legacy_onboarded(root=None):
    """Backward-compat: an existing install (live profile already populated with a
    real identity) predates the marker - stamp it so it is never re-gated into
    onboarding. Returns True if it stamped, False otherwise. Idempotent."""
    if not profile_is_live(root):
        return False
    cfg = config(root=root)
    if cfg.get("onboarded"):
        return False
    name = (profile(root=root).get("display_name") or "").strip()
    placeholder = name == "" or name.lower() in ("your name", "name")
    if placeholder:
        return False   # genuinely fresh/example-shaped; let onboarding run
    mark_onboarded(root=root)
    return True
```

NOTE: if `profile_lib` has no generic `_set_config_value`, write `mark_onboarded` using the same load->mutate->atomic-write idiom the module already uses for config (find it; do NOT hand-roll a new write path). Keep it consistent.

**Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_profile_onboarding_gate.py -v`
Expected: PASS (6).

**Step 5: Commit**

```bash
git add scripts/profile_lib.py tests/test_profile_onboarding_gate.py
git commit -m "feat(onboarding): profile onboarding-complete marker + legacy migration (Inc 3a)"
```

---

## Task 2: `onboard_transcript.py` — turn persistence

**Files:**
- Create: `scripts/onboard_transcript.py`
- Test: `tests/test_onboard_transcript.py`

**Step 1:** Read `scripts/chat_transcript.py` first and MIRROR it, but single-session (no task_id — onboarding is one session). Public surface: `append_event(event, root=None)`, `read_events(root=None)` (returns list), `reset(root=None)` (for a fresh run). Store under `<profile_or_state>/onboard_transcript.jsonl` — pick a location that exists pre-profile (e.g. `<repo>/logs/onboard_transcript.jsonl`, since profile/ may not exist yet at first turn). Make the path a mockable module-level `STORE` or a function so tests can point it at tmp_path.

**Step 2–5:** TDD: write failing tests (append then read returns events in order; reset clears; read with no file returns []), run-fail, implement minimal mirror of chat_transcript, run-pass, commit:
```bash
git commit -m "feat(onboarding): onboard_transcript single-session turn store (Inc 3a)"
```

---

## Task 3: the fairway hook — `scripts/hooks/onboard_settings.json`

**Files:**
- Create: `scripts/hooks/onboard_settings.json`
- Test: `tests/test_onboard_settings.py`

**Step 1:** Read `scripts/hooks/adapt_settings.json` and any hook script it references. The onboarding fairway must ALLOW the legitimate onboarding actions (write under repo + profile, run the onboarding bash commands, use `mcp__claude_ai_*`) while blocking writes outside the repo/home-profile and obviously destructive operations. MIRROR the adapt hook's structure (PreToolUse). If adapt uses a companion hook script, decide whether onboarding reuses it with a broader fairway or needs its own; prefer reuse with a parameter if clean.

**Step 2–5:** TDD at the achievable level: a test asserting the settings file is valid JSON, declares a PreToolUse hook, and is shaped like adapt_settings.json (same keys). Implement, pass, commit:
```bash
git commit -m "feat(onboarding): onboard fairway hook settings (Inc 3a)"
```

---

## Task 4: `onboard_runner.py` — drive meta-onboard headlessly

**Files:**
- Create: `scripts/onboard_runner.py`
- Test: `tests/test_onboard_runner.py`

**Step 1:** Read `scripts/adapt_runner.py` fully and MIRROR its shape. Key points:
- `ONBOARD_ALLOWED_TOOLS` — BROAD: `["Bash", "Read", "Grep", "Glob", "Write", "Edit", "mcp__claude_ai_*", "mcp__qmd__*", "Skill"]` (onboarding runs installers/auth and invokes the meta-onboard Skill). Document WHY this is broad and that the fairway hook (Task 3) is the real bound.
- A harness prompt (a `_harness()` builder, like `adapt_harness`) that instructs the agent: you are running headless behind the board to onboard this user; invoke the `meta-onboard` skill; narrate browser-auth steps in plain language ("a sign-in window just opened"); when fully done, FINISH by calling `profile_lib.mark_onboarded()` (or run the CLI) and print the literal sentinel line `ONBOARDING_COMPLETE`.
- `ONBOARD_SETTINGS_PATH = os.path.join(PM_OS_DIR, "scripts", "hooks", "onboard_settings.json")`.
- `run_turn(message)` — mirror adapt_runner.run_turn but session-keyed to a fixed onboarding session id (persisted so subsequent turns resume). Reuse `chat_runner.build_chat_cmd(..., append_system_prompt=harness, settings=ONBOARD_SETTINGS_PATH, allowed_tools=",".join(ONBOARD_ALLOWED_TOOLS))`, `chat_runner.normalize`, `chat_runner._spawn`. Persist via `onboard_transcript`.
- **Completion detection:** when a normalized event's text contains the `ONBOARDING_COMPLETE` sentinel (or the result event arrives after the marker is set), emit a synthetic `{"kind": "onboarding_complete", "role": "system", ...}` event (append to transcript + yield).

**Step 2:** Write failing tests mirroring `test_adapt_runner.py`: monkeypatch `onboard_runner._spawn` (imported from chat_runner) to yield canned stream-json lines; point `onboard_transcript` at tmp via monkeypatch; assert (a) a normal text turn persists + yields; (b) a stream containing the `ONBOARDING_COMPLETE` sentinel yields an `onboarding_complete` event; (c) `build_chat_cmd` is called with the broad allowlist + the settings path (assert via a monkeypatched spy). Run-fail.

**Step 3:** Implement minimally per the mirror. **Step 4:** run-pass. **Step 5:** commit:
```bash
git commit -m "feat(onboarding): onboard_runner drives meta-onboard headless w/ fairway + completion sentinel (Inc 3a)"
```

---

## Task 5: server — first-run gate + SSE route + startup migration

**Files:**
- Modify: `scripts/task_server.py`
- Test: `tests/test_onboarding_route.py`

**Step 1:** Study `handle_chat` (task_server.py ~1602-1677), `_stream_live_run` (~1699-1733), `_route_request` (~2638), and `do_GET` (~3044). Then write failing tests mirroring `tests/test_chat_route.py` (FakeHandler; monkeypatch `onboard_runner.run_turn` to a canned persisting generator; `live_runs._reset()` autouse):
- `POST /api/onboarding/run` with `{"message": "..."}` starts a decoupled `live_runs` run and streams events (assert SSE frames written, `event: done` at end).
- a concurrent onboarding run returns 409 (mirror chat's guard).
- a first-run gate unit test: a helper `_should_onboard(handler)` (or however you factor it) returns True when `profile_lib.onboarding_complete()` is False and False when True (monkeypatch profile_lib).

**Step 2:** run-fail. **Step 3:** implement:
- Add route `POST /api/onboarding/run` in `_route_request` -> `handle_onboarding_run(handler)`, mirroring `handle_chat`: validate message, guard concurrent run via `live_runs.is_live`, `live_runs.start("onboarding", onboard_runner.run_turn(message), lambda e: None)`, then `_sse_begin` + `_stream_live_run(handler, "onboarding", lambda: onboard_transcript.read_events(), "The onboarding run ended unexpectedly. You can retry.")`.
- First-run gate in `do_GET`: AFTER `_route_request("GET")` returns False and BEFORE the `/` -> index.html rewrite, if `parsed.path in ("/", "/index.html")` and NOT `profile_lib.onboarding_complete()`, rewrite `self.path = "/onboarding.html"` (Inc 3b ships that page; for 3a a minimal placeholder `ui/task-board/onboarding.html` is fine so the route resolves — note it's replaced in 3b).
- Startup migration: in the server `main()` (or module init where the server boots), call `profile_lib.migrate_legacy_onboarded()` once so existing installs are stamped and never gated.

**Step 4:** run-pass (and full suite). **Step 5:** commit:
```bash
git commit -m "feat(onboarding): first-run gate + /api/onboarding/run SSE route + legacy migration (Inc 3a)"
```

---

## Task 6: meta-onboard completion extension

**Files:**
- Modify: `.claude/skills/meta-onboard/SKILL.md`

**Step 1:** Add a short, explicit completion step to the skill (keep it denylist-clean — it is scanned by `test_engine_no_jay.py`): at the very end (after step 7 / Close), the skill MUST (a) run `python3 scripts/profile_lib.py`-style marking — i.e. set the onboarding-complete marker by calling the documented mechanism (`mark_onboarded`, via a one-line CLI or a Bash python -c), and (b) print the literal line `ONBOARDING_COMPLETE` so the runner detects terminal state. Also add a one-line note that when run headless-in-UI it should narrate browser-auth steps ("a sign-in window just opened - finish it and come back"). Do not restructure the existing 7 steps.

**Step 2:** Verify the de-personalization gate still passes: `python3 -m pytest tests/test_engine_no_jay.py -q`. **Step 3:** Commit:
```bash
git add .claude/skills/meta-onboard/SKILL.md
git commit -m "feat(onboarding): meta-onboard sets completion marker + emits sentinel for headless UI (Inc 3a)"
```

---

## Task 7: all five gates green, push

```bash
cd /Users/jayjenkins/dev/pm-os-onboarding
python3 -m pytest -q
python3 scripts/card_schema.py        # registry.json OK
python3 -m pytest tests/test_engine_no_jay.py -q
python3 scripts/portability_gate.py   # portability OK
python3 scripts/program_schema.py     # programtypes OK
git push   # stacks onto PR #43 - NO new PR
```

---

## Notes for the executor

- Worktree `/Users/jayjenkins/dev/pm-os-onboarding`, branch `feat/onboarding-front-door`. NEVER `git checkout` another branch; inspect with `git show`/`git diff`.
- This is mostly **mirror-existing-patterns** work: `onboard_runner` ~ `adapt_runner`, `onboard_transcript` ~ `chat_transcript`, the SSE route ~ `handle_chat`, the fairway ~ `adapt_settings.json`. READ the originals first; match their idioms exactly rather than inventing.
- Tier-1 from the engine's seam view (no adapter publish). The agent it spawns is high-privilege but bounded by the fairway hook (Task 3) - that boundedness is load-bearing; do not widen it casually.
- Mock `_spawn` in every runner test; never spawn a real `claude`. Use FakeHandler for the SSE route; never bind a real socket.
- If the full suite dirties `profile.example/capabilities.json`, the fix is already on-branch; restore and do not stage it.
- ASCII runtime output (hyphen, not em-dash). The `ONBOARDING_COMPLETE` sentinel is literal/exact - the runner greps for it.
- Inc 3b (the browser room + board reveal) is NOT in this increment; the `onboarding.html` placeholder here is replaced there.
