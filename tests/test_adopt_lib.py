import os

import adopt_lib
import platform_lib
import profile_lib


# ── helpers ──────────────────────────────────────────────────────────────────

def _write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _fake_agent_plist(la_dir, prior_scripts, python_path, meetings_dir_literal):
    """Write a fake prior otter-sync LaunchAgent + its script, return the plist path.

    If meetings_dir_literal is a str, the script carries a MEETINGS_DIR = "..."
    literal; if None, the script computes it (no recoverable hint)."""
    os.makedirs(la_dir, exist_ok=True)
    os.makedirs(prior_scripts, exist_ok=True)
    script = os.path.join(prior_scripts, "otter_sync.py")
    if meetings_dir_literal is None:
        _write(script, "from pathlib import Path\nMEETINGS_DIR = Path(__file__).parent / 'x'\n")
    else:
        _write(script, 'MEETINGS_DIR = "%s"\n' % meetings_dir_literal)
    plist = os.path.join(la_dir, "com.priortool.otter-sync.plist")
    _write(plist,
           "<plist><dict><key>Label</key>"
           "<string>com.priortool.otter-sync</string>"
           "<key>ProgramArguments</key><array>"
           "<string>%s</string><string>%s</string>"
           "</array></dict></plist>" % (python_path, script))
    return plist


# ── detect_meetings_candidates ────────────────────────────────────────────────

def test_detect_from_plist_parses_script_python_and_hint(tmp_path):
    la = tmp_path / "LaunchAgents"
    prior_scripts = tmp_path / "old" / "scripts"
    meetings = tmp_path / "old" / "datasets" / "meetings" / "2025-09"
    _write(str(meetings / "a.txt"), "hi")
    _write(str(meetings / "b.md"), "# note")
    py = str(tmp_path / "old" / ".venv" / "bin" / "python")
    _fake_agent_plist(str(la), str(prior_scripts), py,
                      str(tmp_path / "old" / "datasets" / "meetings"))
    # Use home=tmp so no real common dirs leak in.
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la),
                                                 home=str(tmp_path / "nohome"))
    la_hits = [c for c in cands if c["provenance"] == "launchagent"]
    assert len(la_hits) == 1
    hit = la_hits[0]
    assert hit["agent"]["python"] == py
    assert hit["agent"]["script"].endswith("otter_sync.py")
    assert hit["agent"]["meetings_hint"] == str(tmp_path / "old" / "datasets" / "meetings")
    assert hit["exists"] is True
    assert hit["txt_count"] == 2  # a.txt + b.md


def test_detect_falls_back_to_venv_python_when_arg0_not_python(tmp_path):
    la = tmp_path / "LaunchAgents"
    prior_scripts = tmp_path / "old" / "scripts"
    os.makedirs(str(la))
    script = _write(str(prior_scripts / "otter_sync.py"), 'MEETINGS_DIR = "/tmp/m"\n')
    # arg0 is NOT a python interpreter → fall back to <script_dir>/.venv/bin/python
    _write(str(la / "com.priortool.otter-sync.plist"),
           "<plist><dict><key>Label</key><string>com.priortool.otter-sync</string>"
           "<key>ProgramArguments</key><array>"
           "<string>%s</string></array></dict></plist>" % script)
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la),
                                                 home=str(tmp_path / "nohome"))
    hit = [c for c in cands if c["provenance"] == "launchagent"][0]
    assert hit["agent"]["python"] == os.path.join(str(prior_scripts), ".venv", "bin", "python")


def test_detect_from_common_dir(tmp_path):
    home = tmp_path / "home"
    meetings = home / "pm-os" / "datasets" / "meetings" / "2025-09"
    _write(str(meetings / "x.txt"), "content")
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la), home=str(home))
    common = [c for c in cands if c["provenance"] == "common" and c["exists"]]
    assert len(common) == 1
    assert common[0]["txt_count"] == 1
    assert common[0]["path"] == str(home / "pm-os" / "datasets" / "meetings")


def test_detect_no_prior_install(tmp_path):
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la),
                                                 home=str(tmp_path / "empty"))
    # No launchagent hits; common dirs all non-existent.
    assert [c for c in cands if c["provenance"] == "launchagent"] == []
    assert all(c["exists"] is False for c in cands)


def test_detect_malformed_plist_degrades_without_crashing(tmp_path):
    # A garbage plist must yield a hint-less candidate, never raise.
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    # feed_guard only surfaces plists whose text signals a transcript downloader.
    _write(str(la / "com.priortool.otter-sync.plist"), "not xml at all: otter sync junk {{{")
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la),
                                                 home=str(tmp_path / "nohome"))
    hit = [c for c in cands if c["provenance"] == "launchagent"][0]
    assert hit["agent"]["meetings_hint"] is None
    assert hit["agent"]["script"] is None
    assert hit["path"] is None


