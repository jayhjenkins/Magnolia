# Cadence Weekly Prioritization Digest (inc3b) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Make the Monday priorities drumbeat go live - a learned, judged `priority-digest` worker drafts the weekly digest from the operator's portfolio, surfaces it for tuning, and (once it earns the rung) sends it through the existing Tier-2 message path.

**Architecture:** The cycle reconciler dispatches the worker (`produce-artifact`) and creates rate-capped send cards (`draft-message`); the worker reads the portfolio + trailing digests and writes a versioned artifact + a `send-message` card; the existing `handle_send_message`/`adapters.publish`/`NeedsConfirmation` path sends it (one Tier-2 confirm, degrades to draft-only). The digest is an interpretation that rides the existing judge + ladder at `shadow`; "automatic over time" is the climb, not new code.

**Tech Stack:** Python (program_lib, reconcile.py, program_schema.py, task_dispatch), a `claude -p` worker (`.md`), JS (cadence.js), JSON registry. Design: `docs/plans/2026-06-18-cadence-weekly-digest-design.md`.

**What inc3a already gave us (reuse, do not rebuild):** `CLOSED_ACTIONS` in `program_schema.py` already contains `produce-artifact` + `draft-message`; `render_view` already surfaces `items`; `_collect_emissions`/`_project_observations` already feed the Cadence tab; the full send path (`handle_send_message` -> `_attempt_send_message` -> `adapters.publish("messaging")` -> `NeedsConfirmation`) and `message-writer` worker already exist; `ladder_lib.tier_of` defaults unknown task_types to `shadow`.

**The five green gates (run before EVERY commit):** `python3 -m pytest -q` · `python3 scripts/card_schema.py` (-> `registry.json OK`) · `python3 -m pytest tests/test_engine_no_jay.py` · `python3 scripts/portability_gate.py` (-> `portability OK`) · `python3 scripts/program_schema.py` (-> `programtypes OK`).

