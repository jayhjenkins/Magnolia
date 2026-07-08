import os

import doctor


def test_probe_which_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: "/usr/bin/" + n)
    cap = doctor.probe_which("pandoc")
    assert cap["kind"] == "local"
    assert cap["status"] == "ok"
    assert "remedy" not in cap


def test_probe_which_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    cap = doctor.probe_which("qmd", remedy="brew install qmd")
    assert cap["status"] == "missing"
    assert cap["remedy"] == "brew install qmd"


def test_probe_python_deps_all_present(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    cap = doctor.probe_python_deps(["ruamel.yaml", "otterai"])
    assert cap["status"] == "ok"


def test_probe_python_deps_missing(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda n: None if n == "otterai" else object())
    cap = doctor.probe_python_deps(["ruamel.yaml", "otterai"])
    assert cap["status"] == "degraded"
    assert "otterai" in cap["missing"]


def test_probe_python_deps_handles_find_spec_raising(monkeypatch):
    def fake_find_spec(n):
        if n == "ruamel.yaml":
            raise ModuleNotFoundError("No module named 'ruamel'")
        return object()
    monkeypatch.setattr(doctor.importlib.util, "find_spec", fake_find_spec)
    cap = doctor.probe_python_deps(["ruamel.yaml", "json"])
    assert cap["status"] == "degraded"
    assert "ruamel.yaml" in cap["missing"]


def test_probe_server_down(monkeypatch):
    # nothing listening on this port
    cap = doctor.probe_server(port=59999)
    assert cap["kind"] == "service"
    assert cap["status"] == "down"
    assert cap["port"] == 59999


def test_probe_transcript_not_expected(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: none\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["status"] == "not_expected"


def test_probe_transcript_needs_reauth_when_no_session(tmp_path, monkeypatch):
    # Deps present (Playwright + otterai installed) but no saved session yet.
    monkeypatch.setattr(doctor, "_dep_missing", lambda m: False)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: otter\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["provider"] == "otter"
    assert cap["status"] == "needs_reauth"  # no session.json present


def test_probe_transcript_otter_needs_setup_when_deps_missing(tmp_path, monkeypatch):
    # The Otter feed needs the transcript extras (Playwright + otterai) installed
    # before otter_auth.py can even run. The Doctor must surface that as a setup
    # step, NOT send the user to re-auth against a script that will crash on import.
    monkeypatch.setattr(doctor, "_dep_missing", lambda m: m in ("playwright", "otterai"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: otter\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["provider"] == "otter"
    assert cap["status"] == "needs_setup"
    assert "requirements-transcript.txt" in cap.get("remedy", "")
    assert "playwright" in cap.get("missing", [])


def test_probe_transcript_otter_external_feed_ok_by_marker(tmp_path, monkeypatch):
    # An adopted (external) Otter feed is verified by OUTPUT MARKER, not by a
    # Playwright/otterai dep probe — the extras may live only in the prior
    # install's venv. A present otter_sync.log means the feed is running.
    monkeypatch.setattr(doctor, "_dep_missing", lambda m: True)  # deps ABSENT
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "transcript:\n  provider: otter\n  external_feed: true\n")
    st = tmp_path / "profile" / "transcript"
    st.mkdir(parents=True)
    (st / "otter_sync.log").write_text("2026-07-01  INFO  synced\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["provider"] == "otter"
    assert cap["status"] == "ok"


def test_probe_transcript_otter_external_feed_no_marker_needs_reauth(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_dep_missing", lambda m: True)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "transcript:\n  provider: otter\n  external_feed: true\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["status"] == "needs_reauth"  # no marker yet


def test_probe_transcript_otter_no_external_flag_still_needs_setup(tmp_path, monkeypatch):
    # external_feed absent/false → the existing dep check runs UNCHANGED.
    monkeypatch.setattr(doctor, "_dep_missing", lambda m: m in ("playwright", "otterai"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "transcript:\n  provider: otter\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["status"] == "needs_setup"
    assert "playwright" in cap.get("missing", [])


def test_probe_transcript_granola_no_marker_needs_reauth(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: granola\n")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["provider"] == "granola"
    assert cap["status"] == "needs_reauth"
    assert "mcp-signup" in cap.get("detail", "")


def test_probe_transcript_granola_with_marker_ok(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: granola\n")
    st = tmp_path / "profile" / "transcript"
    st.mkdir(parents=True)
    (st / "granola_downloaded.json").write_text("{}")
    cap = doctor.probe_transcript(root=str(tmp_path))
    assert cap["status"] == "ok"


def test_detect_assembles_capabilities(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n"
        "transcript:\n  provider: none\n"
    )
    (tmp_path / "profile" / "config.yaml").write_text("server:\n  port: 59998\n")
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    caps = doctor.detect(root=str(tmp_path))
    assert caps["schema_version"] == 1
    assert "platform" in caps
    c = caps["capabilities"]
    assert c["qmd"]["status"] == "missing"
    assert c["msgraph_cli"]["required"] is False
    assert c["server"]["status"] == "down"
    # remote MCP seeded as expected from integrations.yaml
    assert c["jira"]["kind"] == "remote" and c["jira"]["expected"] is True
    # detect() persisted the file
    assert (tmp_path / "profile" / "capabilities.json").is_file()


def test_msgraph_remedy_is_a_real_install_command():
    # The msgraph_cli remedy must be a real macOS install route, not a placeholder.
    remedy = doctor._LOCAL_TOOLS["msgraph_cli"]["remedy"]
    assert "claude.ai/code install" not in remedy   # placeholder gone
    assert "mgc" in remedy or "msgraph" in remedy
    assert "# confirm" not in remedy   # internal note must not leak to users


def test_msgraph_remedy_includes_unified_login_scopes():
    # Onboarding/Doctor must capture the SEND scopes, not just calendar — one
    # login grants calendar + email + Teams. The remedy carries the full set.
    remedy = doctor._LOCAL_TOOLS["msgraph_cli"]["remedy"]
    assert "mgc login --scopes" in remedy
    assert "Mail.Send" in remedy and "Chat.ReadWrite" in remedy


def test_messaging_m365_seeds_a_remote_capability(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "messaging:\n  provider: m365\n")
    (tmp_path / "profile" / "config.yaml").write_text("server:\n  port: 59997\n")
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    caps = doctor.detect(root=str(tmp_path))["capabilities"]
    assert caps["m365"]["kind"] == "remote" and caps["m365"]["expected"] is True


def test_detect_preserves_stamped_remote_status(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n"
        "transcript:\n  provider: none\n"
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    # first detect seeds jira as remote/unknown
    caps1 = doctor.detect(root=str(tmp_path))
    assert caps1["capabilities"]["jira"]["status"] == "unknown"
    # Claude stamps jira ok + last_seen (simulate the workflow-doctor skill)
    doc = doctor.profile_lib.read_capabilities(root=str(tmp_path))
    doc["capabilities"]["jira"]["status"] = "ok"
    doc["capabilities"]["jira"]["last_seen"] = "2026-06-05"
    doctor.profile_lib.write_capabilities(doc, root=str(tmp_path))
    # re-detect must PRESERVE the stamp, not clobber back to unknown
    caps2 = doctor.detect(root=str(tmp_path))
    assert caps2["capabilities"]["jira"]["status"] == "ok"
    assert caps2["capabilities"]["jira"]["last_seen"] == "2026-06-05"


def test_detect_seeds_new_remote_as_unknown(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: jira\n"
        "transcript:\n  provider: none\n"
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    caps = doctor.detect(root=str(tmp_path))
    assert caps["capabilities"]["jira"]["status"] == "unknown"  # first-seen → unknown


def test_report_text_lists_caps(tmp_path):
    caps = {"schema_version": 1, "platform": "darwin", "capabilities": {
        "qmd": {"kind": "local", "status": "missing", "remedy": "brew install qmd"},
        "server": {"kind": "service", "status": "down", "port": 8742},
    }}
    text = doctor.report_text(caps)
    assert "qmd" in text and "missing" in text
    assert "brew install qmd" in text


def test_qmd_is_not_required(tmp_path):
    (tmp_path / "profile").mkdir()
    doc = doctor.detect(root=str(tmp_path))
    qmd = doc["capabilities"]["qmd"]
    assert qmd.get("required") is False


def test_recommended_tools_carry_rationale(tmp_path):
    # qmd/pandoc/mgc are strongly recommended (non-blocking) and must carry a
    # plain-language rationale the Doctor can surface — see _LOCAL_TOOLS.
    (tmp_path / "profile").mkdir()
    doc = doctor.detect(root=str(tmp_path))
    caps = doc["capabilities"]
    for name in ("qmd", "pandoc", "msgraph_cli"):
        assert caps[name].get("recommended") is True, name
        assert caps[name].get("rationale"), name


def test_qmd_remedy_points_at_correct_repo():
    # Guard against the wrong-qmd hallucination: the remedy must name the npm
    # package + tobi/qmd, NOT a brew formula. (Asserted against the source-of-
    # truth spec, since detect() omits remedy when the tool is already present.)
    remedy = doctor._LOCAL_TOOLS["qmd"]["remedy"]
    assert "@tobilu/qmd" in remedy
    assert "github.com/tobi/qmd" in remedy
    assert "brew install qmd" not in remedy


def test_report_text_surfaces_strong_recommendation():
    caps = {"schema_version": 1, "platform": "windows", "capabilities": {
        "qmd": {"kind": "local", "status": "missing", "recommended": True,
                "rationale": "the killer feature", "remedy": "npm install -g @tobilu/qmd"},
    }}
    text = doctor.report_text(caps)
    assert "STRONGLY RECOMMENDED" in text
    assert "the killer feature" in text


def test_check_exit_code(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: none\n")
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())
    doctor.detect(root=str(tmp_path))
    assert doctor.check("qmd", root=str(tmp_path)) == 1   # missing → nonzero
    assert doctor.check("python_deps", root=str(tmp_path)) == 0  # ok → zero
