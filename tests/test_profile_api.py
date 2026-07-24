def test_build_profile_shape(profile_root):
    import task_server
    p = task_server.build_profile(root=profile_root)
    assert p["identity"]["name"] == "Test User"
    assert p["identity"]["email"] == "test@example.com"
    assert "system" not in p                       # System section is cut
    pm = p["integrations"]["project_management"]
    assert pm["active"] == "jira"
    assert any(o["id"] == "jira" for o in pm["options"])
    assert all("status" in o for o in pm["options"])
    # voice is two channels read from the md files
    assert "Tight" in p["voice"]["teams"] and "Warm" in p["voice"]["email"]
    # packs: active from config + an available catalog with id/label/description
    assert "core" in p["packs"]["active"]
    assert all({"id", "label", "description"} <= set(a) for a in p["packs"]["available"])
    # model posture level from config; workers is a list (possibly empty)
    assert p["model_posture"]["level"] == "balanced"
    assert isinstance(p["model_posture"]["workers"], list)


# build_profile owns the /api/profile contract the frontend consumes. The
# Doctor writes capability statuses from a richer vocabulary
# (ok/missing/degraded/running/down/not_expected/needs_reauth/unknown), but
# the Profile room frontend only understands {ok, reauth, available, unset}.
# build_profile MUST normalize Doctor statuses into the frontend vocabulary.
_FRONTEND_STATUS_VOCAB = {"ok", "reauth", "available", "unset"}


def test_build_profile_status_from_capabilities(profile_root):
    # The Doctor keys remote capabilities by the provider id itself (see
    # doctor._REMOTE_FROM_INTEGRATION: project_management -> prov), so the
    # capability key for the Jira PM provider is literally "jira".
    # A Doctor "needs_reauth" status must normalize to the frontend "reauth".
    import task_server
    import profile_lib
    profile_lib.write_capabilities({"capabilities": {"jira": {"status": "needs_reauth"}}}, root=profile_root)
    p = task_server.build_profile(root=profile_root)
    jira = next(o for o in p["integrations"]["project_management"]["options"] if o["id"] == "jira")
    assert jira["status"] == "reauth"           # normalized, NOT "needs_reauth"
    assert jira["status"] in _FRONTEND_STATUS_VOCAB


def test_build_profile_status_missing_normalizes_to_unset(profile_root):
    import task_server
    import profile_lib
    profile_lib.write_capabilities({"capabilities": {"jira": {"status": "missing"}}}, root=profile_root)
    p = task_server.build_profile(root=profile_root)
    jira = next(o for o in p["integrations"]["project_management"]["options"] if o["id"] == "jira")
    assert jira["status"] == "unset"
    assert jira["status"] in _FRONTEND_STATUS_VOCAB


def test_build_profile_status_ok_normalizes_to_ok(profile_root):
    import task_server
    import profile_lib
    profile_lib.write_capabilities({"capabilities": {"jira": {"status": "ok"}}}, root=profile_root)
    p = task_server.build_profile(root=profile_root)
    jira = next(o for o in p["integrations"]["project_management"]["options"] if o["id"] == "jira")
    assert jira["status"] == "ok"
    assert jira["status"] in _FRONTEND_STATUS_VOCAB


def test_build_profile_transcript_status_from_category_capability(profile_root):
    # The Doctor keys the transcripts capability by CATEGORY ("transcript"), not provider id.
    import task_server, profile_lib
    # profile_root's integrations.yaml has transcript provider = granola (active)
    profile_lib.write_capabilities({"capabilities": {"transcript": {"status": "needs_reauth",
                                                                      "detail": "session expired"}}},
                                    root=profile_root)
    p = task_server.build_profile(root=profile_root)
    opts = p["integrations"]["transcripts"]["options"]
    active = next(o for o in opts if o["id"] == p["integrations"]["transcripts"]["active"])
    assert active["status"] == "reauth"          # normalized from needs_reauth via the category key
    assert "session expired" in (active.get("detail") or "")


