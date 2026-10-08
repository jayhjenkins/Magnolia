"""Server lifecycle primitives for the task board. Port-aware via profile config."""
import os
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
# Same file the reboot-persistence agent logs to (magnolia.LOG_PATH).
LOG_PATH = os.path.join(PM_OS_DIR, "logs", "task-server.log")


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
    # Server output goes to logs/task-server.log (never DEVNULL) so a crash on
    # startup - e.g. a missing dependency - is diagnosable. The child keeps its
    # own handle to the log; ours is closed right after the spawn. Detached via
    # the OS seam so closing the launching terminal doesn't kill the board.
    log = _open_log()
    try:
        proc = platform_lib.popen_detached(command, cwd=PM_OS_DIR, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
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
    raise TimeoutError(f"server did not start serving on port {p} within {timeout}s "
                       f"(see {LOG_PATH})")


def _open_log():
    """Open the server log for appending, stamped with a start marker."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    f = open(LOG_PATH, "ab")
    f.write(f"\n--- board start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode("utf-8"))
    f.flush()
    return f


def log_tail(n=15, path=None):
    """Last n lines of the server log ('' if absent). Best-effort, never raises."""
    try:
        with open(path or LOG_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            data = f.read()
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-n:])
