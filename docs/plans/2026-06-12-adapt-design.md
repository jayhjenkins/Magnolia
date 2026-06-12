# Adapt — teach Magnolia a new capability (design)

**Date:** 2026-06-12
**Branch:** `feat/adapt-tab`
**Status:** Design approved; implementation plan to follow.
**Source of UI:** designer handoff `Magnolia - Adapt.zip` → `design_handoff_adapt/` (hifi prototype + real Mood token CSS). The handoff's `support.js` is reference-only; we recreate the design in the real Task Board front end.

---

## 1. What Adapt is

A new top-level **Adapt** tab in the Task Board, sitting `Now · Schedules · **Adapt** · Quality · Activity` (between Schedules and Quality). It lets a user describe, in plain language, something they wish Magnolia could do, and watch a headless Claude Code session running the `/magnolia-build` workflow turn that request into a real, persistent **adaptation** — an integration/adapter + worker + card type (any or all), built down-the-fairway against the engine's existing seams.

- **Left column:** a build chat. Each assistant turn is a 1:1 render of the model's real output stream (`thinking` / `tool_use` / `text` / `AskUserQuestion` / `ExitPlanMode`) — never bespoke structured data. A compact phase tracker (Brainstorm · Plan · Build · Ready) reflects the loop.
- **Right column:** a "Your adaptations" rail. Each built adaptation is one thin row: status dot, name, **pencil** (resume its build session), **trashcan** (delete via git revert), and a **live/off toggle**.

The whole feature is a **composition layer** over infrastructure that already exists (headless session machinery, the factory spine, `git revert`-based Undo, the Mood token system, the SPA tab pattern). The genuinely new work is isolated and named below.

