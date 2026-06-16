# Cadence Slices 1+2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Cadence substrate (program files + `program_lib` + program-type registry + `program_schema.py` gate) and the read-only Cadence tab rendered from a real `GET /api/cadence`.

**Architecture:** Every surface mirrors an existing strict pattern — `program_lib.py`↔`task_lib.py`, `program_schema.py`↔`card_schema.py`, `cadence/programtypes/registry.json`↔`ui/task-board/cardtypes/registry.json`, `js/cadence.js`↔`js/schedules.js`, `GET /api/cadence`↔`GET /api/cron`. Program files are the canonical store (brief §4); the API maps them into the designer prototype's render contract. Tier-1: zero external writes (read-only tab, no emitters).

**Tech Stack:** Python 3 (stdlib + PyYAML + `platform_lib` for locking), vanilla HTML/CSS/JS (no framework/bundler), `pytest`.

**Design doc:** [`2026-06-16-cadence-substrate-and-tab-design.md`](./2026-06-16-cadence-substrate-and-tab-design.md). **Brief:** [`2026-06-12-cadence-design-brief.md`](./2026-06-12-cadence-design-brief.md). **Designer handoff:** `~/Downloads/magnolia-cadence-extract/design_handoff_cadence/` (`Cadence.dc.html` = canonical render math + seed data/voice; `README.md` = the integration guide).

**The five green gates (run before EVERY commit that touches code):**
```bash
python3 -m pytest
python3 scripts/card_schema.py            # -> registry.json OK
python3 -m pytest tests/test_engine_no_jay.py
python3 scripts/portability_gate.py       # -> portability OK
python3 scripts/program_schema.py         # -> programtypes OK   (NEW, exists after Task 2)
```

**Branch:** `feat/cadence-substrate-tab` (already created; design doc already committed). Never commit to `main`. Subagents: inspect history with `git show`/`git diff`, never `git checkout`. ASCII-safe runtime output (hyphen, not em-dash).

---

### Task 1: `program_lib.py` — file format + CRUD

