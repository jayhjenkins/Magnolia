"""magnolia.py - the one-command launcher.

`magnolia`         start the board (if not running), ensure it relaunches on
                   reboot, and open it in the browser.
`magnolia update`  pull the latest engine (git pull, fast-forward only).
`magnolia doctor`  run capability detection and print the summary.

Thin orchestration over server_lib / persist_lib / platform_lib (the OS seam) /
profile_lib. Profile-agnostic: it always starts the server and opens the browser;
the server's first-run gate decides whether to serve onboarding or the board. No
OS branches live here - they belong in platform_lib.
"""
import os
import subprocess
import sys

import platform_lib
import persist_lib
import profile_lib
import server_lib

PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(PM_OS_DIR, "logs", "task-server.log")


def launch(open_browser=True):
    """Start the board if needed, ensure reboot-persistence, open the browser.

    Idempotent: a board already running is reused, not double-started."""
    started = False
    if not server_lib.is_running():
        server_lib.start(cmd=server_lib.default_cmd())
        started = True
    if not persist_lib.is_installed():
        persist_lib.install(program=server_lib.default_cmd(),
                            working_dir=PM_OS_DIR, log_path=LOG_PATH)
    url = server_lib.url()
    if open_browser:
        platform_lib.open_url(url)
    return {"started": started, "url": url}
