"""The single OS-abstraction seam for Magnolia.

Everything platform-specific (package managers, persistence mechanisms, opening
a URL) funnels through here so the rest of the engine stays platform-blind.

macOS is run-validated on the dev machine. Windows branches are written and
unit-tested against a mocked os_kind() but are DESIGN-VALIDATED, NOT
RUN-VALIDATED (no Windows box available).
"""
import os
import platform
import shutil
import signal
import subprocess


def os_kind():
    sysname = platform.system().lower()
    if sysname.startswith("darwin"):
        return "darwin"
    if sysname.startswith("windows"):
        return "windows"
    return "linux"


def open_url_cmd(url):
    kind = os_kind()
    if kind == "darwin":
        return ["open", url]
    if kind == "windows":
        # empty "" is the title arg for start; required when URL is quoted
        return ["cmd", "/c", "start", "", url]
    return ["xdg-open", url]


def open_url(url):
    subprocess.Popen(open_url_cmd(url))


# winget IDs differ from brew names; map the ones the Doctor installs.
_WINGET_IDS = {
    "pandoc": "pandoc",
    "qmd": "qmd",            # placeholder — verify real winget id during impl
    "fswatch": "",           # no winget equivalent; "" → package_install_cmd returns None
}


def package_install_cmd(name):
    kind = os_kind()
    if kind == "windows":
        wid = _WINGET_IDS.get(name, name)
        if not wid:
            # documented no-equivalent case (e.g. fswatch): signal "unsupported on
            # this OS" the same way launch_agents_dir() returns None, rather than
            # emitting a broken `winget install --id "" -e` command.
            return None
        return ["winget", "install", "--id", wid, "-e"]
    # darwin/linux both use brew in this engine's supported setups
    return ["brew", "install", name]


def launch_agents_dir():
    if os_kind() == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
    return None  # Windows uses Task Scheduler (no directory); Linux unsupported for persistence v1


# POSIX install locations to prefer ahead of the inherited PATH.
_CLAUDE_PREPEND_DIRS = [
    os.path.join(os.path.expanduser("~"), ".local", "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


def headless_claude_env(base=None):
    """Env for a headless `claude` subprocess, cross-platform.

    Strips CLAUDE*/CMUX_CLAUDE* (avoid nested-session detection). On POSIX,
    prepends common claude install dirs that actually exist. On Windows the
    inherited PATH is kept verbatim (claude resolves via shutil.which/PATHEXT) —
    never inject unix dirs or ':'-join onto a ';'-separated PATH.
    """
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if not k.startswith(("CLAUDE", "CMUX_CLAUDE"))}
    cur = env.get("PATH", os.defpath)
    if os_kind() != "windows":
        prepend = [d for d in _CLAUDE_PREPEND_DIRS if os.path.isdir(d)]
        if prepend:
            env["PATH"] = os.pathsep.join(prepend + [cur])
    return env


def resolve_claude(path=None):
    """Absolute path to the claude CLI, or the bare name as a last resort.

    shutil.which honors PATHEXT on Windows (finds claude.exe / claude.cmd).
    """
    found = shutil.which("claude", path=path)
    if found:
        return found
    for d in _CLAUDE_PREPEND_DIRS:
        cand = os.path.join(d, "claude")
        if os.path.isfile(cand):
            return cand
    return "claude"


def resolve_codex(path=None):
    """Absolute path to the codex CLI, or None if not installed.

    Unlike resolve_claude (which falls back to the bare name because claude is
    required), this returns None when absent so callers can degrade gracefully.
    """
    found = shutil.which("codex", path=path)
    if found:
        return found
    for d in _CLAUDE_PREPEND_DIRS:
        cand = os.path.join(d, "codex")
        if os.path.isfile(cand):
            return cand
    return None


def headless_codex_env(base=None):
    """Env for a headless `codex` subprocess, cross-platform.

    Mirrors headless_claude_env: strips CODEX* vars to avoid nested-session
    detection and prepends common install dirs on POSIX.
    """
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if not k.startswith(("CODEX",))}
    cur = env.get("PATH", os.defpath)
    if os_kind() != "windows":
        prepend = [d for d in _CLAUDE_PREPEND_DIRS if os.path.isdir(d)]
        if prepend:
            env["PATH"] = os.pathsep.join(prepend + [cur])
    return env


def resolve_harness_binary(harness_name, path=None):
    """Resolve the CLI binary for the named harness ('claude' or 'codex')."""
    if harness_name == "codex":
        return resolve_codex(path) or "codex"
    return resolve_claude(path)


def headless_harness_env(harness_name, base=None):
    """Build the subprocess env for the named harness."""
    if harness_name == "codex":
        return headless_codex_env(base)
    return headless_claude_env(base)


def resolve_tool(name):
    """Absolute path to a CLI tool, or None if not found.

    Honors PATHEXT on Windows via shutil.which. Unlike resolve_claude (which
    falls back to the bare name because claude is required), this returns None
    when the tool is genuinely absent, so callers can gracefully SKIP an
    optional tool (qmd, etc.) instead of crashing with WinError 2.
    """
    found = shutil.which(name)
    if found:
        return found
    for d in _CLAUDE_PREPEND_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return cand
    return None


def open_file_cmd(path):
    """OS-correct argv to open a file in the user's default handler."""
    kind = os_kind()
    if kind == "darwin":
        return ["open", path]
    if kind == "windows":
        # empty "" is the title arg for start; required when the path is quoted
        return ["cmd", "/c", "start", "", path]
    return ["xdg-open", path]


def process_group_kwargs():
    """Popen kwargs giving the child its own killable process group."""
    if os_kind() == "windows":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


def kill_process_group(proc):
    """Best-effort kill of a child and its group, cross-platform."""
    try:
        if os_kind() == "windows":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def lock(fd, exclusive=True, blocking=True):
    """Advisory lock on an open file object. Returns True if the lock was taken.

    The single cross-platform replacement for direct `fcntl` use (which is
    Unix-only and crashes at import on Windows). POSIX uses ``fcntl.flock`` and
    honours both ``exclusive`` and ``blocking``. Windows uses ``msvcrt.locking``,
    which only offers an exclusive byte-range lock — ``exclusive`` is ignored
    there. A blocking lock is best-effort on Windows (proceeds unlocked rather
    than crashing if the OS won't grant it); a non-blocking lock that can't be
    taken returns False on both platforms (used as the single-instance guard).

    Both imports are lazy so neither module is required at import time on the
    other platform.
    """
    if os_kind() == "windows":
        import msvcrt
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), mode, 1)
            return True
        except OSError:
            # blocking caller: proceed best-effort. non-blocking caller: report contention.
            return bool(blocking)
    import fcntl
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(fd.fileno(), flags)
        return True
    except OSError:
        return False


def unlock(fd):
    """Release a lock taken by lock(). Best-effort; never raises."""
    try:
        if os_kind() == "windows":
            import msvcrt
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def move_file(src, dst):
    """The single cross-platform file-move seam. Mirrors lock/resolve_tool.

    Creates parent directory if needed, then moves src to dst using
    shutil.move. Both paths should be absolute or relative to cwd. A raw
    shell `mv`/`move` is a portability bug; route all file moves through here.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
