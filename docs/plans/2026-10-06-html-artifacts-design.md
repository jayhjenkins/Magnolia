# HTML artifacts in the task card - design

Status: draft for approval · 2026-10-06

## Problem

Markdown artifacts have a full loop: an agent writes the file, the task card points at it, the operator reviews and edits it inline (Milkdown) with chat alongside. HTML artifacts - roadmaps, pre-reads, infographics, dashboards, review decks - are already produced (37 files under `datasets/`, ~10 tasks with an `.html` `agent_output`), but the loop stops at the card: the server refuses non-`.md` output paths (`task_server.py:219` `_resolve_output_path`) and the board renders an inert "Output" tile (`tasks.js:91-96`, `board.js:120`). HTML should work exactly like markdown: the card points at the file, the operator views it, and changes it via chat or by hand.

## Decisions

### 1. Format selection - declared, default md

- A task carries `output_format: md | html` (absent = `md`).
- Set at creation: an explicit phrase in the ask ("as HTML", "as a page") sets `html`. No LLM guess at dispatch.
- A worker may declare a default `output_format` in its frontmatter; the task field wins.
- Rule of thumb written into the guidance: **md** when the content is edited as text, sent to Word/Jira, or is a PRD, memo, or strategy doc. **html** when it is looked at or presented - roadmap visuals, pre-reads, infographics, dashboards, slides, mockups.

### 2. Rendering - sandboxed iframe

- New route `GET /artifact/<TASK-ID>` serves the task's `.html` output, contained to `datasets/` (realpath check, same shape as `_resolve_output_path`).
- Response header `Content-Security-Policy: sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src https: data:; script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; connect-src 'none'`.
- The board embeds it as `<iframe sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox">` - no `allow-same-origin`, so artifact script cannot reach `/api/*`. `connect-src 'none'` matters because the server sends `Access-Control-Allow-Origin: *`.

### 3. Render surfaces

- **Task modal, left pane (review):** an HTML viewer overlay in the same takeover slot the markdown editor uses (`.dt-editor` in `#split-modal .task-pane`), chat stays on the right. Toolbar: Open full · Source · Copy path.
- **Full tab:** "Open full" opens `/artifact/<TASK-ID>` in a new browser tab - full width, printable.
- **Card face:** the output tile reads "HTML page" and opens the full tab; the detail-pane tile opens the in-pane viewer.
- **Design target:** full-width page that holds up at pane width (~640px).

### 4. Editing - in place, like markdown

- **Chat:** the agent edits the existing file with targeted edits; it never regenerates the whole file for a change. The viewer reloads on save.
- **Manual:** "Source" swaps the iframe for a plain source textarea (the markdown editor's existing fallback), saving through the existing `PUT /api/tasks/{id}/output`.
- **Versioning:** edits overwrite in place, identical to the markdown editor's autosave. A version suffix (`-v2`) applies only when a new run produces a new artifact (invariant #6 is about not deleting generated work, not about every edit).

### 5. Visual style - profile, not engine

- **Profile (operator taste):** a new voice channel `html` → `profile/voice/html.md`, edited in Profile as "Visual style" beside Teams/Email (`_VOICE_CHANNELS`, `profile_lib.voice_text`, `profile.js _pfVoice`). Seeded from the operator's existing house style (palette, layout, components, exemplar file paths) plus explicit bans.
- **Engine (technical contract, fixed):** a context skill `context-html-artifact` that every HTML-producing run reads: single self-contained file, inline CSS, external hosts limited to the CSP allowlist, no network calls, responsive to 640px, print stylesheet, title/date header, read `profile/voice/html.md` and follow it, edit in place on change requests. Injected by dispatch when `output_format: html`.
- Seed content for the operator's profile (not engine) - house style as found in the existing artifacts:
  - Palette `--blue #247ae4 · --ink #1a2230 · --muted #6b7686 · --line #e5eaf1 · --bg #eef4fb · --green #1a9e5c · --amber #d4880f · --red #d14040 · --lightblue #eaf2fe`; system font stack.
  - Layout: light-blue page, one white `.wrap` card (max 1060px, 16px radius, soft shadow), header with blue uppercase eyebrow + H1, sections divided by hairlines, blue uppercase section labels, stat-tile hero row, callouts, left-ruled quote blocks, ruled tables with tabular numerals.
  - Bans: no pill/chip/badge tags (status via colored text, a left rule, or a plain label); no subtitle under the H1 that restates it; no "context" lines under stats; no section-intro sentences explaining what the section shows; no helper text that over-explains; no emoji icons.

### 6. Search

qmd only indexes `*.md`, so every HTML artifact is invisible to `context-search`. Add an HTML-to-text pass in the nightly qmd update that writes plain text into a gitignored mirror (`datasets/.qmd-html/`) indexed as a new `html_artifacts` collection.

### 7. Judge

`judge.read_artifact` truncates at 16,000 chars, which on HTML is mostly CSS. For `.html`, strip `<style>`/`<script>` and tags to text before truncation.

## Out of scope

Word/PDF export or any publish path (separate effort) · wiring the legacy roadmap system · an "attach existing file" UI (pointing a task at a path already works via `agent_output`) · changing Approve & delete · a document voice for markdown · Cadence `produce-artifact` HTML support and `program_lib` HTML versioning · thumbnails · screenshot-based judging.

## Surfaces touched (for scope-extension)

| Surface | Change |
|---|---|
| `scripts/task_server.py` | widen `_resolve_output_path` to `.html`; GET output returns `format: html`; new `/artifact/<id>` route with CSP |
| `ui/task-board/js/tasks.js`, `board.js` | `.html` tiles; new viewer overlay (`js/html-viewer.js` + css) |
| `scripts/profile_lib.py`, `task_server.py`, `js/profile.js` | `html` voice channel + "Visual style" textarea |
| `scripts/task_dispatch.py`, `task_cli.py`/`task_lib.py`, worker frontmatter | `output_format` field, default resolution, contract injection |
| `.claude/skills/context-html-artifact/` | new engine contract skill |
| `scripts/qmd-nightly-update.sh`, qmd config | html-to-text mirror + collection |
| `scripts/judge.py` | strip HTML before truncation |

Gates: pytest (new tests for path resolution, CSP header, voice channel, format resolution, judge stripping), `card_schema.py`, `program_schema.py`, `portability_gate.py`, denylist (the seed style lives only in `profile/`).
