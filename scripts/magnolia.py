"""magnolia.py - the one-command launcher.

`magnolia`         start the board (if not running), ensure it relaunches on
                   reboot, and open it in the browser.
`magnolia update`  pull the latest engine (git pull, fast-forward only).
`magnolia doctor`  run capability detection and print the summary.

Thin orchestration over server_lib / persist_lib / platform_lib (the OS seam).
Profile-agnostic: it always starts the server and opens the browser;
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
    explicit = profile_lib.configured_server_port()
    if explicit is not None:
        target = explicit                      # respect a deliberate choice — and by
                                               # design we reuse a board already on this
                                               # port (the auto-free-port hunt below only
                                               # applies to the unconfigured path).
    else:
        # Fresh/unconfigured: claim our OWN port so we never piggyback on a board
        # already on 8742 (e.g. the user's prod). Prefer the canonical default when
        # it's actually free; otherwise take the first free port in a small range
        # (skipping 8743, the documented dev-board port), then fall back to an
        # OS-assigned free port. Persist it so the install is stable next run (and
        # so task_server reads it).
        if server_lib.port_available(8742):
            target = 8742
        else:
            target = next((p for p in range(8744, 8780) if server_lib.port_available(p)),
                          server_lib.free_port())
        profile_lib.set_server_port(target)
    started = False
    if not server_lib.is_running(port=target):
        server_lib.start(cmd=server_lib.default_cmd())   # task_server reads the (now-persisted) port from config
        started = True
    if not persist_lib.is_installed():
        persist_lib.install(program=server_lib.default_cmd(),
                            working_dir=PM_OS_DIR, log_path=LOG_PATH)
    url = f"http://localhost:{target}"
    if open_browser:
        platform_lib.open_url(url)
    return {"started": started, "url": url, "port": target}


def _run(cmd):
    """Mockable seam: run a command, return (returncode, combined_output)."""
    p = subprocess.run(cmd, cwd=PM_OS_DIR, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def update():
    """Pull the latest engine, fast-forward only (never auto-merge over local edits)."""
    rc, out = _run(["git", "-C", PM_OS_DIR, "pull", "--ff-only"])
    return {"status": "ok" if rc == 0 else "failed", "output": out}


def doctor():
    """Run capability detection (scripts/doctor.py detect) and return its output."""
    rc, out = _run([sys.executable, os.path.join(PM_OS_DIR, "scripts", "doctor.py"), "detect"])
    return {"status": "ok" if rc == 0 else "failed", "output": out}


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="magnolia",
                                description="Boot the Magnolia board and open it.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("update", help="pull the latest engine (fast-forward only)")
    sub.add_parser("doctor", help="run capability detection")
    args = p.parse_args(argv)

    if args.cmd == "update":
        res = update()
        print(res["output"].rstrip())
        return 0 if res["status"] == "ok" else 1
    if args.cmd == "doctor":
        res = doctor()
        print(res["output"].rstrip())
        return 0 if res["status"] == "ok" else 1
    try:
        res = launch()
    except Exception:
        print("Magnolia could not start the board. Run: magnolia doctor")
        return 1
    print(f"Magnolia is live at {res['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
