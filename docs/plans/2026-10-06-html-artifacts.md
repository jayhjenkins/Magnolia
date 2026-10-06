# HTML Artifacts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** A task whose `agent_output` is an `.html` file gets the same loop as markdown: the card points at it, the operator previews it in the task pane or a full tab, and changes it in place via chat or source.

**Architecture:** One pure helper module (`scripts/html_artifact_lib.py`) owns every HTML rule: path test, format detection, the dispatch/chat guidance text, the CSP, and html-to-text. The server widens the output routes to `.html`, adds a sandboxed `/artifact/<id>` route, and adds an `html` voice channel. The board gets a viewer overlay that reuses the markdown editor's takeover chrome. Dispatch, chat, judge, and qmd each call the helper.

**Tech stack:** Python stdlib (`html.parser`, `re`), vanilla JS, existing pytest `_FakeHandler` pattern.

Design: [`2026-10-06-html-artifacts-design.md`](./2026-10-06-html-artifacts-design.md).

---

## Build contract (from meta-scope-extension)

| Surface | Decision | Seam | Gate |
|---|---|---|---|
| adapter | none - no external write | - | - |
| worker | reuse all; optional `output_format` frontmatter key read by dispatch | `scripts/task_dispatch.py` call site of `build_prompt_for_worker` | pytest |
| card | no registry change; extend the existing `outputLink` JS handler + detail-pane tile | `ui/task-board/js/board.js`, `js/tasks.js` | `card_schema.py` (unchanged, must stay OK) |
| platform / UI | extend: server routes, new viewer overlay, profile voice channel, judge, qmd mirror | `task_server.py`, `js/html-viewer.js`, `profile_lib`, `judge.py`, `scripts/html_text_mirror.py` | pytest + `portability_gate.py` |
| skill | build-new: `context-html-artifact` engine contract | `.claude/skills/context-html-artifact/SKILL.md` | `test_skill_frontmatter.py` + denylist |