### Two distinct git contexts (do not conflate)
- **This epic (us building Adapt):** branch `feat/adapt-tab` off main; gates green; **merge to main when green** (operator's chosen authority for this build). No PR.
- **Adapt's runtime (what an end user does in the tab):** builds **auto-commit to main** when green — no merge-authority prompt, no PRs. Each factory commit's SHA is recorded so toggle/delete/revert key off real commits.

---

## 2. Architecture decision: the store + how the toggle reaches the system (Approach A, approved)

A built adaptation is a **bundle** of artifacts that must turn on/off atomically across the three places artifacts get discovered. Chosen approach:

**Manifest record + membership tags + an enabled-set consulted at the seams.**

- **Store:** one record per adaptation at `datasets/adaptations/<id>.md` (YAML frontmatter + human notes body). Frontmatter:
  ```yaml
  id: <slug>
  name: "<display name>"            # provisional on creation, finalized as the build crystallizes
  claude_session_id: "<uuid>"       # the headless session, for --resume (pencil/edit)
  state: building | off | on        # the live/off toggle (building is transient)
  created: "YYYY-MM-DDTHH:MM:SSZ"
  manifest:                         # every artifact this adaptation owns
    - surface: worker | card-type | adapter
      ref: "<path or registry key>" # e.g. scripts/workers/stock_sentinel.md
      commit: "<sha>"               # the factory commit that created/edited it
  status: active | deleted          # tombstone on delete (append-only; invariant #6)
  ```
- **Membership tags** so the seams know which artifact belongs to which adaptation:
  - worker frontmatter gains an optional `adaptation: <id>` field;
  - card-registry entries gain an optional `"adaptation": "<id>"` key;
  - adapters record membership in the adaptation's manifest (adapters are provider-routed, not list-discovered).
- **`adaptations_lib.py`** is the single source of truth: `live_ids()`, `is_live(surface, ref)`, plus CRUD on the store. Returns "live" for untagged/legacy artifacts (back-compat: anything not owned by an adaptation is always live).
- **Three seams filter** on `is_live`:
  1. `task_dispatch.load_workers()` — skip a worker whose `adaptation` is set and not live.
  2. Card render (`js/card-registry.js` + any server-side card filtering) — a card type tagged to an off adaptation does not render/route.
  3. Adapter routing (`scripts/adapters/__init__.py` `get`/`publish`) — an adapter owned by an off adaptation is not routed.
- **Toggle** = flip `state` in the record + `PUT /api/adaptations/{id}/toggle`. No files move (rejected the staging-dir approach: file moves aren't atomic and symlinks break Windows portability, invariant #8).

**Toggle ⊥ Tier-2.** Turning an adaptation "live" does **not** bypass the one-time external-write confirm (invariant #5). For an adapter, the first external write still raises `NeedsConfirmation`. The toggle answers "is this wired into the system"; Tier-2 answers "has the user consented to external blast radius." Orthogonal.

Rejected alternatives: **B** physical staging dirs (non-atomic, Windows-hostile); **C** model adaptations as tasks/receipts (overloads task queues; adaptations aren't board tasks; the hard part — seam filtering — is identical either way, so we keep queues clean with a purpose-built store).

---

## 3. The gated build session

A new **`adapt_runner`** (sibling to `chat_runner`, reusing `build_chat_cmd` / `normalize` / spawn / SSE), un-bound from any task, driving `claude -p --resume`. Three guardrails:

1. **Curated allowlist** — permits the factory's real operations (Read/Grep/Glob/Edit/Write, the gate Bash commands `pytest` / `card_schema.py` / `portability_gate.py`, git for commit/revert, the `Agent`/Task tool for subagent dispatch, qmd search) and nothing else (no MCP external writes, no broad Bash).
2. **Path-scoped Write/Edit (the hard scope gate, approved)** — Write/Edit physically confined to the four factory surfaces: `scripts/adapters/`, `scripts/workers/`, the card registry (`ui/task-board/cardtypes/registry.json`), judge rubrics (`judge/rubrics/`), classifier routing. Plus the adaptations store. **Cannot** touch the top nav, `index.html` chrome, engine core, or `docs/reference/`. Enforced at the runner boundary, not just by prose.
3. **The scope-gate harness prompt (prose refusal)** — re-injected every turn via `--append-system-prompt` (see §4). Belt-and-suspenders: a jailbroken prompt still can't write outside the fairway.

**Endpoint:** `POST /api/adapt` (start/continue a turn) streaming SSE; un-bound from a task id. See §5 for the long-running connection model.

**Out-of-fairway behavior:** when the ask needs the top nav / engine core / anything outside the four surfaces, the session refuses in plain language and tells the user to run Claude Code natively — and the path-scoping makes the refusal real even if the model tries.

---

## 4. The build harness prompt — lean by subtraction, steering intact

The `/magnolia-build` slash command is **not sticky across resumed turns** (confirmed via the Claude Code guide), so the workflow framing is re-injected every turn through `--append-system-prompt`. Injecting **identical text every turn hits Anthropic's prompt cache**, so it's near-free after turn one and never drifts.

The harness is authored by **surgical subtraction from the actual `workflow-magnolia-build/SKILL.md`** — remove only environment ornamentation, keep the steering verbatim. The plan must show the diff for sign-off before it goes live.

- **Kept in full (the steering — load-bearing, avoids slop):** brainstorm → **meta-scope-extension** (emit the build contract) → writing-plans → **subagent-driven-development** with the two-stage review (spec-compliance, then code-quality) → e2e verify. All sub-skills (`meta-integration-discovery`, the `meta-create-*` factories, the superpowers loop). All iron laws (bind-to-the-seam, gates-green-before-commit, capture-to-profile, ASCII-safe runtime output — hyphen not em-dash). Subagent dispatch is explicitly preserved — a headless `claude -p` session can spawn subagents and run skills, so Adapt builds with the same discipline a native session does.
- **Removed (environment ornamentation only):**
  - **Step 0 preflight** entirely — the harness states *assume the environment is ready; never narrate systems checks* (no "checking superpowers/profile/git author/dev board…" output to burn tokens or clutter the chat).
  - **Step 2 merge-authority question** — replaced by *always auto-commit to main when green; never ask; never narrate git*.
  - **Step 6 PR / finishing-branch ceremony** — replaced by the factory's auto-commit-to-main + Keep/Undo.
- **Added (Adapt-context deltas):** the scope gate ("only adapters/workers/card-types via the meta-create-* factories; refuse skills/top-nav/core/net-new → tell the user to run Claude Code natively"), the path-confinement reminder, and "speak Keep/Undo, never commits/reverts." Note: the Adapt RUNTIME offers only the **three toggleable surfaces** (adapters, workers, card-types) - the surfaces with a live/off seam. Skills are dropped from Adapt (no liveness seam, no fairway write root); native magnolia-build still builds skills.

The native `workflow-magnolia-build` SKILL is **left untouched** (correct for native sessions); the harness is its Adapt-shaped sibling. Sync/traceability to the source SKILL is decided in the plan.

---

## 5. Long-running robustness (shared substrate) — the must-not-fail piece

Builds run up to an hour or more. The connection model must let a build **outlive the viewing connection**. Today `chat_runner` does the opposite: on early client close it kills the subprocess group (`chat_runner.py:470`), so walking away kills the work. The board is already `ThreadingHTTPServer (daemon_threads=True)`, so long streams don't block other requests — but there is no heartbeat and no detach.

**Build the survive-disconnect behavior once, as shared infra**, and put `adapt_runner` and `chat_runner` on it:

- **Detached supervised run** — the build subprocess is owned by a small supervisor keyed by adaptation id (resp. task id for chat), **not** by the HTTP request. A dropped/closed browser **does not kill the build**. (This is the key divergence from current `chat_runner` early-close behavior.)
- **Event-log tail** — every normalized event is appended to a persisted log (extending `chat_transcript`). The SSE endpoint **tails** the log: on connect/reconnect/pencil-resume it **replays** then **resumes tailing** until a terminal event.
- **Heartbeat** — SSE keep-alive comments (`: ping`) every ~15s during quiet stretches. Zero model-token cost (server→browser only).
- **No turn timeout** (or a generous ~2h ceiling) for a build run, vs the synchronous chat turn.

This gives "start a build, close the laptop, come back" for free, and fixes the existing task-chat walk-away bug at the same time.

**Cost model (confirmed):** `claude -p --resume` turns are one-shot invocations that exit on completion — **between turns there is no process and zero token cost** (the session is just a transcript on disk). A long turn costs more only because it does more work (more tool calls → more context per round-trip), proportional to building effort, not wall-clock. Heartbeats cost nothing.

**Auto-compaction is free.** Claude Code auto-compacts in `-p` mode as context fills; the session id is stable across compaction and `--resume` carries the summary forward. So compaction's ceiling is handled by the platform. Our **lightweight v1** adds: (a) inject an explicit `/compact` turn after each successful ship (keep the session lean for the next edit), and (b) a best-effort recommend-compact nudge *only if* the `result` event turns out to expose usage (CLI does not document this; verify empirically during build and degrade gracefully if absent — never block on it).

### Task-chat adoption (final, separable plan task)
The substrate is shared from day one. Adapt rides it fully in v1. The existing task chat adopts detached mode as the **last plan task, clearly separable** — if it surfaces risk in the working chat (subtle resume-state + module-caching; "restart :8743 after any `chat_runner` change"), ship Adapt and fast-follow the chat. The chat stays *interactive*; the substrate only means a turn's work outlives the connection.

---

## 6. Lifecycle & UI behavior

- **Tab & front end** — `index.html`: Adapt tab button between Schedules and Quality, a `tab-adapt` content div, a `switchTab('adapt')` case, and a new `js/adapt.js`. Recreate the handoff faithfully against the real Mood tokens (token-only — no hardcoded colors/radii; the design-system gate enforces this for card types). Reuse the SSE reader pattern from `js/chat.js`.
- **+ New adaptation** button — placed tastefully near the "Teach Magnolia something new" subline. Clears the chat to a clean slate **without a page refresh**; no session yet.
- **Row creation timing** — on **first session-id capture once intent is established** (per spec): the row appears toggled **off**, state `building`, holding the session id so the pencil always works; the name is provisional and finalized as the build crystallizes; state flips `building → off` when the build finishes. Output cards live in the **Now feed**, never rendered inside the Adapt chat.
- **Toggle** — `PUT /api/adaptations/{id}/toggle`, optimistic in the UI, persisted; flips `state` on↔off.
- **Edit (pencil)** — `--resume` the stored `claude_session_id`; post the "Resumed <name> session" divider; **one session per adaptation** (no multi-session; simplicity by design).
- **Delete (trashcan)** — warning modal ("Are you sure? You can just toggle it off instead.") → `git revert` the manifest's commits in reverse order (bundle Undo; history preserved per invariant #6) → tombstone the record (`status: deleted`). Reuses the receipt/`undo_receipt` revert mechanism, bundle-scoped.

---

## 7. Endpoints (server)

All on `task_server.py`, following the existing `_route_request` + SSE helper patterns:
- `POST /api/adapt` — start/continue a build turn (SSE, detached + tailable).
- `GET  /api/adapt/stream?adaptation=<id>` — reconnect/tail an in-progress run (replay + resume tailing).
- `GET  /api/adaptations` — list (feeds the rail).
- `PUT  /api/adaptations/{id}/toggle` — flip live/off.
- `POST /api/adaptations/{id}/delete` — warning-gated revert + tombstone.
- (Edit reuses `POST /api/adapt` with the stored session id for `--resume`.)

---

## 8. Gates & invariants

Every commit keeps the four gates green: `python3 -m pytest` (incl. `tests/test_engine_no_jay.py`), `python3 scripts/card_schema.py` (→ `registry.json OK`), `python3 scripts/portability_gate.py` (→ `portability OK`). New code (`adapt_runner`, the shared substrate, `adaptations_lib`, seam filters) stays **denylist-clean** (no hardcoded identity — read from `profile/`) and **portable** (OS/shell/encoding through `platform_lib`, never hand-rolled; ASCII-safe output). New card-type artifacts reference **theme tokens only**. Dev board only (`localhost:8743`); never touch prod or `~/pm-os`.

---

## 9. What's reused vs net-new (summary)

| Reused (do not rebuild) | Net-new (this epic) |
|---|---|
| `chat_runner` cmd/normalize/spawn/SSE; `chat_transcript` persistence | `adapt_runner`; the gated build session (allowlist + path-scope + harness) |
| Factory spine (`meta-scope-extension`, `meta-create-*`, `factory_lib.commit_and_emit_receipt`) | `adaptations_lib` + `datasets/adaptations/` store + membership tags |
| `git revert` Undo (`undo_receipt`) | live/off toggle + the three seam filters |
| Mood token system; SPA tab pattern; `ThreadingHTTPServer` | shared survive-disconnect substrate (detach + log-tail + heartbeat); task-chat adoption |
| `profile_lib`, `task_dispatch.load_workers`, card registry | Adapt tab + `js/adapt.js`; +New / pencil-resume / trashcan-delete UI; lightweight compaction (post-ship `/compact` + best-effort nudge) |