Mirror `scripts/task_lib.py` (read it first: `_parse_task_file` line 77, `_write_task_file` line 104, `_next_id` line 198, `create_task` line 220, `read_task` line 387, `list_tasks` line 412). Use `scripts/platform_lib.py` `lock`/`unlock` for the counter (task_lib's pattern). Programs live in `datasets/programs/`, IDs `PROG-{:04d}` via `datasets/programs/_counter`.

**Files:**
- Create: `scripts/program_lib.py`
- Test: `tests/test_program_lib.py`

**Step 1: Write failing tests** (`tests/test_program_lib.py`). Use a `tmp_path` root so no real datasets are touched (pass `root=` like task_lib does).

```python
import os
from scripts import program_lib as pl  # adjust import to match how task_lib is imported in its tests

def test_create_and_read_roundtrip(tmp_path):
    root = str(tmp_path)
    pid, path = pl.create_program(
        type="roadmap-initiative", title="Payments revamp",
        owner_role="product", root=root,
        frontmatter_extra={"phase": "execution", "drift": "holding"},
        intent="Rebuild reconciliation.")
    assert pid == "PROG-0001"
    assert os.path.isfile(path)
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["title"] == "Payments revamp"
    assert prog["frontmatter"]["type"] == "roadmap-initiative"
    assert "Rebuild reconciliation." in prog["body"]

def test_next_id_increments(tmp_path):
    root = str(tmp_path)
    a, _ = pl.create_program(type="weekly-priorities", title="A", owner_role="product", root=root)
    b, _ = pl.create_program(type="weekly-priorities", title="B", owner_role="product", root=root)
    assert (a, b) == ("PROG-0001", "PROG-0002")

def test_list_programs_filters_status(tmp_path):
    root = str(tmp_path)
    pl.create_program(type="weekly-priorities", title="active one", owner_role="product",
                      root=root, frontmatter_extra={"status": "active"})
    pl.create_program(type="weekly-priorities", title="archived one", owner_role="product",
                      root=root, frontmatter_extra={"status": "archived"})
    actives = pl.list_programs(status="active", root=root)
    assert [p["frontmatter"]["title"] for p in actives] == ["active one"]

def test_write_roundtrip_validates_yaml(tmp_path):
    # malformed frontmatter must raise, mirroring task_lib's parse-back gate
    root = str(tmp_path)
    pid, path = pl.create_program(type="weekly-priorities", title="X", owner_role="product", root=root)
    prog = pl.read_program(pid, root=root)
    prog["frontmatter"]["title"] = "Edited"
    pl._write_program_file(path, prog["frontmatter"], prog["body"])
    assert pl.read_program(pid, root=root)["frontmatter"]["title"] == "Edited"
```

**Step 2: Run to verify they fail.** `python3 -m pytest tests/test_program_lib.py -v` → FAIL (module/functions missing).

**Step 3: Implement `scripts/program_lib.py`.** Port task_lib's mechanics. Required frontmatter defaults: `program_id`, `type`, `status` (default `active`), `title`, `owner_role`, `created` (ISO now), and pass-through of `frontmatter_extra` (phase, phase_entered, checkpoints, bindings, drift, last_cycle). Body template: `## Intent\n{intent}\n\n## Observations\n\n## Cycles\n`. Functions: `_program_dir(root)`, `_counter_path(root)`, `_next_id(root)`, `_parse_program_file(path)`, `_write_program_file(path, fm, body)` (parse-back YAML validation), `create_program(...)`, `read_program(program_id, root)`, `list_programs(status=None, root=None)`. Keep `root=None` → resolves to repo `datasets/` like task_lib.

**Step 4: Run to verify pass.** `python3 -m pytest tests/test_program_lib.py -v` → PASS.

**Step 5: Run all five gates** (program_schema doesn't exist yet — skip it this task; run the other four). Then commit:
```bash
git add scripts/program_lib.py tests/test_program_lib.py
git commit -m "feat(cadence): program_lib CRUD + program file format

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: program-type registry + `program_schema.py` gate + denylist extension

Read `scripts/card_schema.py` fully first — `program_schema.py` is its sibling (load registry → validate closed-sets + token-only → print `… OK`; `__main__` exits 1 on errors).

**Files:**
- Create: `cadence/programtypes/registry.json`
- Create: `scripts/program_schema.py`
- Create: `tests/test_program_schema.py`
- Modify: `tests/test_engine_no_jay.py:3-8` (extend TARGETS glob to `cadence/**/*.json`)

**Step 1: Author `cadence/programtypes/registry.json`** — the leanest set spanning all four state models and four families (see design §2). Shape:

```json
{
  "families": [
    { "id": "roadmap",  "label": "Roadmap",  "order": 1 },
    { "id": "weekly",   "label": "Weekly",   "order": 2 },
    { "id": "outcomes", "label": "Outcomes", "order": 3 },
    { "id": "eos",      "label": "EOS",      "order": 4 }
  ],
  "types": [
    { "id": "roadmap-initiative", "label": "Roadmap initiative", "family": "roadmap",
      "state_model": "pipeline",
      "phases": [
        { "id": "discovery", "label": "Discovery", "max_age_days": 21 },
        { "id": "planning",  "label": "Planning",  "max_age_days": 14 },
        { "id": "execution", "label": "Execution" },
        { "id": "shipped",   "label": "Shipped" },
        { "id": "verified",  "label": "Verified", "terminal": true }
      ],
      "cadence": "weekly",
      "sources": [ { "kind": "transcripts", "mode": "read" }, { "kind": "project_management", "mode": "read" } ],
      "presentation": { "chip_tokens": { "discovery": "--text-dim", "execution": "--accent" } } },

    { "id": "weekly-priorities", "label": "Weekly priorities", "family": "weekly",
      "state_model": "cycle", "cadence": "weekly",
      "sources": [ { "kind": "transcripts", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } },

    { "id": "eng-sync-prep", "label": "Eng sync prep", "family": "weekly",
      "state_model": "cycle", "cadence": "weekly",
      "sources": [ { "kind": "team_threads", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } },

    { "id": "did-it-work", "label": "Did it work?", "family": "outcomes",
      "state_model": "target", "cadence": "weekly",
      "sources": [ { "kind": "metrics", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } },

    { "id": "eos-rock", "label": "EOS rock", "family": "eos",
      "state_model": "pipeline",
      "phases": [
        { "id": "define", "label": "Define", "max_age_days": 14 },
        { "id": "build",  "label": "Build",  "max_age_days": 42 },
        { "id": "beta",   "label": "Beta",   "max_age_days": 21 },
        { "id": "ga",     "label": "GA", "terminal": true }
      ],
      "cadence": "weekly",
      "sources": [ { "kind": "eos_sheet", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } },

    { "id": "eos-cycle", "label": "EOS cycle", "family": "eos",
      "state_model": "cycle", "cadence": "weekly",
      "sources": [ { "kind": "eos_sheet", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } },

    { "id": "eos-issues", "label": "EOS issues", "family": "eos",
      "state_model": "register", "cadence": "weekly",
      "sources": [ { "kind": "eos_sheet", "mode": "read" } ],
      "presentation": { "chip_tokens": {} } }
  ]
}
```
(Trim/rename to taste, but keep ≥1 type per state_model and per family. **No identity literals** — generic role/vocabulary only.)

**Step 2: Write failing tests** (`tests/test_program_schema.py`):

```python
from scripts import program_schema as ps

def test_seed_registry_is_valid():
    assert ps.validate() == []   # the real seed registry passes

def test_rejects_unknown_state_model():
    reg = {"families":[{"id":"x","label":"X","order":1}],
           "types":[{"id":"t","label":"T","family":"x","state_model":"workflow",
                     "sources":[],"presentation":{"chip_tokens":{}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("state_model" in e for e in errs)

def test_rejects_phases_on_non_pipeline():
    reg = {"families":[{"id":"x","label":"X","order":1}],
           "types":[{"id":"t","label":"T","family":"x","state_model":"cycle",
                     "phases":[{"id":"p","label":"P"}],
                     "sources":[],"presentation":{"chip_tokens":{}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("phases" in e for e in errs)

def test_rejects_unknown_family():
    reg = {"families":[{"id":"x","label":"X","order":1}],
           "types":[{"id":"t","label":"T","family":"nope","state_model":"cycle",
                     "sources":[],"presentation":{"chip_tokens":{}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("family" in e for e in errs)

def test_rejects_non_token_presentation():
    reg = {"families":[{"id":"x","label":"X","order":1}],
           "types":[{"id":"t","label":"T","family":"x","state_model":"cycle","sources":[],
                     "presentation":{"chip_tokens":{"a":"#ff0000"}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("token" in e for e in errs)

def test_rejects_source_without_mode():
    reg = {"families":[{"id":"x","label":"X","order":1}],
           "types":[{"id":"t","label":"T","family":"x","state_model":"cycle",
                     "sources":[{"kind":"transcripts"}],"presentation":{"chip_tokens":{}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("mode" in e for e in errs)
```

**Step 3: Run to verify fail.** `python3 -m pytest tests/test_program_schema.py -v` → FAIL.

**Step 4: Implement `scripts/program_schema.py`** (sibling of card_schema). `STATE_MODELS = {"pipeline","cycle","target","register"}`. Load theme tokens from `ui/task-board/themes/_TEMPLATE.css` (reuse card_schema's regex `(--[a-zA-Z0-9-]+)\s*:`). `validate_doc(reg, tokens)`: per type — state_model in set; `phases` key present only when pipeline; `family` resolves to a `families[].id`; every `presentation.chip_tokens` value starts with `--` and is in `tokens`; every `source` has a `mode`. `validate()` loads `cadence/programtypes/registry.json` + tokens, returns errors. `__main__`: print errors+exit 1, else `print("programtypes OK")`.

**Step 5: Run to verify pass.** `python3 -m pytest tests/test_program_schema.py -v` → PASS. Then `python3 scripts/program_schema.py` → `programtypes OK`.

**Step 6: Extend the denylist gate.** In `tests/test_engine_no_jay.py`, add to the `TARGETS` tuple (after the adapters glob):
```python
    glob.glob(os.path.join(ROOT, "cadence", "**", "*.json"), recursive=True)
```
Run `python3 -m pytest tests/test_engine_no_jay.py -v` → PASS (registry is clean).

**Step 7: Wire `program_schema.py` into the gate runners.** Grep for where `card_schema.py` is invoked (`grep -rn "card_schema" --include='*.sh' --include='*.py' --include='Makefile' --include='*.yml' .`) and add a `program_schema.py` invocation alongside each. Record the new gate in `docs/reference/invariants.md` (gate #2 row or a new #5 row) and `docs/reference/conventions.md` §2.

**Step 8: Run all five gates** (all exist now) → all green. Commit:
```bash
git add cadence/programtypes/registry.json scripts/program_schema.py tests/test_program_schema.py tests/test_engine_no_jay.py docs/reference/invariants.md docs/reference/conventions.md
git commit -m "feat(cadence): program-type registry + program_schema gate (#5) + denylist extends to cadence/**

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `render_view` mapping + registry loader in `program_lib`

The mapping from canonical program file → the prototype's render contract. **Read the prototype's `rowVM()` (lines 441-508) and `buildSeries()` (lines 428-439) — they are the exact spec for the math.** The mapping lives in `program_lib` (not the server) so it is unit-testable.

**Files:**
- Modify: `scripts/program_lib.py` (add `load_registry`, `render_view`, `build_cadence_payload`)
- Modify: `tests/test_program_lib.py` (add render tests)

**Step 1: Write failing tests.** Cover one assertion per model:
- `pipeline`: given `phase: "execution"` and the roadmap type's phases, `render_view` returns `model="pipeline"`, `current==2`, `phases` list of `{l,e,w}`, and `drift`-derived `tone` reachable.
- `target`: given `actual:58,target:55,unit:"%"` and `series`, returns `metricDelta=="+3pt"`, and `series` with `predPts/actPts/band/lastX/lastY/stroke` (assert non-empty strings; assert stroke maps `holding→--success`-ish, i.e. matches the prototype's `buildSeries` stroke rule).
- `cycle`: returns `statusLine` and `periods` with per-cell status.
- `register`: returns `items` with `age` and an `agePastPolicy` boolean (or the raw `age`+`policy` so the client colors — match the prototype, which colors client-side; simplest is to pass `age`,`policy` through and let JS color, OR precompute a tone. **Decision: pass raw `age` + `policy` through; JS colors** — keeps render_view free of CSS).
- `drift→health`/tone present for all; `activity` derived from the program body's `## Observations` entries (date + claim + tag), most-recent-first.
- `build_cadence_payload(root)` groups `render_view` outputs by family using the registry `families` order, dropping empty families, returning `{"families":[{"id","label","programs":[...]}]}`.

```python
def test_render_pipeline_current_index(tmp_path):
    root = str(tmp_path); _seed_registry(root)  # helper: copy real registry into tmp root, or load real one
    pid, _ = pl.create_program(type="roadmap-initiative", title="P", owner_role="product", root=root,
        frontmatter_extra={"phase":"execution","drift":"holding"})
    reg = pl.load_registry()
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "pipeline"
    assert vm["current"] == 2

def test_render_target_delta(tmp_path):
    # actual 58 target 55 -> "+3pt"; verify buildSeries keys exist
    ...

def test_build_payload_groups_by_family_and_drops_empty(tmp_path):
    ...
```
(Note: `render_view` needs the registry to know each type's phases/family/model. For `target`/`cycle`/`register` the metric/series/periods/items live in frontmatter on the instance — author them there in the seed and in these tests.)

**Step 2: Run to verify fail.** → FAIL.

**Step 3: Implement.** `load_registry()` reads `cadence/programtypes/registry.json`. `render_view(program, registry)`: look up the type, switch on `state_model`, port `rowVM`/`buildSeries` math to Python (return data only — no inline CSS; JS owns tone/color from `drift`, `age`, `status`). Derive `activity` from `## Observations` (parse the `### DATE — sentinel:... [kind]` headers + the `claim:` line). `build_cadence_payload(root)`: `list_programs(status="active")` → `render_view` each → group by `registry["families"]` order, drop empties.

**Step 4: Run to verify pass.** → PASS.

**Step 5: Five gates green.** Commit:
```bash
git add scripts/program_lib.py tests/test_program_lib.py
git commit -m "feat(cadence): render_view mapping (file -> render contract) + family payload builder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Seed program instances

Author ~13 realistic instances (the prototype's `programs` array is the canonical voice/data — translate each into a canonical program file). Cover all four families and all four models.

**Files:**
- Create: `datasets/programs/PROG-0001.md` … `PROG-0013.md`
- Create: `datasets/programs/_counter` (contents: `13`)
- Modify: `tests/test_program_lib.py` (add a guard test)

**Step 1: Write a guard test** that every seed program parses and renders without error:
```python
def test_all_seed_programs_render():
    reg = pl.load_registry()
    progs = pl.list_programs()  # real datasets root
    assert len(progs) >= 4
    for p in progs:
        vm = pl.render_view(p, reg)
        assert vm["model"] in {"pipeline","target","cycle","register"}
```

**Step 2: Run to verify fail** (no seeds yet, or <4). → FAIL.

**Step 3: Author the seed files.** Each file: frontmatter per design §3 + `## Intent` + a few `## Observations` (for the Activity feed) + a `## Cycles` entry. For `target` instances include `metric: {actual,target,unit}` + `series: {pred:[...],act:[...]}` in frontmatter; for `cycle` include `periods: [{w,s}]` and `status_line`; for `register` include `items: [{name,owner,age}]` and `policy`. Map the prototype's 13 programs (r1-r4, w1-w2, o1-o3, e1-e4) onto the seed types. **Set `_counter` to the highest number used.**

**Step 4: Run to verify pass.** → PASS. Also `python3 scripts/program_schema.py` still `programtypes OK` (instances aren't scanned by the schema gate, only the registry).

**Step 5: Five gates green** (instances are in `datasets/`, so they are NOT denylist-scanned — personal content may name people; that's fine). Commit:
```bash
git add datasets/programs/
git commit -m "feat(cadence): seed ~13 example program instances across all four models

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `GET /api/cadence` endpoint

Read `scripts/task_server.py` `_route_request` (line ~2365) and `_json_response` (line ~98). Add a read-only GET. The handler is thin — it calls `program_lib.build_cadence_payload()`.

**Files:**
- Modify: `scripts/task_server.py` (`_route_request` + a `handle_list_cadence` function)

**Step 1: Add the route** in `_route_request`, mirroring the `/api/cron` GET branch:
```python
if path == "/api/cadence" and method == "GET":
    handle_list_cadence(self)
    return True
```
And the handler (near the other `handle_*` functions):
```python
def handle_list_cadence(handler):
    import program_lib
    payload = program_lib.build_cadence_payload()
    _json_response(handler, payload)
```
(Match the module's existing import style — task_server imports sibling libs directly.)

**Step 2: Verify manually.** Start the server on the dev port and curl:
```bash
python3 scripts/task_server.py &   # binds dev port 8743 per profile
sleep 2
curl -s http://localhost:8743/api/cadence | python3 -m json.tool | head -40
```
Expected: `{"families":[{"id":"roadmap","label":"Roadmap","programs":[...]}, ...]}` with non-empty families. Kill the server after.

**Step 3: Five gates green.** Commit:
```bash
git add scripts/task_server.py
git commit -m "feat(cadence): GET /api/cadence read endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: UI — tab markup + `js/cadence.js` + CSS + wiring

Follow the designer README's "How to build it" steps exactly. **Lift the visual structure + the `rowVM`/`buildSeries` render logic from `Cadence.dc.html`** into a vanilla `renderCadence()`. Mirror `js/schedules.js` (fetch → build HTML string → `innerHTML`; module-level state). Drop the prototype's mock chrome/toasts and the `cadence-mood` key (use the app's existing chrome + `pmos-mood`).

**Files:**
- Modify: `ui/task-board/index.html` (nav button, tab panel, CSS section, script tag)
- Create: `ui/task-board/js/cadence.js`
- Modify: `ui/task-board/js/app.js` (`switchTab` dispatch + deep-link `known` array)

**Step 1: `index.html` — nav button** in `.topbar-tabs`, between Now and Schedules:
```html
<button class="topbar-tab" data-tab="cadence" onclick="switchTab('cadence')">Cadence</button>
```
**Step 2: `index.html` — tab panel**, alongside the other `.tab-content` blocks:
```html
<div id="tab-cadence" class="tab-content">
  <div class="cadence-view" id="cadence-view"><div class="loading">Loading…</div></div>
</div>
```
**Step 3: `index.html` — script tag** before `app.js`: `<script src="/js/cadence.js"></script>`.

**Step 4: `index.html` — CSS.** Add a `/* ─── Cadence ─── */` section. Lift the prototype's inline styles into classes (`.cadence-*`), **token-only** — every prototype rule already uses tokens (`--text`,`--accent`,`--warning`,`--danger`,`--success`,`--surface`,`--border-soft`,`--r-lane`,`--ease`, etc.). Include the 3-column grid row, the phase stepper, the metric readout, the cycle week-cells, the register items, the expand panel, and the `@media (max-width:640px)` single-column collapse.

**Step 5: `js/cadence.js`.** Module-level `let cadenceData = null;` and `const cadenceExpanded = new Set();`.
- `fetchCadence()` — `fetch(`${API}/cadence`)`, store `cadenceData`, call `renderCadence()`; on error show the `.loading`/danger message like `fetchCronJobs`.
- `renderCadence()` — build the page header + a `<section>` per family + a lane + a row per program. Per row, branch on `program.model` for column 2 (port `rowVM`: pipeline stepper with reach %, target metric+delta, cycle/register status line) and the expand panel (port the `## Observations`→Activity, checkpoints, history-by-model incl. the SVG chart from `buildSeries`, bindings, footer). Compute tone from `drift` (`holding→--text-muted`, `drifting→--warning`, `broken→--danger`, and handle `blind→--text-dim`). Use `escapeHtml()`/`formatDate()` (core.js) for server text and `svgIcon()` (icons.js) for the chevron if an equivalent exists (else inline the prototype's chevron SVG).
- Row click toggles membership in `cadenceExpanded` and re-renders (or toggles a class).

**Step 6: `js/app.js`** — in `switchTab(tabName)` add `if (tabName === 'cadence') fetchCadence();`, and add `'cadence'` to the deep-link `known` array.

**Step 7: No unit test** (vanilla JS, no harness) — verified live in Task 7. Five gates green (JS isn't covered by pytest but run the suite to confirm nothing else broke). Commit:
```bash
git add ui/task-board/index.html ui/task-board/js/cadence.js ui/task-board/js/app.js
git commit -m "feat(cadence): read-only Cadence tab (4 row models + expand), token-only

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Five gates green + live e2e across Moods

**Step 1: Run all five gates** — all green.

**Step 2: Live e2e on the DEV board (`:8743`, never `:8742`).** Start `task_server.py`, open `http://localhost:8743/#cadence`. Confirm:
- All four families render with their seed programs; empty families don't appear.
- Each row model renders correctly (pipeline stepper with the current phase highlighted in the drift tone; target metric + delta + the predicted-vs-actual SVG chart; cycle status line + week-cells; register status line + aging items).
- Clicking a row expands it (chevron rotates); intent, history-by-model, checkpoints, activity, bindings, footer all render. Multiple rows open at once.
- Deep link `#cadence` selects the tab on load.

**Step 3: Mood pass.** Switch Moods via the top bar (use the `visual-pass-technique` memory — Chrome headless screenshots). Confirm the view is fully theme-aware (no hardcoded colors leak) across Organic, Vantaca, Sugar Magnolia, and at least one of modafinil/breathe/karesansui. **Verify the real input path** (dispatch real clicks, don't call handlers directly — see the `verify-real-input-path` memory).

**Step 4:** If all clean, the build is ready to ship — hand back to the loop (`superpowers:finishing-a-development-branch`) for merge per the kickoff merge authority (merge to local `main`, not pushed, unless told otherwise). At ship, save the **deferred full-schema-gate circle-back** (design §5) to agent memory so a future session doesn't assume `program_schema.py` is complete.

---

## Notes for the executor
- `root=None` everywhere resolves to the real repo `datasets/`; tests pass `root=tmp_path`.
- Program **instances** are personal content (`datasets/`) and are NOT denylist-scanned; the **registry** (`cadence/**`) IS — keep it free of person/team literals.
- The prototype is a *design reference*; do not copy `support.js` or `mock-api.js` into the live UI.
- ASCII-safe runtime strings (hyphen, not em-dash) per invariant #8.
- Do not build emitters, sentinels, cron, or the reconciler — those are slices 3-11.