**Standing items for every subagent:**
- Runtime output ASCII-safe: hyphen not em-dash, ASCII quotes, in every string the code emits (prompt text, errors, UI copy).
- No person/team/company identity in engine files (invariant #1). The operator's house style lives only in `profile/voice/html.md` (gitignored).
- UI CSS is token-only (invariant #3) - the artifact's own CSS is the artifact's business, but the board chrome around it uses Mood tokens.
- OS specifics go through `platform_lib` (invariant #8). Nothing here should need it.
- Inspect history with `git show` / `git diff`, never `git checkout`.
- Gates before each commit: `python3 -m pytest -q`, `python3 scripts/card_schema.py`, `python3 scripts/program_schema.py`, `python3 scripts/portability_gate.py`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `html_artifact_lib` - the single home for HTML rules

**Files:**
- Create: `scripts/html_artifact_lib.py`
- Test: `tests/test_html_artifact_lib.py`

**Step 1: Write the failing tests**

```python
"""Pure helpers for HTML artifacts - path test, format detection/resolution,
guidance text, CSP, html-to-text."""
import html_artifact_lib as h


def test_is_html_path():
    assert h.is_html_path("datasets/product/x.html")
    assert h.is_html_path("  datasets/product/X.HTM ")
    assert not h.is_html_path("datasets/product/x.md")
    assert not h.is_html_path("")
    assert not h.is_html_path(None)


def test_detect_output_format_phrases():
    assert h.detect_output_format("Q4 roadmap", "build it as HTML") == "html"
    assert h.detect_output_format("Pre-read as a page", "") == "html"
    assert h.detect_output_format("Pre-read as an HTML page", "") == "html"
    assert h.detect_output_format("Write the memo", "summarize the html export") is None
    assert h.detect_output_format("Write the memo", "") is None


def test_resolve_output_format_precedence():
    assert h.resolve_output_format({"output_format": "html"}, {"output_format": "md"}) == "html"
    assert h.resolve_output_format({}, {"output_format": "html"}) == "html"
    assert h.resolve_output_format({}, {}) == "md"
    assert h.resolve_output_format({"output_format": "bogus"}, {}) == "md"
    assert h.resolve_output_format(None, None) == "md"


def test_dispatch_block_points_at_contract_and_profile():
    b = h.dispatch_block()
    assert "context-html-artifact" in b and "profile/voice/html.md" in b
    assert ".html" in b
    b.encode("ascii")  # ASCII-safe runtime text


def test_chat_hint_names_path_and_in_place_rule():
    t = h.chat_hint("datasets/product/agent-output/x.html")
    assert "datasets/product/agent-output/x.html" in t
    assert "in place" in t
    t.encode("ascii")


def test_csp_blocks_network_and_same_origin():
    assert "sandbox allow-scripts" in h.CSP
    assert "allow-same-origin" not in h.CSP
    assert "connect-src 'none'" in h.CSP


def test_html_to_text_drops_style_script_and_tags():
    src = ("<html><head><style>.a{color:red}</style><script>var x=1</script></head>"
           "<body><h1>Offers</h1><p>GA in <b>Q4</b>.</p></body></html>")
    out = h.html_to_text(src)
    assert "color:red" not in out and "var x" not in out
    assert "Offers" in out and "GA in Q4." in out
```

**Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_html_artifact_lib.py -q`
Expected: FAIL, `ModuleNotFoundError: html_artifact_lib`.

**Step 3: Implement**

```python
"""html_artifact_lib - the one place the engine's HTML-artifact rules live.

Rendering, dispatch, chat, judge, and search all import from here so the rules
can't drift. Pure functions, stdlib only. Runtime text is ASCII-safe.
"""
import re
from html.parser import HTMLParser

FORMATS = ("md", "html")
CONTRACT_SKILL = ".claude/skills/context-html-artifact/SKILL.md"
STYLE_PROFILE = "profile/voice/html.md"

# Served on /artifact/<id>. No allow-same-origin: the page runs in an opaque
# origin and can't read the board's API; connect-src 'none' also blocks fetch
# because the server sends Access-Control-Allow-Origin: *.
CSP = (
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; "
    "default-src 'none'; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; "
    "img-src https: data:; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "connect-src 'none'"
)

_HTML_ASK = re.compile(r"\bas\s+(?:an?\s+)?(?:html(?:\s+page)?|page|web\s*page)\b", re.I)


def is_html_path(path):
    p = (path or "").strip().lower()
    return p.endswith(".html") or p.endswith(".htm")


def detect_output_format(title, description=""):
    """'html' when the ask says so explicitly ("as HTML", "as a page"), else None."""
    text = f"{title or ''}\n{description or ''}"
    return "html" if _HTML_ASK.search(text) else None


def resolve_output_format(task_fm, worker):
    """Task field wins, then the worker's default, then md."""
    for src in (task_fm or {}, worker or {}):
        v = str(src.get("output_format") or "").strip().lower()
        if v in FORMATS:
            return v
    return "md"


def dispatch_block():
    return (
        "\n\nOUTPUT FORMAT: HTML\n"
        "This task's deliverable is a single self-contained .html file, not markdown.\n"
        f"- Before writing, read {CONTRACT_SKILL} (the technical contract) and "
        f"{STYLE_PROFILE} (the operator's visual style) and follow both.\n"
        "- Write it under the appropriate datasets/ directory and complete with "
        "--output pointing at the .html path.\n"
    )


def chat_hint(path):
    return (
        f"This task's output is an HTML page at {path}. When asked to change it, "
        "make targeted edits to that file in place with the Edit tool - never "
        "regenerate or rewrite the whole file, and never save a new copy. Keep to "
        f"{CONTRACT_SKILL} and {STYLE_PROFILE}.\n\n"
    )


class _Text(HTMLParser):
    _SKIP = {"style", "script", "noscript", "svg", "template"}
    _BLOCK = {"p", "div", "section", "header", "footer", "li", "tr", "h1", "h2",
              "h3", "h4", "h5", "h6", "br", "table", "ul", "ol", "blockquote", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(src):
    p = _Text()
    p.feed(src or "")
    p.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in "".join(p.out).split("\n"))
    return "\n".join(ln for ln in lines if ln)
```

**Step 4: Run to verify it passes** - same command, expected PASS.

**Step 5: Commit** - `git add scripts/html_artifact_lib.py tests/test_html_artifact_lib.py && git commit -m "html artifacts: shared helper lib"`.

---

### Task 2: Output routes accept `.html` (Word stays markdown-only)

**Files:**
- Modify: `scripts/task_server.py` - `_resolve_output_path` (~:219), `handle_get_output` (~:862), `handle_save_output` (~:1049), `handle_publish_word` (~:956), the Word-link lookup (~:297)
- Test: `tests/test_output_route.py` (append)

**Step 1: Failing tests** (append; reuse the file's `_FakeHandler`, `srv`, `_seed_task_with_output`)

```python
def test_resolve_output_path_html_opt_in(srv):
    assert srv._resolve_output_path("datasets/product/x.html") is None          # default md-only
    got = srv._resolve_output_path("datasets/product/x.html", allow_html=True)
    assert got is not None and got.endswith("x.html")
    assert srv._resolve_output_path("../../etc/x.html", allow_html=True) is None


def test_get_output_html_returns_format_html(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = _FakeHandler()
    srv.handle_get_output(h, tid)
    body = h.json()
    assert h.status == 200 and body["format"] == "html" and body["content"] == "<h1>Hi</h1>"
    assert "word" not in body


def test_put_output_html_saves_in_place(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    srv.handle_save_output(_FakeHandler({"content": "<h1>Bye</h1>"}), tid)
    with open(os.path.join(tasks_root, "datasets/product/p.html"), encoding="utf-8") as f:
        assert f.read() == "<h1>Bye</h1>"


def test_publish_word_still_rejects_html(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = _FakeHandler({"confirm": False})
    srv.handle_publish_word(h, tid)
    assert h.status == 404
```

**Step 2:** `python3 -m pytest tests/test_output_route.py -q` -> new tests FAIL.

**Step 3: Implement**
- `_resolve_output_path(rel, allow_html=False)`: accept `rel.endswith(".md") or (allow_html and html_artifact_lib.is_html_path(rel))`. Update the docstring.
- `handle_get_output` / `handle_save_output`: call with `allow_html=True`; set `fmt = "html" if html_artifact_lib.is_html_path(rel) else "markdown"` and return it as `format`; only attach `word` when `fmt == "markdown"`. Error text becomes "Task has no editable output".
- `handle_publish_word` and the Word-link lookup keep the default (md-only) - no change beyond confirming the call has no `allow_html`.
- `import html_artifact_lib` alongside the other script imports.

**Step 4:** run the file plus `tests/test_word_publish.py` -> PASS.

**Step 5: Commit** - "html artifacts: output routes read/write .html".

---

### Task 3: `GET /artifact/<TASK-ID>` - sandboxed full-page route

**Files:**
- Modify: `scripts/task_server.py` - new `handle_artifact_page(handler, task_id)` near `handle_get_output`; route in `_route_request` next to the `/output` route
- Test: `tests/test_artifact_route.py`

**Step 1: Failing tests**

```python
"""GET /artifact/<id> serves a task's .html output under datasets/ with a sandbox CSP."""
import os
import pytest
from test_output_route import _FakeHandler, _seed_task_with_output  # noqa: F401


class _HeaderHandler(_FakeHandler):
    def __init__(self):
        super().__init__()
        self.headers_out = {}
    def send_header(self, k, v): self.headers_out[k] = v


@pytest.fixture
def srv(tasks_root, monkeypatch):
    import task_server
    monkeypatch.setattr(task_server, "PM_OS_DIR", tasks_root)
    return task_server


def test_serves_html_with_csp(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = _HeaderHandler()
    srv.handle_artifact_page(h, tid)
    assert h.status == 200
    assert h.headers_out["Content-Type"].startswith("text/html")
    assert h.headers_out["Content-Security-Policy"] == srv.html_artifact_lib.CSP
    assert h.headers_out["Cache-Control"] == "no-store"
    assert b"".join(h._chunks) == b"<h1>Hi</h1>"


def test_refuses_markdown_and_outside_datasets(srv, tasks_root):
    md = _seed_task_with_output(tasks_root, "datasets/product/p.md", "# x")
    out = _seed_task_with_output(tasks_root, "scratch/p.html", "<h1>x</h1>")
    for tid in (md, out):
        h = _HeaderHandler()
        srv.handle_artifact_page(h, tid)
        assert h.status == 404


def test_missing_file_404(srv, tasks_root):
    import task_lib
    tid, _ = task_lib.create_task("x", queue="agent")
    task_lib.update_task(tid, changes={"agent_output": "datasets/product/none.html"})
    h = _HeaderHandler()
    srv.handle_artifact_page(h, tid)
    assert h.status == 404
```

If `test_output_route` isn't importable as a module, copy `_FakeHandler` / `_seed_task_with_output` into the new file instead.

**Step 2:** run -> FAIL (`handle_artifact_page` missing).

**Step 3: Implement**

```python
def handle_artifact_page(handler, task_id):
    """GET /artifact/<id> - the task's .html output as a standalone page.

    Only .html under datasets/. Sent with a sandbox CSP (html_artifact_lib.CSP)
    so the page gets an opaque origin and no network access to the board API."""
    try:
        task_data = task_lib.read_task(task_id)
    except FileNotFoundError:
        _error_response(handler, f"Task {task_id} not found", status=404)
        return
    rel = str(task_data["frontmatter"].get("agent_output") or "")
    filepath = _resolve_output_path(rel, allow_html=True)
    if (filepath is None or not html_artifact_lib.is_html_path(rel)
            or not _under_datasets(filepath) or not os.path.isfile(filepath)):
        _error_response(handler, "Task has no HTML page", status=404)
        return
    with open(filepath, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Security-Policy", html_artifact_lib.CSP)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)
```

Route (GET only), placed before the `/api/tasks/{id}` match:

```python
        match = re.match(r"^/artifact/([^/?]+)$", path)
        if match and method == "GET":
            task_id = _parse_task_id(match.group(1))
            if task_id is None:
                _error_response(self, "Invalid task ID format", status=400)
            else:
                handle_artifact_page(self, task_id)
            return True
```

Confirm `path` in `_route_request` is the query-stripped path; if not, strip `?` before matching.

**Step 4:** run -> PASS. **Step 5: Commit** - "html artifacts: sandboxed /artifact page route".

---

### Task 4: `html` voice channel ("Visual style") in the profile

**Files:**
- Modify: `scripts/task_server.py` - `_VOICE_CHANNELS` (~:651), `build_profile` voice dict (~:575), `apply_profile_voice` docstring
- Modify: `scripts/profile_lib.py` - `voice_text` docstring only (channel=None must stay teams+email; the judge uses it for messages)
- Modify: `ui/task-board/js/profile.js` - `_pfVoice`, `pfSaveVoice`
- Create: `profile.example/voice/html.md` (generic, identity-free starter)
- Test: `tests/test_profile_api.py` (append)

**Step 1: Failing tests**

```python
def test_apply_voice_accepts_html_channel(profile_root):
    import task_server, profile_lib
    st, _ = task_server.apply_profile_voice({"html": "no pills"}, root=profile_root)
    assert st == 200
    assert profile_lib.voice_text("html", root=profile_root) == "no pills"
    assert "no pills" not in profile_lib.voice_text(root=profile_root)   # messages voice unaffected


def test_build_profile_exposes_html_voice(profile_root):
    import task_server, profile_lib
    profile_lib.write_voice("html", "palette", root=profile_root)
    assert task_server.build_profile(profile_root)["voice"]["html"] == "palette"
```

Match `build_profile`'s real signature when writing the second test.

**Step 2:** run -> FAIL.

**Step 3: Implement**
- `_VOICE_CHANNELS = {"teams", "email", "html"}`; `voice["html"] = profile_lib.voice_text("html", root)`.
- `profile.js`: add a third section after Email, same markup shape: title "Visual style", hint "How your HTML pages look", intro "Magnolia builds HTML pages to this. Palette, layout, components, and what never to do.", textarea `#pf-voice-html`, a Save button, `pf-saved-voice-html` - no Regenerate button. `pfSaveVoice` sends `{teams, email, html}`, updates `_profile.voice`, flashes all three.
- `profile.example/voice/html.md`: short generic starter (one white content card on a tinted page, one accent color, uppercase section labels, ruled tables; no pill tags; no helper text). No names or brands.

**Step 4:** run `tests/test_profile_api.py tests/test_profile_lib.py tests/test_engine_no_jay.py` -> PASS.

**Step 5: Commit** - "html artifacts: Visual style voice channel".

**Step 6 (controller, not subagent, not committed):** write the operator's `profile/voice/html.md` seeded from the house style in the design doc section 5, including the ban list and exemplar paths. `profile/` is gitignored.

---

### Task 5: Declare the format - task field, worker default, dispatch injection

**Files:**
- Modify: `scripts/task_lib.py` `create_task` (~:189) - new `output_format=None` kwarg
- Modify: `scripts/task_cli.py` `cmd_add` + `p_add` - `--format {md,html}`
- Modify: `scripts/task_dispatch.py` (~:812) - append `html_artifact_lib.dispatch_block()` when the resolved format is html
- Test: `tests/test_html_output_format.py`

**Step 1: Failing tests**

```python
import task_lib


def test_create_task_sets_explicit_format(tasks_root):
    tid, _ = task_lib.create_task("Q4 swim lanes", queue="agent", output_format="html")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_create_task_detects_phrase(tasks_root):
    tid, _ = task_lib.create_task("Pre-read for Thursday", queue="agent",
                                  description="Build it as HTML")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_create_task_defaults_absent(tasks_root):
    tid, _ = task_lib.create_task("Strategy memo", queue="agent")
    assert "output_format" not in task_lib.read_task(tid)["frontmatter"]


def test_create_task_rejects_bad_format(tasks_root):
    import pytest
    with pytest.raises(ValueError):
        task_lib.create_task("x", queue="agent", output_format="pdf")


def test_dispatch_appends_block_for_html(monkeypatch):
    import task_dispatch, html_artifact_lib
    p = task_dispatch.apply_output_format("BASE", {"output_format": "html"}, {})
    assert p.startswith("BASE") and html_artifact_lib.dispatch_block() in p
    assert task_dispatch.apply_output_format("BASE", {}, {}) == "BASE"
    assert html_artifact_lib.dispatch_block() in task_dispatch.apply_output_format(
        "BASE", {}, {"output_format": "html"})
```

**Step 2:** run -> FAIL.

**Step 3: Implement**
- `create_task`: validate `output_format in (None, "md", "html")` else `ValueError`; `fmt = output_format or html_artifact_lib.detect_output_format(title, description)`; if `fmt`, set `frontmatter["output_format"] = fmt`.
- `task_cli`: `p_add.add_argument("--format", dest="output_format", choices=["md", "html"], default=None)`; pass through in `cmd_add`.
- `task_dispatch`: add

```python
def apply_output_format(prompt, task_fm, worker):
    """Append the HTML contract pointer when the task resolves to html."""
    if html_artifact_lib.resolve_output_format(task_fm, worker) == "html":
        return prompt + html_artifact_lib.dispatch_block()
    return prompt
```

  and at the call site: `prompt = apply_output_format(build_prompt_for_worker(task_id, worker, rerun=rerun), task, worker)`. Confirm `task` there is the frontmatter dict; if it's the full task record, pass its frontmatter.

**Step 4:** run plus `tests/test_task_lib.py tests/test_dispatch_model.py tests/test_quick_add_route.py` -> PASS.

**Step 5: Commit** - "html artifacts: output_format field and dispatch guidance".

---

### Task 6: Chat edits HTML in place

**Files:**
- Modify: `scripts/chat_runner.py` - `build_context_prompt` (~:245) and `build_resume_prompt` (~:296)
- Test: `tests/test_chat_runner_html_hint.py`

**Step 1: Failing tests**

```python
import chat_runner, html_artifact_lib


def _task(out):
    return {"id": "TASK-0001", "title": "t", "agent_output": out}


def test_context_prompt_includes_hint_for_html(monkeypatch):
    p = chat_runner.build_context_prompt(_task("datasets/product/x.html"), "", "tweak it")
    assert html_artifact_lib.chat_hint("datasets/product/x.html") in p


def test_context_prompt_no_hint_for_md():
    p = chat_runner.build_context_prompt(_task("datasets/product/x.md"), "", "tweak it")
    assert "HTML page at" not in p
```

Mirror whatever profile/task fixtures `tests/test_chat_runner_*.py` already use so `build_context_prompt` runs. Add a resume-prompt test if `build_resume_prompt` takes a task dict the same way.

**Step 2:** run -> FAIL.

**Step 3: Implement** - a helper `_html_hint(task)` returning `html_artifact_lib.chat_hint(out)` when `is_html_path(task.get("agent_output"))`, else `""`; insert it just before `{task_block}` in the fresh prompt and before the user message in the resume prompt.

**Step 4:** run plus all `tests/test_chat_runner_*.py` -> PASS. **Step 5: Commit** - "html artifacts: chat edits pages in place".

---

### Task 7: `context-html-artifact` - the engine contract skill

**Files:**
- Create: `.claude/skills/context-html-artifact/SKILL.md`

Content (identity-free; this is the technical contract, taste lives in the profile):

```markdown
---
name: context-html-artifact
description: Use when a task's deliverable is an HTML page (output_format html, or an existing .html agent_output) - the technical contract every HTML artifact must meet so it renders in the board's sandboxed preview and full-page tab
allowed-tools: Read, Write, Edit, Bash
---

# HTML artifact contract

Read `profile/voice/html.md` first. It is the operator's visual style - palette,
layout, components, and bans. Follow it exactly. This file is the technical
contract that keeps the page working inside Magnolia.

## Must
- One self-contained `.html` file. All CSS inline in a `<style>` block. Images as
  `data:` URIs or https URLs.
- External hosts only: fonts.googleapis.com, fonts.gstatic.com, cdn.jsdelivr.net,
  cdnjs.cloudflare.com, and https images. Anything else is blocked by the viewer.
- No network calls from script (fetch, XHR, websockets) - they are blocked.
- Full-width design that still reads at 640px wide: fluid widths, a
  `@media (max-width: 720px)` pass, no fixed-width canvases.
- A `@media print` pass: no shadows or tinted page background, avoid breaking
  cards across pages.
- `<title>` set; the header shows the title and the date.
- Plain hyphens in text, not em dashes.

## Editing an existing page
- Change it in place with targeted edits. Never regenerate the whole file for a
  change, and never save a new copy (no -v2) for an edit.
- Keep the page's existing structure and classes; add CSS only when a new
  component needs it.

## Where it goes
- Write under the appropriate `datasets/` directory (default
  `datasets/product/agent-output/YYYY-MM-DD_<slug>.html`) and complete the task with
  `--output` pointing at it. Pages outside `datasets/` do not render in the board.
```

**Step 2:** `python3 -m pytest tests/test_skill_frontmatter.py tests/test_engine_no_jay.py -q` -> PASS. **Commit** - "html artifacts: context-html-artifact contract skill".

---

### Task 8: Judge reads HTML as text

**Files:**
- Modify: `scripts/judge.py` `read_artifact` (~:222)
- Test: `tests/test_judge_html_artifact.py`

**Step 1: Failing test**

```python
import os
import judge


def test_read_artifact_strips_html(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "PM_OS_DIR", str(tmp_path))
    p = tmp_path / "x.html"
    p.write_text("<style>" + ".a{}" * 9000 + "</style><h1>Offers GA</h1>", encoding="utf-8")
    text, _ = judge.read_artifact("x.html")
    assert "Offers GA" in text and ".a{}" not in text
```

**Step 2:** FAIL. **Step 3:** after reading, `if html_artifact_lib.is_html_path(candidate): text = html_artifact_lib.html_to_text(text)`, before the truncation check. **Step 4:** run plus `tests/test_judge_*.py` -> PASS. **Step 5: Commit** - "html artifacts: judge scores page text, not markup".

---

### Task 9: Search indexes HTML pages

**Files:**
- Create: `scripts/html_text_mirror.py`
- Modify: `scripts/qmd-nightly-update.sh` (run the mirror before `qmd update`), `scripts/qmd-setup.sh` (add the collection + context), `.gitignore` (`datasets/.qmd-html/`)
- Test: `tests/test_html_text_mirror.py`

**Step 1: Failing test**

```python
import html_text_mirror


def test_mirror_writes_md_per_html(tmp_path):
    ds = tmp_path / "datasets"
    (ds / "product").mkdir(parents=True)
    (ds / "product" / "r.html").write_text("<title>Roadmap</title><h1>H2</h1><p>Feed GA</p>", encoding="utf-8")
    (ds / "product" / "n.md").write_text("# not mirrored", encoding="utf-8")
    n = html_text_mirror.build(str(tmp_path))
    out = ds / ".qmd-html" / "product" / "r.html.md"
    assert n == 1 and out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "source: datasets/product/r.html" in body and "Feed GA" in body


def test_mirror_removes_stale(tmp_path):
    ds = tmp_path / "datasets"
    stale = ds / ".qmd-html" / "gone.html.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    html_text_mirror.build(str(tmp_path))
    assert not stale.exists()
```

**Step 2:** FAIL. **Step 3: Implement**

```python
"""html_text_mirror - plain-text copies of datasets/**/*.html for qmd.

qmd indexes markdown only. This rebuilds datasets/.qmd-html/<rel>.html.md (one per
page, frontmatter points at the source) so context-search can find HTML artifacts.
Run nightly before `qmd update`. Usage: python3 scripts/html_text_mirror.py
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import html_artifact_lib  # noqa: E402

MIRROR = ".qmd-html"


def build(root):
    ds = os.path.join(root, "datasets")
    out_root = os.path.join(ds, MIRROR)
    shutil.rmtree(out_root, ignore_errors=True)
    count = 0
    for dirpath, dirnames, filenames in os.walk(ds):
        dirnames[:] = [d for d in dirnames if d != MIRROR and not d.startswith(".")]
        for fn in filenames:
            if not html_artifact_lib.is_html_path(fn):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, ds)
            with open(src, encoding="utf-8", errors="replace") as f:
                text = html_artifact_lib.html_to_text(f.read())
            dest = os.path.join(out_root, rel + ".md")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(f"---\nsource: datasets/{rel.replace(os.sep, '/')}\n---\n\n{text}\n")
            count += 1
    return count


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"html_text_mirror: {build(root)} pages")
```

- nightly: `python3 "$REPO/scripts/html_text_mirror.py" >> "$LOG" 2>&1` before `qmd update`.
- setup: `$QMD collection add "$PMDIR/datasets/.qmd-html" --mask "**/*.md" --name html_artifacts` and a context line: "Plain-text copies of HTML artifacts (roadmaps, pre-reads, infographics, dashboards). The source field points at the real .html file - cite and open that."
- `.gitignore`: `datasets/.qmd-html/`.

**Step 4:** run + `tests/test_portability_gate.py tests/test_script_paths.py` -> PASS (if the portability gate flags the `.sh` edit, keep the change to the one python line, which mirrors the existing calls). **Step 5: Commit** - "html artifacts: qmd mirror makes pages searchable".

**Step 6 (controller, local machine):** run the mirror once, `qmd collection add ... --name html_artifacts`, `qmd update && qmd embed`, and confirm a query for a known roadmap phrase hits.

---

### Task 10: Board - HTML tile, viewer overlay, full tab

**Files:**
- Create: `ui/task-board/js/html-viewer.js`, `ui/task-board/css/html-viewer.css`
- Modify: `ui/task-board/js/tasks.js` (~:91), `ui/task-board/js/board.js` (~:120), `ui/task-board/index.html` (link css after `markdown-editor.css` at ~:1637; script after `markdown-editor.js` at ~:1855)

**Behavior:**
- `tasks.js`: when `isHtml(v)` (`/\.html?$/i`), push `{ icon:'doc', cls:'dt-review dt-html', kind:'HTML page', name, path, inline:true, taskId, label:'Preview', external:false }`. The `.dt-review` click handler in `markdown-editor.js` must ignore `.dt-html` - change its selector to `.dt-review[data-output-task]:not(.dt-html)`.
- `board.js` `outputLink`: `.html`/`.htm` -> `{ href: '/artifact/' + task.id, label: 'Open page', external: true }`.
- `html-viewer.js` (IIFE, same globals as markdown-editor.js): capture-phase click on `.dt-html[data-output-task]` -> `openHtmlViewer(taskId)`:
  - Build `.dt-editor.dt-htmlview` inside `#split-modal .task-pane`, reusing the markdown editor's bar markup classes (`dte-bar`, `dte-back`, `dte-doc`, `dte-spacer`) so it inherits the chrome. Right side buttons: "Source" toggle, "Open full" (`<a target="_blank" href="/artifact/<id>">`), "Copy path".
  - Body: `<iframe class="dth-frame" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" src="/artifact/<id>?t=<ts>">`.
  - Source mode: hide the iframe, show `<textarea class="dth-source">` filled from `GET /api/tasks/<id>/output`; input debounced 750ms -> `PUT` `{content}`; on save, refresh the iframe `src` with a new `?t=`.
  - Live reload: while open and not in source mode with unsaved edits, poll `GET /api/tasks/<id>/output` every 2s; if `content` changed since last seen, refresh the iframe. Clear the poll on close.
  - Close: back button, Esc (capture-phase, one layer, like markdown-editor.js), and wrap `window.closeModal` to tear down first. Reuse `is-open`/`has-editor` classes for the slide-in.
- `html-viewer.css`: token-only. `.dth-frame { flex:1; width:100%; border:0; background:#fff; }` is the one exception - it's the canvas the page paints on, not chrome; use `background: var(--paper, var(--surface))` if the gate or review objects. `.dth-source` uses `--font-mono`-ish fallback, `--text`, `--surface`, `--border`.

**Verify:** `python3 scripts/card_schema.py` -> `registry.json OK`; `python3 -m pytest tests/test_onboarding_ui.py tests/test_smoke.py -q` -> PASS. Live check happens in Task 11.

**Commit** - "html artifacts: board preview, source edit, full tab".

---

### Task 11: Docs, gates, live e2e

1. Docs: add a short "HTML artifacts" paragraph to `ui/task-board/CLAUDE.md` (viewer, `/artifact/<id>`, CSP) and a line in `docs/reference/architecture.md` §9 pointing at `scripts/html_artifact_lib.py`.
2. All gates green: `python3 -m pytest -q`, `python3 scripts/card_schema.py`, `python3 scripts/program_schema.py`, `python3 scripts/portability_gate.py`.
3. Live e2e on `localhost:8744`:
   - Point a scratch task at `datasets/product/packages/2026/perkspot-offers/offers-adoption-review-2026-09-21.html`; open it -> tile reads "HTML page / Preview"; preview renders in the pane with chat beside it; "Open full" opens a full tab.
   - DevTools: response carries the CSP; `fetch('/api/tasks')` from the iframe console fails.
   - Source mode edit (scratch copy, not the real file) -> preview refreshes.
   - Ask the chat for a one-word change -> file edited in place, no `-v2`, preview reloads.
   - Profile tab shows Visual style with the seeded content.
   - A markdown task still opens in Milkdown; Word menu unchanged.
4. Finish per `superpowers:finishing-a-development-branch`; merge authority for this build: **merge to main when green**.
