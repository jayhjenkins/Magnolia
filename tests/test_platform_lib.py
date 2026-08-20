import sys
import types

import platform_lib


def test_os_kind_known(monkeypatch):
    monkeypatch.setattr(platform_lib.platform, "system", lambda: "Darwin")
    assert platform_lib.os_kind() == "darwin"
    monkeypatch.setattr(platform_lib.platform, "system", lambda: "Windows")
    assert platform_lib.os_kind() == "windows"
    monkeypatch.setattr(platform_lib.platform, "system", lambda: "Linux")
    assert platform_lib.os_kind() == "linux"


def test_open_url_cmd_per_os(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    assert platform_lib.open_url_cmd("http://x") == ["open", "http://x"]
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    assert platform_lib.open_url_cmd("http://x") == ["cmd", "/c", "start", "", "http://x"]
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "linux")
    assert platform_lib.open_url_cmd("http://x") == ["xdg-open", "http://x"]


def test_package_install_cmd_per_os(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    assert platform_lib.package_install_cmd("pandoc") == ["brew", "install", "pandoc"]
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "linux")
    assert platform_lib.package_install_cmd("pandoc") == ["brew", "install", "pandoc"]
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    assert platform_lib.package_install_cmd("pandoc") == ["winget", "install", "--id", "pandoc", "-e"]
    # documented no-equivalent package → None (unsupported-on-this-OS signal, not a broken command)
    assert platform_lib.package_install_cmd("fswatch") is None


