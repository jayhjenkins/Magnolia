"""Server lifecycle primitives for the task board. Port-aware via profile config."""
import os
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_lib  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)


def port(root=None):
    return profile_lib.server_port(root)


def url(root=None):
    return f"http://localhost:{port(root)}"


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_available(p):
    """True if TCP port p on 127.0.0.1 is free to bind, False if in use.

    SO_REUSEADDR is deliberately NOT set so a real listener registers as
    unavailable (otherwise a board already on the port would falsely read free)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(p)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def is_running(port=None, root=None):
    p = port if port is not None else profile_lib.server_port(root)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/tasks", timeout=1.0) as r:
            return r.status == 200
    except Exception:
        return False


def default_cmd():
    return [sys.executable, os.path.join(SCRIPT_DIR, "task_server.py")]


def start(port=None, cmd=None, timeout=15.0, poll=0.25):
    """Launch the server detached; poll until it serves or raise TimeoutError.

    On any failure the spawned process is terminated (escalating to kill) so a
    failed start never leaves a lingering server.

    NOTE: when cmd is the default (task_server.py), the server resolves its own
    port from the profile config, so an explicit `port` argument must match the
    configured port. The onboarding flow persists the chosen port to config
    BEFORE calling start(), so the default `port=None` path stays consistent.
    """
    p = port if port is not None else profile_lib.server_port()
    command = cmd or default_cmd()
    proc = subprocess.Popen(command, cwd=PM_OS_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running(port=p):
            return proc
        if proc.poll() is not None:  # process died
            break
        time.sleep(poll)
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
    raise TimeoutError(f"server did not start serving on port {p} within {timeout}s")