def test_build_profile_active_provider_status_ok_without_capability(profile_root):
    # With no capability entry: the active provider (jira) reads "ok" and a
    # non-active known adapter (asana) reads "available".
    import task_server
    p = task_server.build_profile(root=profile_root)
    pm = p["integrations"]["project_management"]
    jira = next(o for o in pm["options"] if o["id"] == "jira")
    assert jira["status"] == "ok"
    assert jira["status"] in _FRONTEND_STATUS_VOCAB
    asana = next((o for o in pm["options"] if o["id"] == "asana"), None)
    if asana is not None:
        assert asana["status"] == "available"


# ─── Profile write endpoints (Task 4.3) ──────────────────────────────────────
# These test the PURE helpers behind the HTTP wrappers. The helpers do the
# validation + profile_lib persistence and return (status_code, body).


def test_apply_identity_persists(profile_root):
    import task_server, profile_lib
    st, body = task_server.apply_profile_identity(
        {"name": "Jay", "email": "jay@v.com", "company": "Vantaca", "timezone": "America/Chicago"},
        root=profile_root)
    assert st == 200 and body["ok"] is True
    assert profile_lib.profile(root=profile_root)["display_name"] == "Jay"
    assert profile_lib.profile(root=profile_root)["email"] == "jay@v.com"


def test_apply_identity_ignores_unknown_keys(profile_root):
    import task_server, profile_lib
    st, _ = task_server.apply_profile_identity(
        {"name": "Jay", "persona": "evil", "extra": "x"}, root=profile_root)
    assert st == 200
    # persona is NOT a known identity key; must be left untouched.
    assert profile_lib.profile(root=profile_root)["persona"] == "pm"


def test_apply_voice_writes_channel(profile_root):
    import task_server, profile_lib
    st, _ = task_server.apply_profile_voice({"teams": "tight + lowercase"}, root=profile_root)
    assert st == 200
    assert "tight + lowercase" in profile_lib.voice_text("teams", root=profile_root)
    assert "Warm" in profile_lib.voice_text("email", root=profile_root)   # untouched


def test_apply_voice_rejects_bad_channel(profile_root):
    import task_server, profile_lib
    st, body = task_server.apply_profile_voice({"../config": "x"}, root=profile_root)
    assert st == 400   # path-traversal/unknown channel rejected; nothing written
    assert "error" in body
    # teams.md must still hold its seeded content (nothing written)
    assert "Tight" in profile_lib.voice_text("teams", root=profile_root)


def test_apply_integration_maps_transcripts_key(profile_root):
    import task_server, profile_lib
    st, _ = task_server.apply_profile_integration("transcripts", {"active": "otter"}, root=profile_root)
    assert st == 200
    assert profile_lib.provider("transcript", root=profile_root) == "otter"   # on-disk singular key


def test_apply_integration_rejects_unknown_category(profile_root):
    import task_server
    st, body = task_server.apply_profile_integration("../../etc", {"active": "x"}, root=profile_root)
    assert st == 400
    assert "error" in body


def test_apply_packs_persists(profile_root):
    import task_server, profile_lib
    st, _ = task_server.apply_profile_packs({"active": ["core", "exec"]}, root=profile_root)
    assert st == 200 and profile_lib.config(root=profile_root)["active_skill_packs"] == ["core", "exec"]


def test_apply_model_posture_validates_level(profile_root):
    import task_server, profile_lib
    assert task_server.apply_profile_model_posture({"level": "high"}, root=profile_root)[0] == 200
    assert profile_lib.config(root=profile_root)["models"]["cost_posture"] == "high"
    assert task_server.apply_profile_model_posture({"level": "bogus"}, root=profile_root)[0] == 400


# ─── Profile write WRAPPERS (Task 4.3 follow-up) ──────────────────────────────
# The pure helpers above are well covered; the handle_profile_* HTTP wrappers
# (body read + error emission) were not. These exercise that layer with a
# minimal fake handler that satisfies _read_request_body (headers + rfile) and
# captures what the wrapper emits via _json_response/_error_response.

import io
import json as _json