def test_launch_agents_dir(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    assert platform_lib.launch_agents_dir().endswith("/Library/LaunchAgents")
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    assert platform_lib.launch_agents_dir() is None  # Task Scheduler has no dir


def test_headless_claude_env_strips_claude_vars():
    base = {"CLAUDE_CODE_X": "1", "CMUX_CLAUDE_Y": "1", "PATH": "/usr/bin", "HOME": "/h"}
    env = platform_lib.headless_claude_env(base=base)
    assert not any(k.startswith(("CLAUDE", "CMUX_CLAUDE")) for k in env)
    assert "HOME" in env


def test_headless_claude_env_keeps_windows_path_untouched(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    base = {"PATH": r"C:\Windows;C:\tools"}
    env = platform_lib.headless_claude_env(base=base)
    assert env["PATH"] == r"C:\Windows;C:\tools"


def test_resolve_claude_uses_which(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n, path=None: "/found/claude")
    assert platform_lib.resolve_claude() == "/found/claude"


def test_resolve_claude_falls_back_to_bare_name(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n, path=None: None)
    monkeypatch.setattr(platform_lib.os.path, "isfile", lambda p: False)
    assert platform_lib.resolve_claude() == "claude"


def test_process_group_kwargs_per_os(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    assert "creationflags" in platform_lib.process_group_kwargs()
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    assert platform_lib.process_group_kwargs() == {"start_new_session": True}


# ─── file-lock seam (cross-platform replacement for fcntl) ──────────────────

def test_lock_unlock_roundtrip(tmp_path):
    p = tmp_path / "counter"
    p.write_text("0")
    with open(p, "r+") as fd:
        assert platform_lib.lock(fd) is True
        platform_lib.unlock(fd)  # must not raise


def test_lock_nonblocking_returns_false_when_held(tmp_path):
    p = tmp_path / "lockfile"
    p.write_text("")
    fd1 = open(p, "w")
    fd2 = open(p, "w")
    try:
        assert platform_lib.lock(fd1, blocking=False) is True
        assert platform_lib.lock(fd2, blocking=False) is False
    finally:
        platform_lib.unlock(fd1)
        fd1.close()
        fd2.close()


def test_unlock_never_raises_when_nothing_held(tmp_path):
    p = tmp_path / "f"
    p.write_text("x")
    with open(p, "r+") as fd:
        platform_lib.unlock(fd)  # no lock held → silent


def _fake_msvcrt(locking):
    return types.SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=locking)


def test_lock_windows_uses_msvcrt(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    calls = []
    fake = _fake_msvcrt(lambda fileno, mode, n: calls.append((mode, n)))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    p = tmp_path / "f"
    p.write_text("0")
    with open(p, "r+") as fd:
        assert platform_lib.lock(fd) is True
        assert calls[0] == (fake.LK_LOCK, 1)
        platform_lib.unlock(fd)
        assert calls[-1] == (fake.LK_UNLCK, 1)


def test_lock_windows_nonblocking_contention_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")

    def boom(fileno, mode, n):
        raise OSError("already locked")

    monkeypatch.setitem(sys.modules, "msvcrt", _fake_msvcrt(boom))
    p = tmp_path / "f"
    p.write_text("0")
    with open(p, "r+") as fd:
        assert platform_lib.lock(fd, blocking=False) is False


# ─── resolve_tool + open_file_cmd seams ─────────────────────────────────────

def test_resolve_tool_found(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n: "/usr/bin/qmd")
    assert platform_lib.resolve_tool("qmd") == "/usr/bin/qmd"


def test_resolve_tool_missing_returns_none(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n: None)
    monkeypatch.setattr(platform_lib.os.path, "isfile", lambda p: False)
    assert platform_lib.resolve_tool("qmd") is None


def test_open_file_cmd_darwin(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    assert platform_lib.open_file_cmd("/x/y.docx") == ["open", "/x/y.docx"]


def test_open_file_cmd_windows(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    assert platform_lib.open_file_cmd("C:/x/y.docx") == ["cmd", "/c", "start", "", "C:/x/y.docx"]


def test_open_file_cmd_linux(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "linux")
    assert platform_lib.open_file_cmd("/x/y.docx") == ["xdg-open", "/x/y.docx"]


# ─── file-move seam (cross-platform replacement for shutil.move) ──────────────

def test_move_file_renames_within_tree(tmp_path):
    src = tmp_path / "source.txt"
    dst = tmp_path / "destination.txt"
    src.write_text("content here")

    platform_lib.move_file(str(src), str(dst))

    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "content here"


def test_move_file_creates_parent_dir(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "subdir1" / "subdir2" / "dst.txt"
    src.write_text("nested content")

    platform_lib.move_file(str(src), str(dst))

    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "nested content"
    assert dst.parent.exists()


# ─── resolve_codex seam ───────────────────────────────────────────────────────

def test_resolve_codex_uses_which(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n, path=None: "/found/codex")
    assert platform_lib.resolve_codex() == "/found/codex"


def test_resolve_codex_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(platform_lib.shutil, "which", lambda n, path=None: None)
    monkeypatch.setattr(platform_lib.os.path, "isfile", lambda p: False)
    assert platform_lib.resolve_codex() is None


# ─── headless_codex_env ───────────────────────────────────────────────────────

def test_headless_codex_env_strips_codex_vars():
    base = {"CODEX_SESSION": "x", "CODEX_THREAD": "y", "PATH": "/usr/bin", "HOME": "/h"}
    env = platform_lib.headless_codex_env(base=base)
    assert not any(k.startswith("CODEX") for k in env)
    assert "HOME" in env


def test_headless_codex_env_keeps_other_vars():
    base = {"FOO": "bar", "CLAUDE_X": "keep", "PATH": "/usr/bin"}
    env = platform_lib.headless_codex_env(base=base)
    assert env["FOO"] == "bar"
    # CLAUDE vars are NOT stripped by the codex env helper
    assert env["CLAUDE_X"] == "keep"


# ─── resolve_harness_binary ──────────────────────────────────────────────────

def test_resolve_harness_binary_claude(monkeypatch):
    monkeypatch.setattr(platform_lib, "resolve_claude", lambda path=None: "/bin/claude")
    result = platform_lib.resolve_harness_binary("claude")
    assert result == "/bin/claude"


def test_resolve_harness_binary_codex(monkeypatch):
    monkeypatch.setattr(platform_lib, "resolve_codex", lambda path=None: "/bin/codex")
    result = platform_lib.resolve_harness_binary("codex")
    assert result == "/bin/codex"


def test_resolve_harness_binary_codex_falls_back(monkeypatch):
    monkeypatch.setattr(platform_lib, "resolve_codex", lambda path=None: None)
    result = platform_lib.resolve_harness_binary("codex")
    assert result == "codex"


# ─── headless_harness_env ─────────────────────────────────────────────────────

def test_headless_harness_env_claude(monkeypatch):
    base = {"CLAUDE_X": "strip", "FOO": "keep", "PATH": "/usr/bin"}
    env = platform_lib.headless_harness_env("claude", base=base)
    # Delegates to headless_claude_env -> strips CLAUDE vars
    assert "CLAUDE_X" not in env
    assert env["FOO"] == "keep"


def test_headless_harness_env_codex(monkeypatch):
    base = {"CODEX_X": "strip", "FOO": "keep", "PATH": "/usr/bin"}
    env = platform_lib.headless_harness_env("codex", base=base)
    # Delegates to headless_codex_env -> strips CODEX vars
    assert "CODEX_X" not in env
    assert env["FOO"] == "keep"
