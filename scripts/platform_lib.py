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


def text_kwargs():
    """subprocess kwargs to decode a child's output as UTF-8.

    `text=True` alone decodes with the locale encoding — cp1252 on Windows,
    which raises UnicodeDecodeError on curly quotes / em dashes / emoji that
    claude routinely emits. errors="replace" means a stray bad byte degrades to
    U+FFFD instead of crashing the caller."""
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


def is_cmd_shim(path):
    """True if `path` is a Windows batch shim (.cmd/.bat), e.g. npm's claude.cmd.

    Arguments to a batch shim are re-parsed by cmd.exe, which mangles long
    multi-line prompts (newlines truncate, & | < > ^ % are interpreted)."""
    if not path:
        return False
    return str(path).lower().endswith((".cmd", ".bat"))


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


def resolve_qmd():
    """Absolute path to the qmd CLI (tobi/qmd, `npm install -g @tobilu/qmd`), or None.

    On Windows npm installs qmd as a `qmd.cmd` batch shim, which shutil.which
    finds via PATHEXT; callers must launch it through foreground_cmd(), never as
    a bare argv (CreateProcess can't run a .cmd directly)."""
    return resolve_tool("qmd")


# cmd.exe re-parses a batch shim's command line: inside "..." the & | < > ^
# operators are literal, but these still expand or break the quoting outright.
_CMD_UNSAFE = ('"', "%", "!", "\r", "\n", "\0")
_CMD_NEEDS_QUOTES = (" ", "\t", "&", "|", "<", ">", "^", "(", ")", ",", ";", "=")


def _cmd_quote(arg):
    if any(c in arg for c in _CMD_UNSAFE):
        raise ValueError(f"argument not safe to pass through cmd.exe: {arg!r}")
    if not arg or any(c in arg for c in _CMD_NEEDS_QUOTES):
        return f'"{arg}"'
    return arg


def foreground_cmd(exe, args):
    """The command to Popen/exec `exe args...` WITHOUT a shell, cross-platform.

    POSIX and real Windows executables: a plain argv list. A Windows batch shim
    (.cmd/.bat, e.g. npm's qmd.cmd) can't be CreateProcess'd, so it runs under
    %COMSPEC% as an explicit command line `"cmd" /d /s /c ""<shim>" args"`:
    /s strips exactly the outer quote pair, /d skips AutoRun. Built by hand,
    never shell=True and never list2cmdline (its \\" escaping is not cmd's).
    Arguments cmd.exe would expand (% ! ") raise ValueError instead of being
    silently mangled."""
    args = list(args)
    if os_kind() != "windows" or not is_cmd_shim(exe):
        return [exe] + args
    if any(c in exe for c in _CMD_UNSAFE):
        raise ValueError(f"path not safe to pass through cmd.exe: {exe!r}")
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    inner = " ".join([f'"{exe}"'] + [_cmd_quote(a) for a in args])
    return f'"{comspec}" /d /s /c "{inner}"'


_JOB_HANDLE = None  # held open for the process lifetime; closing it kills the tree


def _kill_children_with_parent():
    """Windows: put this process in a KILL_ON_JOB_CLOSE job object, so every child
    it spawns afterwards (cmd.exe -> node for an npm shim) is killed by the OS the
    moment this process exits - including TerminateProcess, which no signal
    handler sees. Best-effort: returns False (no-op) off Windows or on failure."""
    global _JOB_HANDLE
    if os_kind() != "windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BasicLimits),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                wintypes.LPVOID, wintypes.DWORD]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9,  # JobObjectExtendedLimitInformation
                                           ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            return False
        _JOB_HANDLE = job
        return True
    except Exception:
        return False


def run_foreground(cmd):
    """Run `cmd` (from foreground_cmd) as a transparent stand-in for this process.

    stdin/stdout/stderr are inherited handles, never pipes, so a byte stream such
    as MCP stdio passes straight through: no buffering, no re-encoding.
    POSIX: exec in place (never returns) - the caller's signals, stdio and exit
    status belong to the child directly. Windows (no real exec): spawn, tie the
    child tree's life to ours via a job object, wait, and return its exit code."""
    if os_kind() != "windows":
        os.execv(cmd[0], cmd)
    _kill_children_with_parent()
    proc = subprocess.Popen(cmd)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            # Ctrl+C/Break reaches the child via the shared console; let it
            # shut down and report its own exit code.
            continue


def open_file_cmd(path):
    """OS-correct argv to open a file in the user's default handler."""
    kind = os_kind()
    if kind == "darwin":
        return ["open", path]
    if kind == "windows":
        # empty "" is the title arg for start; required when the path is quoted
        return ["cmd", "/c", "start", "", path]
    return ["xdg-open", path]


def reveal_file_cmd(path):
    """OS-correct argv to show a file selected in the file manager.

    macOS and Windows select the file itself; Linux has no portable "select",
    so it opens the containing folder."""
    kind = os_kind()
    if kind == "darwin":
        return ["open", "-R", path]
    if kind == "windows":
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", os.path.dirname(path)]


def process_group_kwargs():
    """Popen kwargs giving the child its own killable process group."""
    if os_kind() == "windows":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


# Windows creation flags (literal fallbacks: the subprocess constants only exist
# on Windows builds of Python).
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def detached_popen_kwargs(breakaway=False):
    """Popen kwargs so a long-lived background child (the board server) outlives
    the terminal that launched it.

    Windows: closing a console window kills every process attached to it, so the
    child gets its own process group and its own hidden console
    (CREATE_NO_WINDOW, not DETACHED_PROCESS: grandchildren like `claude` then
    inherit the hidden console instead of each popping a visible window).
    `breakaway` adds CREATE_BREAKAWAY_FROM_JOB for terminals that wrap their
    shell in a kill-on-close job object. POSIX: {} (behavior unchanged).
    """
    if os_kind() != "windows":
        return {}
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", _CREATE_NEW_PROCESS_GROUP)
             | getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW))
    if breakaway:
        flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", _CREATE_BREAKAWAY_FROM_JOB)
    return {"creationflags": flags}


def popen_detached(cmd, **kwargs):
    """subprocess.Popen a long-lived background child detached from this terminal.

    On Windows, first try breaking away from the parent's job object; if the job
    forbids breakaway (Popen raises OSError / access denied), retry without it."""
    if os_kind() == "windows":
        try:
            return subprocess.Popen(cmd, **detached_popen_kwargs(breakaway=True), **kwargs)
        except OSError:
            return subprocess.Popen(cmd, **detached_popen_kwargs(breakaway=False), **kwargs)
    return subprocess.Popen(cmd, **kwargs)


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
