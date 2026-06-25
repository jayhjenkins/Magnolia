#!/usr/bin/env python3
"""
One-time (or periodic re-auth) script.
Opens a real browser window for Microsoft OAuth login to Otter.ai,
then saves the session state for use by otter_sync.py.

Session state is written to the profile transcript state dir
(profile/transcript/session.json), created if missing.

Run this when:
  - First-time setup
  - otter_sync.log shows "Session expired"
"""

import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # optional transcript extra; absent unless the Otter feed is set up
    sync_playwright = None

# ── Engine wiring ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_lib  # noqa: E402

STATE_DIR = Path(profile_lib.transcript_state_dir())
SESSION_FILE = STATE_DIR / "session.json"


def main() -> None:
    if sync_playwright is None:
        sys.stderr.write(
            "Playwright not installed - Otter sign-in is unavailable on this machine.\n"
            "The Otter transcript feed needs extra dependencies. Install them first:\n"
            "  pip install -r requirements-transcript.txt\n"
            "  python3 -m playwright install chromium\n"
            "Then re-run: python3 scripts/otter_auth.py\n"
            "(Or switch your transcript feed to Granola, which needs none of this.)\n")
        sys.exit(1)

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Otter.ai — Microsoft OAuth Login")
    print("=" * 60)
    print()
    print("A browser window will open.")
    print("Sign in with Microsoft as you normally would.")
    print("This window closes automatically once login is detected.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://otter.ai/login")

        print("Complete the Microsoft sign-in in the browser window.")
        print("When you can see your Otter home screen, come back here and press Enter.")
        input("  >>> Press Enter to save session and close browser: ")

        print("Saving session...")
        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    os.chmod(SESSION_FILE, 0o600)
    print(f"\nSession saved to: {SESSION_FILE}")
    print("You're all set. Run otter_sync.py to test:")
    print("  python3 scripts/otter_sync.py")


if __name__ == "__main__":
    main()