def test_detect_computed_path_script_hint_is_none(tmp_path):
    la = tmp_path / "LaunchAgents"
    prior_scripts = tmp_path / "old" / "scripts"
    py = str(tmp_path / "old" / ".venv" / "bin" / "python3")
    _fake_agent_plist(str(la), str(prior_scripts), py, meetings_dir_literal=None)
    cands = adopt_lib.detect_meetings_candidates(launch_agents_dir=str(la),
                                                 home=str(tmp_path / "nohome"))
    hit = [c for c in cands if c["provenance"] == "launchagent"][0]
    assert hit["agent"]["meetings_hint"] is None  # computed path → no crash, no hint
    assert hit["path"] is None
    assert hit["exists"] is False


# ── adopt_meetings ────────────────────────────────────────────────────────────

def _install_root(tmp_path):
    """A minimal Magnolia install root with a live profile + integrations.yaml."""
    root = tmp_path / "magnolia"
    _write(str(root / "profile" / "integrations.yaml"),
           "transcript:\n  provider: otter\n  target: datasets/meetings/\n")
    return root


def test_adopt_copies_tree_and_target_derivation(tmp_path):
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "2025-09" / "a.txt"), "alpha")
    _write(str(src / "2025-10" / "b.md"), "# beta")
    result = adopt_lib.adopt_meetings(str(src), root=str(root))
    assert result["copied"] == 2
    assert result["skipped"] == 0
    assert result["target"] == os.path.abspath(str(root / "datasets" / "meetings"))
    assert os.path.isfile(str(root / "datasets" / "meetings" / "2025-09" / "a.txt"))


def test_adopt_non_destructive_keeps_existing_content(tmp_path):
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "a.txt"), "NEW")
    # A pre-existing dest file with different content must be preserved (skipped).
    _write(str(root / "datasets" / "meetings" / "a.txt"), "ORIGINAL")
    result = adopt_lib.adopt_meetings(str(src), root=str(root))
    assert result["copied"] == 0
    assert result["skipped"] == 1
    with open(str(root / "datasets" / "meetings" / "a.txt")) as f:
        assert f.read() == "ORIGINAL"  # never clobbered


def test_adopt_is_idempotent(tmp_path):
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "a.txt"), "x")
    first = adopt_lib.adopt_meetings(str(src), root=str(root))
    second = adopt_lib.adopt_meetings(str(src), root=str(root))
    assert first["copied"] == 1
    assert second["copied"] == 0  # 2nd run copies nothing
    assert second["skipped"] == 1


def test_adopt_never_creates_symlink(tmp_path):
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "a.txt"), "x")
    adopt_lib.adopt_meetings(str(src), root=str(root))
    dest = str(root / "datasets" / "meetings" / "a.txt")
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)  # COPY, never symlink — the bug being fixed


def test_adopt_skips_symlinked_source_file(tmp_path):
    # The prior tree is not fully trusted: a symlink in the corpus must NOT be
    # dereferenced (which would copy the link target's content into Magnolia).
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "real.txt"), "real")
    secret = _write(str(tmp_path / "secret.txt"), "SECRET")
    os.symlink(secret, str(src / "link.txt"))
    result = adopt_lib.adopt_meetings(str(src), root=str(root))
    assert result["copied"] == 1                    # only real.txt
    assert result["skipped"] == 1                   # link.txt skipped, not chased
    assert not os.path.exists(str(root / "datasets" / "meetings" / "link.txt"))


def test_adopt_copy_onto_self_is_noop(tmp_path):
    root = _install_root(tmp_path)
    target = root / "datasets" / "meetings"
    _write(str(target / "a.txt"), "x")
    result = adopt_lib.adopt_meetings(str(target), root=str(root))
    assert result["copied"] == 0
    assert result["skipped"] == 0


def test_adopt_also_skills_records_diverged(tmp_path):
    root = _install_root(tmp_path)
    src = tmp_path / "old" / "datasets" / "meetings"
    _write(str(src / "a.txt"), "x")
    prior_root = tmp_path / "old"
    # An engine-existing skill (must NOT be copied → diverged) and a novel one.
    _write(str(root / ".claude" / "skills" / "context-search" / "SKILL.md"), "engine")
    _write(str(prior_root / ".claude" / "skills" / "context-search" / "SKILL.md"), "prior")
    _write(str(prior_root / ".claude" / "skills" / "my-custom-skill" / "SKILL.md"), "mine")
    result = adopt_lib.adopt_meetings(str(src), root=str(root), also=["skills"])
    assert "context-search" in result["extras"]["skills_diverged"]
    assert result["extras"]["skills"]["copied"] == 1  # only the novel skill's file
    # The engine skill was left untouched.
    with open(str(root / ".claude" / "skills" / "context-search" / "SKILL.md")) as f:
        assert f.read() == "engine"
    assert os.path.isfile(str(root / ".claude" / "skills" / "my-custom-skill" / "SKILL.md"))


