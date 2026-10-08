import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import server_lib


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    # start() appends server output to LOG_PATH; keep tests out of the repo's logs/.
    monkeypatch.setattr(server_lib, "LOG_PATH", str(tmp_path / "logs" / "task-server.log"))


def test_url_uses_port(monkeypatch):
    monkeypatch.setattr(server_lib.profile_lib, "server_port", lambda root=None: 8755)
    assert server_lib.url() == "http://localhost:8755"


def test_free_port_returns_unused():
    p = server_lib.free_port()
    # we can bind it → it was free
    s = socket.socket()
    s.bind(("127.0.0.1", p))
    s.close()


def test_port_available_true_when_free():
    p = server_lib.free_port()
    assert server_lib.port_available(p) is True


def test_port_available_false_when_bound():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert server_lib.port_available(port) is False
    finally:
        s.close()


def test_is_running_true_when_api_serves():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"[]")
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        assert server_lib.is_running(port=port) is True
    finally:
        srv.shutdown()


def test_is_running_false_when_nothing_listens():
    assert server_lib.is_running(port=59997) is False


import sys as _sys


def test_start_polls_until_serving(tmp_path):
    # a tiny server script that serves /api/tasks 200 on argv[1]
    script = tmp_path / "tiny.py"
    script.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'[]')\n"
        "    def log_message(self,*a): pass\n"
        "HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n"
    )
    p = server_lib.free_port()
    proc = server_lib.start(port=p, cmd=[_sys.executable, str(script), str(p)], timeout=5.0)
    try:
        assert server_lib.is_running(port=p) is True
    finally:
        proc.terminate()


def test_start_raises_if_never_serves():
    p = server_lib.free_port()
    import pytest
    with pytest.raises(TimeoutError):
        # 'true' exits immediately, never serves
        server_lib.start(port=p, cmd=["true"], timeout=1.5)


def _pid_alive(pid):
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_start_kills_child_that_ignores_sigterm(tmp_path):
    # A child that ignores SIGTERM and never serves /api/tasks. terminate()
    # alone never reaps it, so start() must escalate to kill on the failure
    # path. The child records its own pid so we can verify it is gone after
    # start() raises. Without the kill fallback this child stays alive and the
    # assertion fails.
    import os
    import time as _t

    pidfile = tmp_path / "child.pid"
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time, os, sys\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    time.sleep(0.05)\n"
    )
    import pytest
    p = server_lib.free_port()
    with pytest.raises(TimeoutError):
        server_lib.start(
            port=p,
            cmd=[_sys.executable, str(script), str(pidfile)],
            timeout=1.0,
        )

    # The child wrote its pid before installing the SIGTERM handler.
    deadline = _t.monotonic() + 2.0
    while _t.monotonic() < deadline and not pidfile.exists():
        _t.sleep(0.02)
    assert pidfile.exists(), "child never started"
    child_pid = int(pidfile.read_text())

    # start() reaps the child before raising, so it must be dead now. Give a
    # tiny grace for the OS to release the pid.
    deadline = _t.monotonic() + 2.0
    while _t.monotonic() < deadline and _pid_alive(child_pid):
        _t.sleep(0.02)
    if _pid_alive(child_pid):
        # Clean up the orphan so it does not leak into other tests.
        try:
            os.kill(child_pid, 9)
        except ProcessLookupError:
            pass
        raise AssertionError("start() left a SIGTERM-ignoring child alive")


def test_start_logs_server_output_instead_of_devnull(tmp_path):
    # A crashing server's stderr must land in the log so the failure is diagnosable.
    script = tmp_path / "crash.py"
    script.write_text("import sys\nsys.stderr.write('ModuleNotFoundError: croniter\\n')\nsys.exit(1)\n")
    p = server_lib.free_port()
    with pytest.raises(TimeoutError) as ei:
        server_lib.start(port=p, cmd=[_sys.executable, str(script)], timeout=3.0)
    assert server_lib.LOG_PATH in str(ei.value)
    body = open(server_lib.LOG_PATH, encoding="utf-8").read()
    assert "--- board start" in body
    assert "ModuleNotFoundError: croniter" in body
    assert "croniter" in server_lib.log_tail(5)


def test_start_spawns_through_detached_seam(monkeypatch):
    seen = {}

    class FakeProc:
        def poll(self): return 1
        def terminate(self): pass
        def wait(self, timeout=None): return 1
        def kill(self): pass

    def fake_popen_detached(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return FakeProc()

    monkeypatch.setattr(server_lib.platform_lib, "popen_detached", fake_popen_detached)
    with pytest.raises(TimeoutError):
        server_lib.start(port=server_lib.free_port(), cmd=["x"], timeout=0.5, poll=0.05)
    assert seen["cmd"] == ["x"]
    assert seen["kw"]["stdout"] is not server_lib.subprocess.DEVNULL
    assert seen["kw"]["stderr"] == server_lib.subprocess.STDOUT
    assert seen["kw"]["stdin"] == server_lib.subprocess.DEVNULL


def test_log_tail_missing_file_is_empty(tmp_path):
    assert server_lib.log_tail(5, path=str(tmp_path / "nope.log")) == ""


def test_log_tail_returns_last_lines(tmp_path):
    f = tmp_path / "x.log"
    f.write_text("\n".join(f"line{i}" for i in range(50)) + "\n")
    assert server_lib.log_tail(3, path=str(f)).splitlines() == ["line47", "line48", "line49"]
