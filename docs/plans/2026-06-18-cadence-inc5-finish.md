# Cadence Increment 5 — Implementation Plan (slices 9 + 10 + 11)

> **For Claude:** Execute task-by-task with TDD. Gates green after every slice:
> `python3 -m pytest -q` · `python3 scripts/card_schema.py` · `python3 -m pytest tests/test_engine_no_jay.py`
> · `python3 scripts/portability_gate.py` · `python3 scripts/program_schema.py`.
> Commit per logical unit. Design: `2026-06-18-cadence-inc5-finish-design.md`.

**Goal:** Close the Cadence epic — attachments on sends, a working EOS family with a live read-only
sheet source, and self-extension (factory) + a portfolio rollup voice.

**Architecture:** Reuse existing seams. doc_sync (md->docx + SharePoint URL), send_message_graph/mgc
(delivery), M365 MCP (sheet read), priority-digest (rollup pattern), meta-factory-core (factory),
inc3b nudge fence (EOS nudges). Tier-1 except the existing Tier-2 send path.

---

## SLICE 9 — Attachments

### Task 9.1: `attachments` threads through the send draft
- **Files:** `scripts/shipper.py:32` (`_message_draft_from_task`), `tests/test_shipper.py`
- Test: a send-message task whose fm has `attachments: ["a/b.md"]` -> draft carries `attachments: ["a/b.md"]`; a task without -> `attachments: []`.
- Impl: read `fm.get("attachments") or []` into the draft dict.

### Task 9.2: email base64 fileAttachment in the Graph payload
- **Files:** `scripts/send_message_graph.py` (`build_email_payload` + `send_email`), `tests/test_send_message_graph.py`
- Test: `build_email_payload(to, subj, body, attachments=[path])` includes a `message.attachments[0]` with `@odata.type == "#microsoft.graph.fileAttachment"`, base64 `contentBytes`, resolved `name` + `contentType`. Empty/no attachments -> no `attachments` key (back-compat).
- Impl: helper `_file_attachment(path)` -> reads bytes, base64-encodes, guesses contentType from suffix (.docx -> the Word MIME, else octet-stream). Markdown is rendered to .docx via doc_sync FIRST (Task 9.4) — here just attach whatever path is given.

### Task 9.3: Teams reference attachment (+ graceful inline-link)
- **Files:** `scripts/send_message_graph.py` (`build_chat_message_payload` + `send_teams`), `tests/test_send_message_graph.py`
- Test: `build_chat_message_payload(body, attachments=[{"name","url"}])` adds an `attachments` list of `contentType: reference` entries with `contentUrl`, and references them in the HTML body; with no resolvable url the entry is omitted and a link is appended to the body instead.
- Impl: Teams has no base64 path -> reference by URL only.

### Task 9.4: md->docx + URL resolution in the m365 adapter (the degrade ladder)
- **Files:** `scripts/adapters/messaging/m365.py`, `tests/test_messaging_m365.py`
- Test: `publish(draft_with_md_attachment)` (mocked graph + doc_sync): a markdown attachment is converted to .docx via doc_sync and attached (email) / referenced by SharePoint URL (teams); when pandoc/doc_sync raises or URL can't resolve, the body gets an inline link and the send still proceeds (no raise).
- Impl: a `_resolve_attachments(draft, channel)` helper: for each path, try md->docx (doc_sync) for email base64 OR docx+SharePoint-URL for teams; on ANY failure, collect an inline link for the body. Thread resolved attachments + augmented body into send_email/send_teams.

### Task 9.5: produce-artifact sets `attachments` on its send card
- **Files:** `scripts/cadence/reconcile.py` (produce-artifact / draft-message emitter), `scripts/workers/priority-digest.md`, tests
- Test: a produce-artifact emitter that drafts a send-message card sets `attachments: [<artifact-path>]` on the card fm.
- Impl: thread the artifact path into the send-message card creation. Worker doc updated to set `--attachments`.

### Task 9.6: `--attachments` CLI flag + card schema
- **Files:** `scripts/task_lib.py` (add_task message fields), `scripts/task.sh` (if it enumerates flags), `ui/task-board/cardtypes/registry.json` (send-message card if attachments render), tests
- Test: `task.sh add ... --attachments "a.md,b.md"` stores `attachments: ["a.md","b.md"]`; card_schema stays green.
- Slice 9 gates: pytest + card_schema + portability. COMMIT.

---

## SLICE 10 — EOS family + read-only sheet + starter set

### Task 10.1: `sheet-watch` sentinel definition
- **Files:** `scripts/sentinels/sheet-watch.md` (new), `tests/test_sentinel_runner.py` or `tests/test_sentinels.py`
- Test: the sentinel def parses (frontmatter: `kind: sentinel`, `sources: [{kind: eos_sheet, mode: read}]`, `observation_kinds`, `allowed_tools` includes the M365 MCP read tools + Read, `scope: active-programs`); `test_engine_no_jay.py` stays green (no literal).
- Impl: mirror movement-watch.md; body instructs read-only live read via the M365 MCP from the profile-configured sheet locator, conservative attribution, cite source, ASCII-only.

