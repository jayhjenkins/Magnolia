"""GET /artifact/<id> serves a task's .html output under datasets/ with a sandbox CSP."""
import io
import json
import os
import pytest


class _FakeHandler:
    """Same shape as test_output_route._FakeHandler (copied: tests/ is not a package)."""
    def __init__(self, body=None):
        self._body = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = {"Content-Length": str(len(self._body))}
        self.status = None
        self._chunks = []

    @property
    def rfile(self):
        return io.BytesIO(self._body)

    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, b): self._chunks.append(b)
    def json(self): return json.loads(b"".join(self._chunks).decode("utf-8"))


def _seed_task_with_output(tasks_root, rel_path, content):
    """Create an agent task whose agent_output points at rel_path, and write that file."""
    import task_lib
    tid, _ = task_lib.create_task("Artifact page", queue="agent", domain="product")
    task_lib.update_task(tid, changes={"agent_output": rel_path})
    abspath = os.path.join(tasks_root, rel_path)
    os.makedirs(os.path.dirname(abspath), exist_ok=True)
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(content)
    return tid


class _HeaderHandler(_FakeHandler):
    """Records headers as a list of (name, value) so a duplicate is caught."""
    def __init__(self):
        super().__init__()
        self.headers_out = []
    def send_header(self, k, v): self.headers_out.append((k, v))
    def header(self, name):
        vals = [v for k, v in self.headers_out if k.lower() == name.lower()]
        assert len(vals) == 1, f"{name} sent {len(vals)} times: {vals}"
        return vals[0]


@pytest.fixture
def srv(tasks_root, monkeypatch):
    import task_server
    monkeypatch.setattr(task_server, "PM_OS_DIR", tasks_root)
    return task_server


def test_serves_html_with_csp(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = _HeaderHandler()
    srv.handle_artifact_page(h, tid)
    assert h.status == 200
    assert h.header("Content-Type").startswith("text/html")
    assert h.header("Content-Security-Policy") == srv.html_artifact_lib.CSP
    assert h.header("Cache-Control") == "no-store"
    assert b"".join(h._chunks) == b"<h1>Hi</h1>"


def test_refuses_markdown_and_outside_datasets(srv, tasks_root):
    md = _seed_task_with_output(tasks_root, "datasets/product/p.md", "# x")
    out = _seed_task_with_output(tasks_root, "scratch/p.html", "<h1>x</h1>")
    for tid in (md, out):
        h = _HeaderHandler()
        srv.handle_artifact_page(h, tid)
        assert h.status == 404


def test_missing_file_404(srv, tasks_root):
    import task_lib
    tid, _ = task_lib.create_task("x", queue="agent")
    task_lib.update_task(tid, changes={"agent_output": "datasets/product/none.html"})
    h = _HeaderHandler()
    srv.handle_artifact_page(h, tid)
    assert h.status == 404


def test_router_dispatches_artifact_with_query_string(srv, tasks_root):
    """The board requests /artifact/<id>?t=<cache-bust>; the router must strip the query."""
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = _HeaderHandler()
    h.path = f"/artifact/{tid}?t=123"
    assert srv.TaskServerHandler._route_request(h, "GET") is True
    assert h.status == 200
    assert b"".join(h._chunks) == b"<h1>Hi</h1>"


def test_router_artifact_invalid_id_400(srv):
    h = _HeaderHandler()
    h.path = "/artifact/not-a-task"
    assert srv.TaskServerHandler._route_request(h, "GET") is True
    assert h.status == 400


def test_serves_uppercase_htm(srv, tasks_root):
    tid = _seed_task_with_output(tasks_root, "datasets/product/P.HTM", "<p>upper</p>")
    h = _HeaderHandler()
    srv.handle_artifact_page(h, tid)
    assert h.status == 200
    assert h.header("Content-Security-Policy") == srv.html_artifact_lib.CSP
    assert b"".join(h._chunks) == b"<p>upper</p>"


def test_real_handler_sends_cache_control_and_csp_once(srv, tasks_root):
    """Drive the real TaskServerHandler (its end_headers override included) and
    parse the raw response, so a header duplicated by end_headers is caught."""
    tid = _seed_task_with_output(tasks_root, "datasets/product/p.html", "<h1>Hi</h1>")
    h = object.__new__(srv.TaskServerHandler)
    h.path = f"/artifact/{tid}?t=1"
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.requestline = f"GET {h.path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = False
    h.wfile = io.BytesIO()
    h.log_message = lambda *a, **k: None
    assert h._route_request("GET") is True
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    assert lines[0].startswith("HTTP/1.1 200")
    names = [ln.split(":", 1)[0].lower() for ln in lines[1:]]
    assert names.count("cache-control") == 1
    assert names.count("content-security-policy") == 1
    assert body == b"<h1>Hi</h1>"