class _FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler for wrapper tests.

    Captures the status code and parsed JSON body the handler emits. Provides
    headers + rfile so _read_request_body works, and the send_*/wfile surface
    that _json_response writes through (so we also prove the connection wasn't
    left hanging — a full response is written, headers ended, body flushed)."""

    def __init__(self, raw_body: bytes):
        self.headers = {"Content-Length": str(len(raw_body))}
        self.rfile = io.BytesIO(raw_body)
        self.wfile = io.BytesIO()
        self.status = None
        self._headers_ended = False

    def send_response(self, code):
        self.status = code

    def send_header(self, *_a, **_k):
        pass

    def end_headers(self):
        self._headers_ended = True

    @property
    def emitted_json(self):
        return _json.loads(self.wfile.getvalue().decode("utf-8"))


def _fake(payload):
    return _FakeHandler(_json.dumps(payload).encode("utf-8"))


def test_wrapper_malformed_json_returns_400():
    # Garbage body -> _read_request_body raises JSONDecodeError -> 400, NOT 500.
    import task_server
    h = _FakeHandler(b"{not valid json")
    task_server.handle_profile_identity(h)
    assert h.status == 400
    assert "Invalid JSON body" in h.emitted_json["error"]
    assert h._headers_ended                       # response fully emitted, not hung


def test_wrapper_persistence_failure_returns_clean_500(monkeypatch):
    # A raise from the apply_* helper (e.g. disk/permission/YAML failure) must
    # become a clean JSON 500 — not an uncaught exception / dropped connection.
    import task_server

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(task_server, "apply_profile_identity", boom)
    h = _fake({"name": "Jay"})
    task_server.handle_profile_identity(h)        # must NOT raise
    assert h.status == 500
    assert "Failed to update profile" in h.emitted_json["error"]
    assert "disk full" in h.emitted_json["error"]
    assert h._headers_ended                       # connection not left hanging


def test_wrapper_validation_400_passthrough(monkeypatch):
    # A 400 from the helper (no fields) is emitted as a 400, not swallowed/500'd.
    import task_server
    monkeypatch.setattr(task_server, "apply_profile_identity",
                        lambda *_a, **_k: (400, {"error": "No identity fields provided"}))
    h = _fake({})
    task_server.handle_profile_identity(h)
    assert h.status == 400
    assert h.emitted_json["error"] == "No identity fields provided"


def test_apply_packs_rejects_non_string_elements(profile_root):
    # {"active": [{"x": 1}]} must be rejected (400) rather than persisting nested
    # objects into config.yaml — consistent with the other helpers' validation.
    import task_server, profile_lib
    before = profile_lib.config(root=profile_root)["active_skill_packs"]
    st, body = task_server.apply_profile_packs({"active": [{"x": 1}]}, root=profile_root)
    assert st == 400
    assert "error" in body
    # nothing persisted — active packs unchanged
    assert profile_lib.config(root=profile_root)["active_skill_packs"] == before


def test_cut_endpoints_not_routed():
    # The approved Phase 6 design CUT POST /api/system/restart and
    # POST /api/doctor/fix/{capability}. They must remain unrouted (404).
    # No clean unit hook resolves a path+method to a handler, so assert the
    # router source does not reference these path patterns.
    import inspect, task_server
    src = inspect.getsource(task_server.TaskServerHandler._route_request)
    assert "system/restart" not in src
    assert "doctor/fix" not in src


def test_build_profile_packs_derive_from_manifest(profile_root):
    import os, textwrap, task_server
    claude = os.path.join(profile_root, ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, "packs.yaml"), "w") as f:
        f.write(textwrap.dedent("""\
            core:  {label: "Core", description: "Baseline.", skills: [task-create]}
            pm:    {label: "Product Management", description: "PRDs.", skills: [workflow-prd-creation]}
            exec:  {label: "Executive", description: "Memos.", skills: [workflow-strategy-memo]}
        """))
    p = task_server.build_profile(root=profile_root)
    ids = {c["id"] for c in p["packs"]["available"]}
    assert ids == {"core", "pm", "exec"}          # from manifest, not the old hardcoded list
    assert "core" in p["packs"]["active"]


def test_model_posture_workers_include_resolved_model(profile_root, monkeypatch):
    import task_server
    # Make _profile_workers return a known worker tier regardless of real files
    monkeypatch.setattr(task_server, "_profile_workers",
                        lambda root=None: [{"name": "researcher", "tier": "deep"}])
    p = task_server.build_profile(root=profile_root)   # config posture: balanced
    w = p["model_posture"]["workers"][0]
    assert w["tier"] == "deep"
    assert w["model"] == "opus"   # deep @ balanced