### Task 10.2: profile EOS sheet locator (no literal)
- **Files:** `profile/integrations.yaml` (add an `eos:` block: `sheet:` locator placeholder, default empty), `scripts/profile_lib.py` (accessor), tests
- Test: `profile_lib.eos_sheet(root)` returns the configured locator or None; default profile -> None (unconfigured -> sentinel degrades).

### Task 10.3: sheet-watch dispatch + blind/skip telemetry
- **Files:** `scripts/sentinel_runner.py`, `tests/test_sentinel_runner.py`
- Test: dispatching sheet-watch with no M365 MCP / no locator records a blind-or-skip entry in `sentinel-runs.json` (reuse inc4b telemetry) and emits zero observations; the reconciler does not raise and computes no false drift.
- Impl: the runner already records success/error; ensure the unconfigured/MCP-absent path is recorded as a non-success (blind) so the janitor/silent-door logic treats it correctly.

### Task 10.4: `eos-l10-prep` cycle type + rate-capped nudge emitter
- **Files:** `cadence/programtypes/registry.json`, `scripts/program_schema.py` (only if a new trigger needed), `tests/test_program_schema.py`, `tests/test_cadence_reconcile.py`
- Test: registry has `eos-l10-prep` (cycle, family eos, source eos_sheet read, sentinels [sheet-watch], a pre-L10 `draft-message` nudge emitter with `max_nudges_per_person_per_week`); program_schema green; the nudge fires within the cap and is suppressed past it (reuse `nudge_counts`).
- Impl: prefer reusing an existing emitter trigger (e.g. checkpoint-overdue/cycle-fresh) for the pre-L10 nudge; only add a trigger to the closed set if none fits.

### Task 10.5: wire sheet-watch onto the existing eos types
- **Files:** `cadence/programtypes/registry.json`, tests
- Test: eos-rock/eos-cycle/eos-issues list `sheet-watch` in sentinels; program_schema green; all seed programs of these types still render.

### Task 10.6: `cadence/starter-sets.yaml` + loader
- **Files:** `cadence/starter-sets.yaml` (new — an `eos` bundle listing the eos type ids), `scripts/program_lib.py` or a small `starter_sets.py` loader, `tests/test_starter_sets.py`
- Test: the loader reads the yaml; every type id referenced in every bundle exists in the registry (the guard against a dangling starter set); the eos bundle lists the four eos types.
- Slice 10 gates: pytest + program_schema + test_engine_no_jay. COMMIT.

---

## SLICE 11 — Factory + portfolio rollup

### Task 11.1: `meta-create-program-type` skill
- **Files:** `.claude/skills/meta-create-program-type/SKILL.md` (new), `tests/test_engine_no_jay.py` (must stay green)
- Test: skill present + denylist-clean; references meta-factory-core + program_schema gate + factory_lib commit-and-receipt --kind program-type.
- Impl: mirror meta-create-worker/meta-create-card-type. Body: capture state_model/family/phases/sources/emitters conversationally, capture family-label + source locators to profile, scaffold the registry entry, run program_schema, commit-and-receipt, Keep/Undo.

### Task 11.2: factory_lib supports `--kind program-type`
- **Files:** `scripts/factory_lib.py`, `tests/test_factory_lib.py`
- Test: `commit-and-receipt --kind program-type <file>` stages only that file, commits, emits a receipt card with revert_commit + summary. (If `--kind` is an open string already, just add a test for program-type; if enumerated, extend the enum.)

### Task 11.3: `portfolio-rollup` worker
- **Files:** `scripts/workers/portfolio-rollup.md` (new), `tests/` (validate-worker)
- Test: worker def validates (match task_type portfolio-rollup, tier deep, allowed_tools, langfuse_prompt); denylist-clean.
- Impl: mirror priority-digest.md — read ALL active programs cross-family, write a versioned artifact via write-artifact, draft a send-message card with `attachments:[<artifact>]`, shadow-tier proposal language, name cross-program themes, ASCII-only, identity from profile.

### Task 11.4: the >=2-families dispatch gate
- **Files:** `scripts/cadence/reconcile.py` (or the seeded portfolio-rollup program + a produce-artifact emitter), `tests/test_cadence_reconcile.py`
- Test: with >=2 active families present in the store, the rollup dispatch fires (a produce-artifact -> portfolio-rollup task); with <2 families it stays inert (no task). Counts families from the program store, never a literal.
- Impl: a `_active_family_count(root)` helper; gate the rollup produce-artifact emitter on it.

### Task 11.5: seed the portfolio-rollup program (if program-driven)
- **Files:** `datasets/programs/PROG-00NN.md` (new seed, register or cycle), counter bump, `tests/test_program_lib.py` seed count
- Test: seed renders; seed count test updated.
- Slice 11 gates: ALL FIVE. COMMIT.

---

## FINAL: review + e2e + ship
- Dispatch an independent code-reviewer agent over the whole inc5 diff (main..HEAD).
- Fix Critical/Important findings.
- Live e2e on :8743 per the design doc's 3 scenarios (attachments degrade, EOS blind-skip + nudge cap, factory receipt + rollup >=2-families gate). prod :8742 untouched. Restore seeds after.
- `superpowers:finishing-a-development-branch` -> merge to local main (NOT pushed) per kickoff authority.
- Update memory (inc5 record + build-sequence epic COMPLETE).
</content>
