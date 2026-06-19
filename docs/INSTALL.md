# Installing Magnolia

One command installs everything, then you type `magnolia` and a browser opens into a guided
first-run setup. Pick your platform — each guide is self-contained, so you can hand a teammate
the single file for their OS:

- **macOS** → [`INSTALL-macos.md`](./INSTALL-macos.md)
- **Windows** → [`INSTALL-windows.md`](./INSTALL-windows.md)

Both walk the same shape:

1. **Install Claude Code** (once, if you don't have it) — https://claude.com/claude-code
2. **Run the one-line installer** — it installs prerequisites, signs you into Claude if needed,
   clones the repo, seeds folder trust, and puts a `magnolia` command on your PATH.
3. **Type `magnolia`** — the board starts and your browser opens. On a fresh setup it lands on a
   guided onboarding room: click **Onboard me**, answer a few questions, and the board appears,
   ready to work.

No two-prompt dance, no manual restart. The old "paste-prompts-into-Claude-Code-and-restart" flow
is gone — the installer handles the PATH hand-off, and onboarding runs inside the board.
