---
name: context-html-artifact
description: Use when a task's deliverable is an HTML page (output_format html, or an existing .html agent_output) - the technical contract every HTML artifact must meet so it renders in the board's sandboxed preview and full-page tab
allowed-tools: Read, Write, Edit, Bash
---

# HTML artifact contract

Read `profile/voice/html.md` first. It is the operator's visual style - palette,
layout, components, and bans. Follow it exactly. If it is missing, use
`profile.example/voice/html.md`. This file is the technical contract that keeps
the page working inside Magnolia.

## Must
- One self-contained `.html` file. All CSS inline in a `<style>` block. Images as
  `data:` URIs or https URLs.
- External hosts, by type (this matches the viewer's CSP):
  - Scripts: cdn.jsdelivr.net, cdnjs.cloudflare.com.
  - Stylesheets: fonts.googleapis.com only (everything else inline).
  - Fonts: fonts.gstatic.com or `data:`.
  - Images: https or `data:`.
  - Anything else is blocked by the viewer.
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
