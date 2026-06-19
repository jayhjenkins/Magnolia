# Onboarding Front Door — Inc 3b: the onboarding room UI + board reveal

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Replace the `onboarding.html` placeholder with the real first-run room — a
themed, standalone, single-column concierge chat that streams the headless `meta-onboard`
session over SSE and, on completion, runs a window-shade reveal of the board beneath.

**Architecture:** Pure frontend over the Inc 3a backend (no backend change). The page is
served by the existing first-run gate (`task_server.do_GET` rewrites `/` -> `/onboarding.html`
until `profile_lib.onboarding_complete()`). It POSTs each turn to `/api/onboarding/run` and
reads the existing SSE frame format (`data: {...}\n\n` frames + a terminal `event: done`
sentinel), the same kinds chat.js handles (`think` / `tool_step` / `text` / `error` / `notice`
/ `result`) plus the synthetic `onboarding_complete` event the runner emits when meta-onboard
prints its sentinel (and `mark_onboarded` flips the gate). Because the page is standalone
(not the board's split workspace), `onboarding.js` carries its OWN copy of the safe
markdown + turn/step renderer ported from `chat.js` — it cannot lean on chat.js's
task-coupled globals (`openTask`, `chatState.task`, `fetchTasks`, `settleDetailFromServer`).

**Tech stack:** Vanilla JS, no build step, no libraries (board convention). Theme tokens
only (invariant #3) — the page links the same `themes/*.css` and runs the same pre-paint
Mood script as `index.html`, so it tracks the saved Mood. ASCII-only source/output
(invariant #8). All five gates green before commit.

**Decisions (locked with Jay):**
- **Kickoff:** auto-kickoff — clicking "Onboard me" sends a hidden first message so the
  concierge greets first; the kickoff is NOT rendered as a user bubble.
- **Completion copy:** exactly `You're all set. This onboarding will self destruct in 3 seconds :)`
- **Reveal:** the room panel behaves like a spring-loaded window shade — pull DOWN 12px over
  ~500ms, a brief "catch" pause, then LIFT offscreen over ~2.55s on
  `cubic-bezier(.42,.08,.76,.38)` (slow start, gradually faster), with a weighted edge-shadow
  along the panel's bottom, revealing the already-rendered board beneath. Board-beneath =
  a lazily-mounted `<iframe src="/">` (the marker is set by completion time, so `/` serves
  the real board; lazy mount avoids the gate recursing onboarding.html into the iframe).
  After the lift finishes, `window.location = "/"` so the top frame becomes the real board
  (seamless — the iframe already showed it). A "Go to my board" link is always available as
  a fallback.

**Scope guard (YAGNI):** reconnection/replay after a mid-onboarding reload is a documented
follow-on, NOT built here (no GET-history endpoint exists; the production-migration QA is a
single clean run). Keep 3b pure-frontend.

---

### Task 1: The onboarding room page (`ui/task-board/onboarding.html`)

**Files:**
- Modify (replace placeholder): `ui/task-board/onboarding.html`
- Test: `tests/test_onboarding_ui.py` (new — HTML shape/contract guards)

**Step 1: Write the failing shape test (HTML half)**

Create `tests/test_onboarding_ui.py`. The board has no JS harness, so these are Python
text-contract guards (mirrors `tests/test_installers.py` style). HTML assertions:

```python
import os
import re

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "ui", "task-board")

def _read(name):
    with open(os.path.join(UI, name), encoding="utf-8") as f:
        return f.read()

def test_onboarding_html_is_no_longer_the_placeholder():
    html = _read("onboarding.html")
    assert "PLACEHOLDER" not in html
    assert 'src="/js/onboarding.js"' in html

def test_onboarding_html_tracks_the_active_mood():
    # Same pre-paint Mood bootstrap + theme links as index.html, so the room
    # inherits the saved Mood (invariant #3 — token-only theming).
    html = _read("onboarding.html")
    assert "pmos-mood" in html
    assert "/themes/organic.css" in html
    assert "data-theme" in html

def test_onboarding_html_defines_spring_token_locally():
    # --spring lives only in index.html's inline :root, not the theme files, so a
    # standalone page MUST define it (and a --mono fallback) itself.
    html = _read("onboarding.html")
    assert "--spring" in html

def test_onboarding_html_has_the_room_anchors():
    html = _read("onboarding.html")
    for el in ('id="onboard-start"', 'id="onboard-thread"', 'id="onboard-input"',
               'id="onboard-send"', 'id="onboard-body"', 'id="board-underlay"'):
        assert el in html, el

def test_onboarding_html_carries_the_self_destruct_copy():
    html = _read("onboarding.html")
    assert "This onboarding will self destruct in 3 seconds :)" in html

def test_onboarding_html_is_ascii():
    # Invariant #8 — runtime/source ASCII (no em-dash, no smart quotes).
    raw = open(os.path.join(UI, "onboarding.html"), "rb").read()
    raw.decode("ascii")
```

**Step 2: Run it, confirm the HTML tests fail** (placeholder still present).
Run: `python3 -m pytest tests/test_onboarding_ui.py -k html -v`

**Step 3: Replace `onboarding.html`** with the real room. Structure:

- `<head>`: charset, viewport, `<title>Magnolia - Getting set up</title>`, the leaf favicon
  (copy the `#app-favicon` data-URI from index.html), the Google Fonts link (Spectral +
  Mulish, same as index.html), the six `themes/*.css` `<link>`s in the same order, and the
  identical pre-paint Mood script:
  `document.documentElement.dataset.theme = localStorage.getItem('pmos-mood') || 'organic';`
- `<style>`: a `:root` defining `--spring: cubic-bezier(.22,.9,.36,1.14);` and a `--mono`
  fallback (theme provides the rest). Then:
  - `body` uses `var(--app-bg)` / `var(--text)` / `var(--font-sans)` (copy index.html's body
    rule). `* { box-sizing }`.
  - **Welcome panel** (`#welcome`): centered column, max ~34rem, the leaf glyph (reuse the
    favicon leaf as an inline SVG ~44px in `var(--accent)`), a Spectral headline
    ("Welcome to Magnolia"), a `var(--text-dim)` subtitle (one warm line, e.g. "I'm your
    set-up concierge. A few questions and you'll be working - about ten minutes."), and the
    primary `#onboard-start` button styled on `var(--accent)`/`var(--accent-ink)`,
    `var(--r-btn)` radius.
  - **Conversation** (`#convo`, hidden until start): port the chat CSS from index.html
    lines ~1145-1297 verbatim but renamed/scoped under the room — reuse the SAME class names
    the ported renderer emits (`chat-turn`, `turn-user`, `turn-assistant`, `turn-text`,
    `turn-steps`, `tool-step`, `tool-think`, `steps-group`, `steps-toggle`, `typing`,
    `turn-error`, `turn-notice`, `md-h`, etc.) so Task 2's renderer styles correctly. Wrap
    in `#onboard-body` (the scroll container, `overflow-y:auto`) > `#onboard-thread`. Footer
    `#onboard-footer` = the composer (`textarea#onboard-input` placeholder
    "Type your reply..." + `button#onboard-send`, copy the send arrow SVG + `.chat-send`
    styling). Center the thread in a comfortable reading column (max ~46rem, margin auto).
  - **The room panel + shade animation**: the whole room (`#onboard-room`) is a
    `position:fixed; inset:0; z-index:2` panel with `background: var(--app-bg)`. Add:
    - `.shade-pull { transform: translateY(12px); transition: transform .5s ease; }`
    - `.shade-lift { transform: translateY(-110vh); transition: transform 2.55s cubic-bezier(.42,.08,.76,.38); }`
    - a weighted bottom edge: a `::after` on `#onboard-room` (full width, ~14px tall, sitting
      just below the panel) with a downward shadow gradient
      (`box-shadow: 0 10px 24px rgba(0,0,0,.22)` / a `linear-gradient` fade) so the moving
      shade reads as a physical edge.
    - `#board-underlay { position: fixed; inset: 0; z-index: 1; border: 0; }` holds the
      iframe (`width/height: 100%`). It sits BEHIND the room.
  - **Completion banner** (`#complete-banner`, hidden): centered over the room, carrying the
    exact self-destruct copy + a `#go-board` "Go to my board" fallback link to `/`.
- `<body>`: `#board-underlay` (empty), then `#onboard-room` containing `#welcome`, `#convo`
  (hidden), `#complete-banner` (hidden). Close with `<script src="/js/onboarding.js"></script>`.

Keep ALL color/radius/easing as `var(--token)` (invariant #3). ASCII only (hyphen not
em-dash, straight quotes) — invariant #8.

**Step 4: Run the HTML shape tests, confirm pass.**
Run: `python3 -m pytest tests/test_onboarding_ui.py -k html -v`

**Step 5: Commit.**
```bash
git add ui/task-board/onboarding.html tests/test_onboarding_ui.py
git commit -m "feat(onboarding): real first-run room page (Inc 3b)"
```

---

### Task 2: The room behavior (`ui/task-board/js/onboarding.js`)

**Files:**
- Create: `ui/task-board/js/onboarding.js`
- Test: `tests/test_onboarding_ui.py` (extend — JS shape/contract guards)

**Step 1: Write the failing shape test (JS half)** — append to `tests/test_onboarding_ui.py`:

```python
def test_onboarding_js_exists_and_posts_the_run_route():
    js = _read("js/onboarding.js")
    assert "/api/onboarding/run" in js

def test_onboarding_js_handles_the_completion_event():
    js = _read("js/onboarding.js")
    assert "onboarding_complete" in js

def test_onboarding_js_reads_the_sse_done_sentinel():
    js = _read("js/onboarding.js")
    assert "event:" in js and "done" in js  # mirrors chat.js handleFrame

def test_onboarding_js_runs_the_window_shade_reveal():
    js = _read("js/onboarding.js")
    assert "shade-pull" in js and "shade-lift" in js
    assert "board-underlay" in js  # mounts the iframe beneath

def test_onboarding_js_is_ascii():
    open(os.path.join(UI, "js", "onboarding.js"), "rb").read().decode("ascii")
```

**Step 2: Run it, confirm the JS tests fail** (file does not exist yet).
Run: `python3 -m pytest tests/test_onboarding_ui.py -k js -v`

**Step 3: Write `ui/task-board/js/onboarding.js`.** Port the SAFE, self-contained subset
from `ui/task-board/js/chat.js` (do NOT import — copy what's needed, drop task coupling):

- Helpers: `escapeHtml` (define it — chat.js gets it from a global), `revealNow`,
  `mdEscape`, `mdInline`, `mdFormatProse`, `renderMarkdown`, `elFromHTML`, `stepHtml`,
  `toolKind`, `makeStepsGroup`, `collapseGroup`, `renderStepsInto`, `renderTurn` (the
  user/assistant/error/notice branches), `STEP_COLLAPSE_AT`, `CHEV_SVG`, `CHECK_SVG`,
  `NOTICE_SVG`. `scrollThread()` targets `#onboard-body`.
- State: `let busy = false, completed = false;`. `const API = '/api';` (board convention).
  `const KICKOFF = "Hi, I'm ready to get set up.";` (the hidden first message).
- **Wire on load** (`DOMContentLoaded`): bind `#onboard-start` -> `startOnboarding()`; bind
  `#onboard-input` Enter (no shift) -> `sendReply()` + auto-grow; bind `#onboard-send` ->
  `sendReply()`; bind `#go-board` -> `location = '/'`.
- `startOnboarding()`: guard double-click; hide `#welcome` (fade/`gone`), reveal `#convo`,
  focus `#onboard-input`, then `runTurn(KICKOFF, {hidden: true})` — `hidden` means DON'T
  render a user bubble for the kickoff (the concierge speaks first).
- `sendReply()`: read+clear `#onboard-input`; if blank or `busy`, return; `runTurn(text)`
  (renders the user bubble).
- `runTurn(text, opts)`: mirror chat.js `sendChat` exactly —
  - if not hidden, append a `turn-user` bubble (`renderTurn`, `revealNow`).
  - append the assistant turn shell with the `.typing` indicator; grab `stepsBox`/`textBox`.
  - `busy = true`, disable send.
  - `fetch(API + '/onboarding/run', {method:'POST', headers, body: JSON.stringify({message:text})})`.
    - `409` -> render a `notice` turn ("Onboarding is already running.") and stop.
    - `!ok || !body` -> error text on the turn ("Could not reach the concierge. You can retry.").
  - Read the stream with a `ReadableStream` reader + `TextDecoder`, split on `\n\n`, and use
    the SAME `handleFrame` logic as chat.js: detect `event: done` (break) and parse
    `data:` JSON -> `renderEvent`.
  - `renderEvent(ev)`: the chat.js branches for `think` / `tool_step` (with the
    `STEP_COLLAPSE_AT` live-collapse) / `text` (accumulate `rawText`, re-render whole buffer)
    / `error` / `notice`, PLUS a new branch: `if (ev.kind === 'onboarding_complete') completeOnboarding();`.
  - on stream end: `busy = false`, re-enable send (unless `completed`), scroll.
- `completeOnboarding()`: single-fire via `completed`. Steps:
  1. disable the composer; show `#complete-banner` (with the exact self-destruct copy).
  2. mount the board beneath: create `<iframe src="/">` into `#board-underlay`; on its
     `load` event (board rendered), start the shade:
  3. add `shade-pull` to `#onboard-room` (12px down, .5s); after ~650ms (500ms pull + a
     brief "catch") add `shade-lift` (lift offscreen, 2.55s cubic-bezier).
  4. when the lift transition ends (listen for `transitionend` on `transform`, or a
     ~3300ms fallback timer), `window.location = '/'`.
  - Defensive: if the iframe never fires `load` within ~4s, run the shade anyway and then
    redirect (don't strand the user). The `#go-board` link is the always-available fallback.

ASCII only. Token-only (the JS sets classes; the page's CSS owns color). Mirror chat.js's
escape-first XSS model verbatim (assistant prose -> `renderMarkdown`; user text ->
`escapeHtml`).

**Step 4: Run the JS shape tests + the full UI suite, confirm pass.**
Run: `python3 -m pytest tests/test_onboarding_ui.py -v`

**Step 5: Commit.**
```bash
git add ui/task-board/js/onboarding.js tests/test_onboarding_ui.py
git commit -m "feat(onboarding): room behavior + window-shade board reveal (Inc 3b)"
```

---

### Live e2e verification (after both tasks pass review — NOT a committed task)

Stubbed, safe, on a FREE port (NOT 8742=prod, NOT a busy 8743). The worktree has no live
`profile/`, so the first-run gate triggers.

1. Temporarily stub `onboard_runner.run_turn` to yield a short canned conversation
   (a `think`, a couple `text` chunks, a `result`) and, on the SECOND turn (a reply), call
   `profile_lib.mark_onboarded()` then yield the `onboarding_complete` event — so the gate
   genuinely flips and `/` starts serving the board.
2. Start the board from the worktree on a free port; drive it with the Chrome-headless
   visual-pass technique (see the `visual-pass-technique` memory). Capture: (a) `/` serves
   the welcome room; (b) clicking "Onboard me" streams the canned turns; (c) a reply reaches
   completion -> the self-destruct banner + the window-shade lift revealing the board; (d)
   after redirect, `/` serves the real board.
3. Remove the stub. Restart the server after any `task_server`/runner change (it caches
   modules).

### Ship

All five gates green: `python3 -m pytest` / `python3 scripts/card_schema.py` /
`python3 -m pytest tests/test_engine_no_jay.py` / `python3 scripts/portability_gate.py` /
`python3 scripts/program_schema.py`. Push to stack onto PR #43 (NO new PR). Then the
production-migration QA is driven separately with Jay (back up `~/pm-os` + `~/.claude.json`
first; invariant #7 as a deliberate, user-directed exception).