**Standing contract (every task):** ASCII-safe runtime output - hyphen not em-dash, ASCII quotes. Identity (channel, distro, "me") via `profile_lib`, never a literal. Append-only artifacts (invariant #6). Tier-1 except the send (Tier-2 via the existing seam only - no second shipper).

---

### Task 1: Versioned digest-artifact writer in program_lib

**Files:**
- Modify: `scripts/program_lib.py`
- Test: `tests/test_program_artifacts.py` (create)

The worker must write a digest that is NEVER overwritten (invariant #6). Provide a deterministic, testable writer the worker calls, so versioning cannot be gotten wrong by a `claude -p` Write.

**Step 1: Write failing tests**

```python
# tests/test_program_artifacts.py
import program_lib

def _seed(tmp_path, monkeypatch):
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))
    pid, _ = program_lib.create_program(type="weekly-priorities", title="WP",
        owner_role="product", intent="x", root=str(tmp_path))
    return pid

def test_write_artifact_versions_never_overwrites(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    p1 = program_lib.write_artifact(pid, "2026-W25-priorities", "v1 body", root=str(tmp_path))
    p2 = program_lib.write_artifact(pid, "2026-W25-priorities", "v2 body", root=str(tmp_path))
    assert p1.endswith("2026-W25-priorities-v1.md")
    assert p2.endswith("2026-W25-priorities-v2.md")
    assert open(p1).read() == "v1 body"   # v1 untouched (invariant #6)
    assert open(p2).read() == "v2 body"

def test_write_artifact_path_is_under_program_artifacts(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    p = program_lib.write_artifact(pid, "slug", "body", root=str(tmp_path))
    assert f"/artifacts/{pid}/" in p.replace("\\", "/")

def test_iter_recent_artifacts_returns_newest_first_capped(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    for wk in ("W22", "W23", "W24", "W25"):
        program_lib.write_artifact(pid, f"2026-{wk}-priorities", f"{wk} body", root=str(tmp_path))
    recent = program_lib.iter_recent_artifacts(pid, n=3, root=str(tmp_path))
    assert len(recent) == 3
    assert "W25" in recent[0]["body"] and "W22" not in [r["body"][:3] for r in recent]
```

**Step 2:** Run `pytest tests/test_program_artifacts.py -v` -> FAIL (no `write_artifact`).

**Step 3: Implement** in `program_lib.py`:
- `_artifacts_dir(program_id, root)` -> `datasets/programs/artifacts/<program_id>/` (mkdir -p).
- `write_artifact(program_id, slug, content, root=None) -> str`: find the highest existing `-vN.md` for `slug`, write `slug-v{N+1}.md`, return the path. Never opens an existing version for write.
- `iter_recent_artifacts(program_id, n=3, root=None) -> list[{slug, version, path, body, mtime}]`: newest-first by (slug period, version), capped at `n`. Tolerant of a missing dir (-> []).
- Add a tiny CLI branch so the worker can call it: `python3 scripts/program_lib.py write-artifact <pid> <slug> <file>` reading content from a file path (avoids shell-quoting a multi-line digest).

**Step 4:** Run tests -> PASS. **Step 5:** Run the five gates. **Step 6:** Commit.

---

### Task 2: `items` + nudge-cap + counter-metric schema validation

**Files:**
- Modify: `scripts/program_schema.py`
- Test: `tests/test_program_schema.py` (extend)

`CLOSED_ACTIONS` already accepts `produce-artifact`/`draft-message`. Add: validate `max_nudges_per_person_per_week` on any emitter that declares it (int >= 0), and validate the cycle `items` list shape if a type seeds default items.

**Step 1: Write failing tests** (extend `tests/test_program_schema.py`): a registry copy with `max_nudges_per_person_per_week: "lots"` (non-int) fails with an ASCII message naming the field; `max_nudges_per_person_per_week: 1` passes; a negative value fails.

**Step 2:** Run -> FAIL. **Step 3: Implement:** in the emitter loop, if `"max_nudges_per_person_per_week" in em`, require `isinstance(v, int) and v >= 0` (note: `bool` is an `int` subclass - reject `bool` explicitly). ASCII-safe message. **Step 4:** PASS. **Step 5:** gates (esp. `program_schema.py` -> `programtypes OK`). **Step 6:** Commit.

---

### Task 3: `produce-artifact` emitter - dispatch the worker once per period

**Files:**
- Modify: `scripts/cadence/reconcile.py` (`_evaluate_emitters`)
- Test: `tests/test_cadence_reconcile.py` (extend)

On a fresh cycle period, a `produce-artifact` emitter dispatches the `priority-digest` worker as an agent task for this program, deduped to once per period.

**Step 1: Write failing tests** (extend `tests/test_cadence_reconcile.py`, reuse the isolated-queue fixture that patches `task_lib.TASKS_DIR` AND `task_lib.ARCHIVE_DIR` - the inc3a Task-8 lesson):

```python
def test_produce_artifact_dispatches_priority_digest_agent_task(monkeypatch, ...):
    # weekly-priorities program at a fresh period, emitter {on:"cycle-fresh", action:"produce-artifact", worker:"priority-digest"}
    calls = []
    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: calls.append(tid))
    res = reconcile.reconcile_program(pid, root=..., force=True)
    # an agent-queue task with task_type=priority-digest tagged [pid,"cadence"] was created AND dispatched
    assert any(t["task_type"] == "priority-digest" and "agent" == t["queue"] for t in created)
    assert calls  # dispatch fired exactly once

def test_produce_artifact_deduped_within_period(...):
    # a second reconcile in the same period does NOT create/dispatch a second digest task
```

**Step 2:** FAIL. **Step 3: Implement** a `produce-artifact` branch in `_evaluate_emitters`:
- Trigger: a fresh cycle (the function already only runs emitters on a new cycle; gate the branch on `em.get("on")` being the cycle trigger, e.g. `"cycle-fresh"` or matching `f"drift:{verdict}"`-agnostic - keep it a named trigger like the others).
- Dedupe: skip if an OPEN agent task tagged with `program_id` and `task_type=="priority-digest"` already exists (mirror `_open_human_tags`/`_open_propose_update_ops` - add `_open_agent_task_types(task_lib, program_id)`).
- Create `task_lib.create_task(queue="agent", task_type="priority-digest", creator="cadence", tags=[program_id,"cadence"], title=f"{title}: draft {period} digest", description=...)`.
- Dispatch via a thin `_dispatch_agent_task(task_id)` that mirrors `cron_lib._auto_dispatch` (spawn `task_dispatch.py --task <id>` detached with `platform_lib.headless_claude_env()` + `platform_lib.process_group_kwargs`). Keep it a module function so tests monkeypatch it (no real `claude` spawn in tests).

**Step 4:** PASS. **Step 5:** gates. **Step 6:** Commit.

---

### Task 4: `draft-message` emitter - rate-capped send card + counter-metric

**Files:**
- Modify: `scripts/cadence/reconcile.py` (`_evaluate_emitters`)
- Test: `tests/test_cadence_reconcile.py` (extend)

This is the rate fence made real and testable. A `draft-message` emitter creates a `send-message` collab card from a template, enforcing `max_nudges_per_person_per_week` per recipient, recording a response-rate counter-metric.

**Step 1: Write failing tests:**

```python
def test_draft_message_creates_send_message_collab_card(...):
    # emitter {on:..., action:"draft-message", template:"nudge-owner", max_nudges_per_person_per_week:1}
    # -> a queue="collab", task_type="send-message" card tagged [pid,"cadence"] created
def test_draft_message_respects_nudge_cap_and_logs_suppression(...):
    # with the recipient already nudged this period (a prior send-message card this period),
    # a second draft-message is SUPPRESSED (no new card) and the cycle log records the suppression reason
def test_draft_message_records_response_rate_counter(...):
    # the program frontmatter gains/updates a nudge counter for the period (sent vs acked)
```

**Step 2:** FAIL. **Step 3: Implement** a `draft-message` branch:
- Resolve recipient via `profile_lib` (channel/distro) - never a literal; degrade to a generic role target if unset.
- Count this period's `send-message` cards tagged `program_id` for that recipient (reuse the open/period scan); if `count >= max_nudges_per_person_per_week`, suppress: append a suppression note to the facts for the cycle log (`emitted: nudge suppressed (cap N/wk)`), do NOT create a card.
- Else create `task_lib.create_task(queue="collab", task_type="send-message", creator="cadence", tags=[program_id,"cadence"], card_type=...existing send-message card..., description=<drafted body>, message_channel=<profile>, message_to=<profile>)`.
- Bump a per-period counter in fm (e.g. `fm["nudge_counts"][period][recipient] += 1`) and a `response_rate` stub the UI reads. Append-only semantics for the cycle log.

Note: the WEEKLY DIGEST's primary send is created by the worker (Task 5) carrying the digest body; `draft-message`-as-emitter is the rate-capped nudge primitive (used by roadmap-initiative nudge-owner and available to weekly-priorities). Keep the cap logic shared.

**Step 4:** PASS. **Step 5:** gates. **Step 6:** Commit.

---

### Task 5: The `priority-digest` worker (build-new via meta-create-worker)

**Files:**
- Create: `scripts/workers/priority-digest.md`
- Test: worker-validate (`python3 scripts/task_dispatch.py --dry-run` parses it; `match_worker` selects it for `task_type=priority-digest`) + `tests/test_priority_digest_worker.py` (frontmatter + match assertions, NOT claude output)

**Step 1: Write failing tests:** load the worker frontmatter; assert `match.task_type == ["priority-digest"]`, `tier` is set, `allowed_tools` includes Read/Bash/Write but the body instructs writing artifacts ONLY via `program_lib.py write-artifact` (versioning) and creating the send card via the existing send-message task shape; assert `match_worker({"task_type":"priority-digest",...}, workers)` selects `priority-digest`.

**Step 2:** FAIL. **Step 3: Implement** the worker `.md` (prose; brief it with the design doc). Body must instruct:
- Read the portfolio: the target program's `items` + `capture` observations (since last digest); `program_lib.iter_recent_artifacts(pid, 3)` for trailing digests; other ACTIVE programs' `drift` + observation ledgers (enumerate `datasets/programs/*.md`, never a family literal).
- Reconcile: confirm/reorder priorities; **flag every slip explicitly** (a dropped item is named with a reason, never silently gone); raise new candidate priorities.
- Read `ladder_lib.tier_of("priority-digest")`: at `shadow`/`supervised` produce the digest as a PROPOSAL the operator reviews; only auto-finalize at `autonomous`.
- Write the digest via `python3 scripts/program_lib.py write-artifact <pid> <period>-priorities <file>` (versioned).
- Create the send as a `send-message` collab card carrying the digest body + `message_channel`/`message_to` from `profile_lib` (the worker emits the card; the existing Tier-2 path sends it). Degrade to draft-only when messaging is unconfigured.
- ASCII-safe output; the operator's voice; never send directly.

**Step 4:** PASS + worker-validate. **Step 5:** gates. **Step 6:** Commit.

---

### Task 6: Wire weekly-priorities registry + seed PROG-0005 items

**Files:**
- Modify: `cadence/programtypes/registry.json` (weekly-priorities)
- Modify: `datasets/programs/PROG-0005.md` (seed declared `items`)
- Test: `tests/test_program_schema.py` (gate stays green) + a render test asserting items/emitters surface

**Step 1: Write failing test:** assert the weekly-priorities type now has `produce-artifact` + `draft-message` emitters with a `max_nudges_per_person_per_week`, and `render_view` of PROG-0005 surfaces the seeded `items`.

**Step 2:** FAIL. **Step 3: Implement:** add to weekly-priorities `emitters`: `{on:"cycle-fresh", action:"produce-artifact", worker:"priority-digest"}` and `{on:"cycle-fresh", action:"draft-message", template:"weekly-digest", max_nudges_per_person_per_week:1}` (keep the existing `escalate`). Seed PROG-0005 with a small `items:` list (role-referenced, never names). **Step 4:** PASS. **Step 5:** gates (`program_schema.py` MUST stay green - this is the schema-validated change). **Step 6:** Commit.

---

### Task 7: Cadence tab - digest/emission history in row expansion

**Files:**
- Modify: `ui/task-board/js/cadence.js` (+ `index.html` if a block is added)
- Test: `tests/test_cadence_payload.py` or the existing render test (assert the payload carries artifacts + emissions; the JS reads them)

`render_view` already projects `emissions` + `items`. Add a `digests` projection (from `iter_recent_artifacts`) to the payload and render a compact digest-history block in the row expansion, theme tokens only, reusing the inc3a emission tone helper.

**Step 1: Write failing test:** `render_view`/`build_cadence_payload` for a program with artifacts includes a `digests` list (period, version, path). **Step 2:** FAIL. **Step 3: Implement:** add `digests` to `render_view` (cap 3, newest-first); in `cadence.js` render them under the existing Emissions block (`cadenceEmissionTone` tokens; no hardcoded colors). **Step 4:** PASS. **Step 5:** gates (esp. `portability_gate.py`). **Step 6:** Commit.

---

### Task 8: Final integration review + live e2e on :8743

- Dispatch the final code-quality reviewer over the whole branch (spec + quality).
- Restart `:8743`. Seed: PROG-0005 with `items` + a `capture` observation; ensure a roadmap-initiative program is active. Force a fresh cycle (`reconcile.py` CLI or the scheduler) -> assert: a `priority-digest` agent task dispatched; a versioned digest artifact written under `datasets/programs/artifacts/PROG-0005/`; an agent-output card on Now with the ordering + an explicit slip flag + the captured item; a `send-message` collab card created; walking it to send raises the Tier-2 confirm (or degrades to draft-only with mgc unconfigured). Verify the nudge cap suppresses a second per-recipient nudge in the period and the cycle log records it.
- Clean up ALL e2e artifacts (`git checkout datasets/programs/`, remove temp artifacts + cards). Confirm seeds pristine.
- Then `superpowers:finishing-a-development-branch` -> merge to local main (not pushed) unless a PR is requested.