# ── redirect_otter_feed ───────────────────────────────────────────────────────

def _magnolia_root_with_template(tmp_path):
    """A root that carries the real otter plist template + a live profile."""
    root = tmp_path / "magnolia"
    src_tpl = os.path.join(profile_lib.PM_OS_DIR, "scripts", "templates",
                           "transcript-otter-sync.plist.template")
    with open(src_tpl, encoding="utf-8") as f:
        tpl = f.read()
    _write(os.path.join(str(root), "scripts", "templates",
                        "transcript-otter-sync.plist.template"), tpl)
    _write(str(root / "profile" / "integrations.yaml"),
           "transcript:\n  provider: otter\n")
    return root


def test_redirect_off_darwin_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    agent = {"path": str(tmp_path / "old.plist"), "python": "/x/python"}
    result = adopt_lib.redirect_otter_feed(agent, root=str(tmp_path),
                                           launch_agents_dir=str(la))
    assert result["supported"] is False
    assert "macOS" in result["reason"]
    assert os.listdir(str(la)) == []  # NOTHING written


def test_redirect_on_darwin_writes_plist_and_disables_old(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    root = _magnolia_root_with_template(tmp_path)
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    old_plist = _write(str(la / "com.priortool.otter-sync.plist"), "<plist/>")

    disabled_calls = []
    orig_disable = adopt_lib.feed_guard.disable

    def spy_disable(path, activate=True):
        disabled_calls.append(path)
        return orig_disable(path, activate=activate)

    monkeypatch.setattr(adopt_lib.feed_guard, "disable", spy_disable)

    # Stub venv creation and smoke test (can't pip install in test)
    fake_py = str(root / "venv" / "bin" / "python3")
    os.makedirs(os.path.dirname(fake_py), exist_ok=True)
    _write(fake_py, "")
    monkeypatch.setattr(adopt_lib.ensure_venv, "ensure", lambda root=None: fake_py)
    monkeypatch.setattr(adopt_lib, "_smoke_test_python", lambda p: None)

    agent = {"path": old_plist, "python": "/Users/x/old/.venv/bin/python"}
    # activate=False skips launchctl (no real load in tests).
    result = adopt_lib.redirect_otter_feed(agent, root=str(root),
                                           launch_agents_dir=str(la), activate=False)
    assert result["supported"] is True
    assert result["label"] == "com.magnolia.ottersync"
    plist_path = os.path.join(str(la), "com.magnolia.ottersync.plist")
    assert result["plist"] == plist_path
    assert os.path.isfile(plist_path)
    with open(plist_path) as f:
        text = f.read()
    assert "com.magnolia.ottersync" in text
    # Magnolia's own venv python is in ProgramArguments (not the old install's).
    assert fake_py in text
    # feed_guard.disable was invoked on the OLD plist.
    assert old_plist in disabled_calls
    # the external-feed flag is now set in the profile.
    assert profile_lib.transcript_config(str(root))["external_feed"] is True
    # state_files key is present in result.
    assert "state_files" in result


def test_redirect_missing_template_leaves_old_agent_in_place(tmp_path, monkeypatch):
    # The template is read BEFORE the old agent is disabled, so a root lacking
    # the template must fail without having touched the prior agent.
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    root = tmp_path / "magnolia"  # no template written under here
    _write(str(root / "profile" / "integrations.yaml"), "transcript:\n  provider: otter\n")
    la = tmp_path / "LaunchAgents"
    os.makedirs(str(la))
    old_plist = _write(str(la / "com.priortool.otter-sync.plist"), "<plist/>")
    monkeypatch.setattr(adopt_lib.ensure_venv, "ensure", lambda root=None: "/x/python")
    monkeypatch.setattr(adopt_lib, "_smoke_test_python", lambda p: None)
    agent = {"path": old_plist, "python": "/x/python"}
    try:
        adopt_lib.redirect_otter_feed(agent, root=str(root),
                                      launch_agents_dir=str(la), activate=False)
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised
    assert os.path.isfile(old_plist)  # prior agent untouched on failure
    assert not os.path.isfile(os.path.join(str(la), "com.magnolia.ottersync.plist"))
