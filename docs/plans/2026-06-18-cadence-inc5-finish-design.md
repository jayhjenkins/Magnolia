# Cadence Increment 5 — the finish (slices 9 + 10 + 11)

> 2026-06-18. The approved design for the final Cadence increment. Closes the 11-slice epic.
> Builds on inc1-4 (substrate, reconcile engine, interpretation engine, weekly digest, lifecycle
> birth + death + janitor). Companion brief: `2026-06-12-cadence-design-brief.md` §9 (slices 9-11),
> §8 (dormant-type activation / starter sets), §10 (deferred rollover cadence).

**The cut (operator's call): build ALL THREE SLICES in one increment.** Not split. Each slice is
independently green (gates pass after each), but they ship together as "the finish."

**Goal:** Give Cadence (a) Office-native deliverables it can attach to its sends, (b) a working EOS
family with a live read-only sheet source, and (c) the ability to extend itself (a program-type
factory) and speak at the portfolio level (a cross-program rollup) — closing the epic.

**Tier discipline:** Tier-1 throughout EXCEPT where slices 9 and 11 ride the EXISTING Tier-2 send
path (no new external-write surface is created — attachments and the rollup reuse the messaging
adapter that is already Tier-2-gated). The EOS sheet read is a READ (never a write); manual-on-purpose
sources stay `mode: read` forever (brief philosophy + invariant intent).

---

## Slice 9 — Attachments (reuse `doc_sync` + extend the Graph send path)

**Mission:** an emitter's `produce-artifact` output can be delivered as an Office-native attachment
on the send, not just an inline paste. Reuses the two Microsoft seams that already exist; creates no
new external surface.

**The seams we reuse (not rebuild):**
- `scripts/doc_sync.py` — markdown -> `.docx` via pandoc, already lands the docx in the
  OneDrive/SharePoint-synced folder and can compute its SharePoint URL (`_build_sharepoint_url`).
- `scripts/send_message_graph.py` / mgc — Outlook email + Teams chat send (today: body only).
- `scripts/adapters/messaging/__init__.py` + `m365.py` — the Tier-2-gated `publish(draft, root)` seam.

**What we build:**
1. **`attachments: [paths]` on task frontmatter** — a list of local artifact paths (markdown or other)
   the send should carry. Threaded through the send-message card -> `publish(draft)` -> the Graph send.
   `task_lib`/card schema additions; no new card type (reuse `send-message`).
2. **Email delivery — true base64 attachment.** Extend `build_email_payload` to accept
   `attachments` and emit Graph `fileAttachment` entries (base64 `contentBytes`, resolved `name` +
   `contentType`). A markdown artifact is rendered to `.docx` via `doc_sync` first (Office-native),
   then attached; a non-md path is attached as-is.
3. **Teams delivery — file-*reference* attachment.** Graph chat messages have NO base64 path. So:
   render the artifact to `.docx` via `doc_sync` (lands in the OneDrive-synced folder), compute its
   SharePoint URL via `doc_sync._build_sharepoint_url`, and post the chat message with a `reference`
   attachment (`contentType: reference`, `contentUrl: <sharepoint-url>`, `name`). Extend
   `build_chat_message_payload` + `send_teams` accordingly.
4. **Graceful degradation (the iron rule for this slice).** If pandoc is missing, OR the path can't be
   rendered, OR the SharePoint URL can't be resolved (Teams, no OneDrive/SharePoint config), OR the
   provider can't attach — DEGRADE to an **inline link** appended to the message body (the artifact
   path or its resolvable URL). Never fail a send because an attachment couldn't be built; never drop
   the artifact silently.
5. **`produce-artifact` threading.** When a digest worker (priority-digest, and slice 11's
   portfolio-rollup) drafts its send-message card, it sets `attachments: [<artifact-path>]` so the
   versioned artifact rides along. The md->docx conversion and delivery choice live in the send path
   (the adapter), not the worker — the worker just names the artifact.

**Surface:** adapter (extend `messaging` provider `m365` + `send_message_graph`) + platform/UI
(task frontmatter field) + reconciler emitter (set `attachments` on the produce-artifact send).
**Rides existing Tier-2.** **Gate:** `pytest` + `card_schema.py` + `portability_gate.py`.

---

## Slice 10 — EOS family + read-only sheet source + starter set

**Mission:** make the EOS loop real. The EOS types exist in the registry (`eos-rock`, `eos-cycle`,
`eos-issues`) but nothing reads their `eos_sheet` source and there is no L10-prep nudge loop and no
starter set. Finish all of it. The EOS sheet is read-only forever (manual-on-purpose).

