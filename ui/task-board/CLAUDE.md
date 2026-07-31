# PM-OS Task Board UI — notes

This directory IS the task board front end: `index.html` + `js/*.js`. It's a
vanilla HTML/CSS/JS board served by `scripts/task_server.py` (route `/` →
`ui/task-board/index.html`, static `/js/*` and `/themes/*`) against the real
backend API at `/api/*`. Default dev URL: `http://localhost:8744`. (The
separate `~/pm-os` production install and its `:8742` port are retired and no
longer in use — this repo is the only install now; see invariant #7 in
[`docs/reference/invariants.md`](../../docs/reference/invariants.md).)

Design-system rules (token-only, card schema, the composition boundary, Moods):
[`docs/reference/design-system.md`](../../docs/reference/design-system.md).
Theme authoring steps: [`themes/README.md`](themes/README.md).

> Note: a standalone build of this UI ships with a `js/mock-api.js` that
> intercepts `fetch()` for `/api*` and `/open*` with seed data so the redesign
> runs without the server. That file is intentionally **not** part of this live
> UI — here the real `task_server.py` backend serves the data. Don't add it.

## Moods (swappable themes)

The board supports swappable color/type/shape themes called **Moods**, surfaced
via a "Mood" dropdown in the top bar (right of the date). The default and first
mood is **Organic** (the original forest/wood dusk palette).

When asked to add or edit themes, follow `themes/README.md`. In short: a mood is
a token-only stylesheet `themes/<id>.css` (scoped to `[data-theme="<id>"]`),
linked in `index.html`'s `<head>`, and registered in `js/themes.js`'s `MOODS`
array. Switching moods only swaps CSS tokens — never change interactions/UX when
adding a mood. Copy `themes/_TEMPLATE.css` to start.

- Primitives (surfaces, text, accents, q-/prio- hues, radii, ease, app-bg,
  paper) live in each `themes/<id>.css`.
- Derived tokens (`*-soft` tints, legacy `*-bg` aliases) are computed once in
  `index.html`'s `:root` from those primitives — a mood file never repeats them.
- Use absolute paths (`/themes/...`, `/js/...`) in `index.html` to match how the
  server serves static files.

## The Cadence tab

A top-level board tab (sibling to the task views) rendering the **Cadence**
subsystem — the standing-loop "second organ". It is **read-only**: nothing on it
performs an external action. The whole tab renders from the program-type registry
+ program frontmatter (theme tokens only, exactly like cards render from their
registry) — there is no per-type hardcoded UI.

- **Data:** `GET /api/cadence` → `program_lib.build_cadence_payload()`. The payload
  is families (presentation-only shelves) → `programs[]` rows; only non-empty
  families render.
- **Render:** `js/cadence.js`. Rows carry a state chip, a `holding/drifting/broken`
  drift badge, next checkpoint, a last-cycle one-liner, and a needs-you count;
  expansion shows the observation ledger, emission history, and a grounding block.
  Four row layouts, one per state model (pipeline/cycle/target/register).
- **Rules:** token-only and ASCII-safe like the rest of the UI; never add an action
  that writes externally here. The subsystem map is
  [`docs/reference/cadence.md`](../../docs/reference/cadence.md).