**What we build:**
1. **`sheet-watch` sentinel** (`scripts/sentinels/sheet-watch.md`) — a read-only `claude -p` sentinel
   (mirrors `movement-watch`). **Reads the live EOS sheet via the M365 MCP** (`read_resource` /
   `sharepoint_search`) each cycle — the operator's chosen mechanism. The sheet location is
   **profile-configured** (`profile/integrations.yaml`, e.g. an `eos.sheet` block with the
   site/resource locator — NO literal in the engine, invariant #1/#4; read via `profile_lib`).
   `allowed_tools` includes the M365 MCP read tools + `Read`. Emits observation kinds appropriate to
   EOS (status-signal, completion, commitment, risk, metric). **Never writes.**
   - **Graceful degradation:** when the M365 MCP is absent/unauthed at headless dispatch, or the sheet
     locator is unconfigured, the sentinel records a SKIP/blind entry in the existing
     `sentinel-runs.json` telemetry (the dead-vs-blind pattern from inc4b) and emits nothing — never
     fabricates. The reconciler treats a blind sheet-watch like any blind sentinel (no false drift).
2. **`eos-l10-prep` cycle type** — a new `cycle` program type for L10 meeting prep with **pre-L10
   nudge emitters** (countdown to the L10) that **reuse inc3b's nudge fence**: the
   `max_nudges_per_person_per_week` field (already schema-validated) + the `nudge_counts` per-period
   counter in `reconcile.py`. The nudge is a rate-capped `draft-message` (the existing emitter), never
   a new external path.
3. **Finish the eos family** — wire `sheet-watch` onto `eos-rock`/`eos-cycle`/`eos-issues`/`eos-l10-prep`
   sentinel lists; add the appropriate emitters (escalate already present; add the L10 nudge on
   `eos-l10-prep`). Keep all four `mode: read` on `eos_sheet`.
4. **`cadence/starter-sets.yaml` (first entry)** — the EOS bundle the onboarding concierge offers
   ("you run EOS — seed L10-prep, rocks, scorecard, issues?"). Consumed once at setup, never at
   runtime (brief §8). A small loader + a test that the referenced type ids all exist in the registry.

**Surface:** sentinel (new `sheet-watch`) + registry (new `eos-l10-prep` type + emitter/sentinel
wiring) + reconciler (nudge emitter reuse — no new code path) + profile (`eos.sheet` config) + a new
`starter-sets.yaml` + loader. **Tier-1** (a read + local cards). **Gate:** `pytest` +
`program_schema.py` + `test_engine_no_jay.py` (the new sentinel/registry must be denylist-clean).

---

## Slice 11 — `meta-create-program-type` factory + portfolio rollup card (the capstone)

**Mission:** make the system self-extending (a factory to author new program types the right way) and
give it a portfolio-level voice (a weekly cross-program digest). Sequenced LAST — the rollup is only
meaningful once >=2 families run (after slice 10 the operator can have roadmap + weekly + eos).

**What we build:**
1. **`meta-create-program-type`** (`.claude/skills/meta-create-program-type/SKILL.md`) — the 4th
   `meta-create-*` sibling under `meta-factory-core`. Follows the lifecycle exactly:
   scaffold a clean registry entry (closed `state_model`, declared `family`, every source with a
   `mode`, emitters from the closed action set, presentation chips = theme tokens only) ->
   **capture team/person nuance to profile** (family display label, source bindings/locators) NEVER
   into the registry entry -> run `scripts/program_schema.py` (GREEN before commit) ->
   `factory_lib.py commit-and-receipt --kind program-type` -> Keep/Undo receipt. **Tier-1**
   (authoring-time, local files). Add a `--prd`/conversational capture path mirroring the siblings.
2. **`portfolio-rollup` worker** (`scripts/workers/portfolio-rollup.md`) — a judged worker mirroring
   `priority-digest`: reads ALL active programs across families (drift, last cycle, next checkpoint,
   open needs-you), writes a **versioned cross-program digest artifact** (via
   `program_lib.py write-artifact`, invariant #6), and drafts a `send-message` card carrying it
   (with slice 9's `attachments: [<artifact>]`). **Shadow tier** by default on the ladder; judge-spawned
   like every worker. Names cross-program themes ("these two drifts share a root cause") at the
   portfolio level — the place the brief says that awareness belongs (§10), keeping the per-program
   reconciler single-program and dumb.
3. **The rollup trigger + the >=2-families gate.** A seeded `portfolio-rollup` program (or a
   reconciler/scheduler hook) dispatches the worker weekly **only when >=2 families have active
   programs** (counted from the program store — never off a persona/family literal). Below the
   threshold the rollup is inert (no empty digest). Reuses the `produce-artifact` -> worker-dispatch
   door built in inc3b.

**Surface:** skill (new `meta-create-program-type`) + worker (new `portfolio-rollup`) + card (reuse
`send-message` + the existing recommendation/receipt cards) + reconciler/scheduler (the >=2-families
dispatch gate). **Tier-1** except the rollup's send rides the **existing Tier-2** path. **Gate:**
`pytest` + `program_schema.py` + `card_schema.py` + `test_engine_no_jay.py` + `validate-worker`.

---

## Build contract (per surface — what each subagent is bound to)

| Surface | Decision | Seam / factory | Proving gate |
|---|---|---|---|
| adapter: messaging `m365` + `send_message_graph` | EXTEND (attachments: email base64 + teams reference) | `scripts/adapters/messaging/`, `send_message_graph.py`, reuse `doc_sync` for docx+URL | `pytest` + `portability_gate.py` |
| platform: task frontmatter `attachments` | EXTEND | `task_lib.py` / card schema | `card_schema.py` + `pytest` |
| sentinel: `sheet-watch` | BUILD-NEW (read-only, M365 MCP) | `scripts/sentinels/sheet-watch.md`, `sentinel_runner` telemetry (reuse) | `pytest` + `test_engine_no_jay.py` |
| registry: `eos-l10-prep` + eos wiring + nudge emitter | EXTEND | `cadence/programtypes/registry.json`, reuse `reconcile.py` nudge fence | `program_schema.py` + `pytest` |
| starter set: `cadence/starter-sets.yaml` | BUILD-NEW (data + loader) | new file + loader | `pytest` |
| skill: `meta-create-program-type` | BUILD-NEW (4th factory sibling) | `meta-factory-core` lifecycle + `factory_lib.py` | `program_schema.py` + `pytest` |
| worker: `portfolio-rollup` | BUILD-NEW (mirror `priority-digest`) | `scripts/workers/portfolio-rollup.md`, judge/ladder (shadow) | `validate-worker` + `pytest` |
| card: rollup send + receipts | REUSE (`send-message`, recommendation, receipt) | `registry.json` compose | `card_schema.py` |
| reconciler/scheduler: rollup >=2-families dispatch | EXTEND | `reconcile.py` produce-artifact door (reuse) | `pytest` |

**Standing contract item (every surface):** runtime output is **ASCII-safe** — hyphen not em-dash,
straight quotes. Profile-driven identity (invariant #1): no person/team/channel/site literal in any
artifact; read via `profile_lib`. Append-only / version-suffixed artifacts (invariant #6).

---

## Live e2e plan (on :8743, prod :8742 untouched)

1. **Attachments (9):** a `produce-artifact` digest -> send-message card with `attachments:[<md>]` ->
   exercise the publish path in dry-run/configured mode: email payload carries a base64 docx
   (md->docx via doc_sync); teams payload carries a reference attachment OR degrades to an inline link
   when no SharePoint config. Verify graceful degradation when pandoc/URL absent. No real external
   send unless mgc is configured (provider currently `none` -> draft/dry-run).
2. **EOS (10):** seed an `eos-l10-prep` program; run the scheduler reconcile; with the M365 MCP
   absent, sheet-watch records a blind/skip telemetry entry and emits nothing (no false drift); the
   L10 nudge emitter fires within the rate cap and is suppressed past it. starter-sets.yaml loads and
   all referenced type ids resolve.
3. **Factory + rollup (11):** run `meta-create-program-type` to author a throwaway type -> gate green
   -> receipt card (Keep/Undo); delete the throwaway after. Seed >=2 families' active programs ->
   reconcile dispatches the `portfolio-rollup` worker -> it produces a versioned cross-program digest
   artifact + a send-message card (shadow tier, draft-only). Below the threshold (1 family) the rollup
   stays inert. HEAD unchanged on every accept (Tier-1 where applicable).

## Out of scope / deferred (explicit)

- Live external sends (provider is `none`; verified via dry-run/draft-only).
- A standalone read-only **sheet adapter** module / xlsx parsing — v1 reads live via the M365 MCP.
- **Rollover/renewal cadence** at period boundaries (quarterly rock turnover, roadmap re-seeding) —
  brief §10, deferred past the epic close.
- The sentinel **dispatch-failure-vs-empty** telemetry nuance (still recorded coarsely as blind/skip).
- Single-file vs directory-per-program migration (brief §10 open question) — not triggered yet.
</content>
</invoke>
