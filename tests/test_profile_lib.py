import os
import pytest
import profile_lib


def test_profile_dir_prefers_live_profile(profile_root):
    assert profile_lib.profile_dir(root=profile_root).endswith("/profile")


def test_profile_dir_falls_back_to_example(tmp_path):
    # No profile/ dir, but a profile.example/ exists
    (tmp_path / "profile.example").mkdir()
    assert profile_lib.profile_dir(root=str(tmp_path)).endswith("/profile.example")


def test_raw_loaders_return_dicts(profile_root):
    assert profile_lib.profile(root=profile_root)["display_name"] == "Test User"
    assert profile_lib.integrations(root=profile_root)["project_management"]["provider"] == "jira"
    assert profile_lib.config(root=profile_root)["models"]["judge"] == "opus"


def test_missing_file_returns_empty_dict(tmp_path):
    (tmp_path / "profile").mkdir()
    assert profile_lib.profile(root=str(tmp_path)) == {}


def test_identity_accessors(profile_root):
    assert profile_lib.display_name(root=profile_root) == "Test User"
    assert profile_lib.email(root=profile_root) == "test@example.com"
    assert profile_lib.company(root=profile_root) == "Acme"
    assert profile_lib.persona(root=profile_root) == "pm"


def test_eos_sheet_none_when_unconfigured(profile_root):
    # No eos block in the seeded profile -> sheet-watch sees no locator (blind).
    assert profile_lib.eos_sheet(root=profile_root) is None


def test_eos_sheet_returns_configured_locator(tmp_path):
    import textwrap
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "integrations.yaml").write_text(textwrap.dedent("""\
        eos:
          sheet: "sharepoint:PM-OS/EOS/scorecard.xlsx"
    """))
    assert profile_lib.eos_sheet(root=str(tmp_path)) == "sharepoint:PM-OS/EOS/scorecard.xlsx"


def test_identity_fallbacks_when_absent(tmp_path):
    (tmp_path / "profile").mkdir()
    assert profile_lib.display_name(root=str(tmp_path)) == "Operator"
    assert profile_lib.persona(root=str(tmp_path)) == "pm"


def test_integration_and_provider(profile_root):
    assert profile_lib.provider("transcript", root=profile_root) == "granola"
    assert profile_lib.provider("calendar", root=profile_root) == "m365"
    assert profile_lib.provider("nonexistent", root=profile_root) == "none"


def test_jira_config_when_jira(profile_root):
    jc = profile_lib.jira_config(root=profile_root)
    assert jc["cloud_id"] == "acme.atlassian.net"
    assert jc["project_key"] == "ACM"
    assert jc["default_assignee"] == "acct-123"


def test_jira_config_empty_when_not_jira(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: asana\n"
    )
    assert profile_lib.jira_config(root=str(tmp_path)) == {}


def test_model_accessor(profile_root):
    assert profile_lib.model("judge", root=profile_root) == "opus"
    assert profile_lib.model("missing", default="x", root=profile_root) == "x"


def test_voice_text_concatenates_channels(profile_root):
    txt = profile_lib.voice_text(root=profile_root)
    assert "Teams voice" in txt
    assert "Email voice" in txt


def test_voice_text_single_channel(profile_root):
    assert "Teams voice" in profile_lib.voice_text("teams", root=profile_root)
    assert "Email voice" not in profile_lib.voice_text("teams", root=profile_root)


def test_voice_text_falls_back_to_example(tmp_path):
    # Only profile.example/ exists (no live profile/) -> voice still resolves.
    ex = tmp_path / "profile.example" / "voice"
    ex.mkdir(parents=True)
    (ex / "teams.md").write_text("# Example teams voice\n")
    (ex / "email.md").write_text("# Example email voice\n")
    txt = profile_lib.voice_text(root=str(tmp_path))
    assert "Example teams voice" in txt
    assert "Example email voice" in txt


def test_loader_handles_comments_only_file(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.yaml").write_text("# only a comment, no keys\n")
    assert profile_lib.profile(root=str(tmp_path)) == {}
    assert profile_lib.display_name(root=str(tmp_path)) == "Operator"


def test_provider_handles_null_value(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("project_management:\n")
    assert profile_lib.provider("project_management", root=str(tmp_path)) == "none"
    assert profile_lib.jira_config(root=str(tmp_path)) == {}


def test_server_port_default(tmp_path):
    (tmp_path / "profile").mkdir()
    assert profile_lib.server_port(root=str(tmp_path)) == 8742


def test_server_port_from_config(profile_root):
    # profile_root fixture defines a server block with port 8755
    assert profile_lib.server_port(root=profile_root) == 8755


def test_configured_server_port_none_when_unset(tmp_path):
    # No server.port in config -> None (distinct from server_port()'s 8742 default)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "config.yaml").write_text("models:\n  judge: x\n")
    assert profile_lib.configured_server_port(root=str(tmp_path)) is None


def test_configured_server_port_returns_int(profile_root):
    # fixture config has server.port: 8755
    assert profile_lib.configured_server_port(root=profile_root) == 8755
    assert isinstance(profile_lib.configured_server_port(root=profile_root), int)


def test_configured_server_port_ignores_example_fallback(tmp_path):
    # Regression: with NO live profile/, profile_dir() falls back to the shipped
    # profile.example/ (which ships server.port: 8742). configured_server_port
    # must NOT treat that template port as an explicit operator choice, or a fresh
    # install would skip the launcher's auto-free-port hunt and collide on 8742.
    (tmp_path / "profile.example").mkdir()
    (tmp_path / "profile.example" / "config.yaml").write_text("server:\n  port: 8742\n")
    # no live profile/ dir on purpose
    assert profile_lib.configured_server_port(root=str(tmp_path)) is None


def test_set_server_port_writes_config(profile_root):
    profile_lib.set_server_port(8761, root=profile_root)
    assert profile_lib.configured_server_port(root=profile_root) == 8761
    # sibling server keys / other top-level keys preserved
    assert profile_lib.config(root=profile_root)["active_skill_packs"] == ["core", "pm"]


def test_set_server_port_creates_live_profile_dir(tmp_path):
    # No live profile/ dir exists yet; set_server_port must create it and write
    # there, never falling back to profile.example.
    root = str(tmp_path)
    assert not (tmp_path / "profile").exists()
    profile_lib.set_server_port(8762, root=root)
    assert (tmp_path / "profile" / "config.yaml").exists()
    assert profile_lib.configured_server_port(root=root) == 8762


def test_set_server_port_does_not_touch_example(tmp_path):
    # A tracked profile.example/config.yaml must stay untouched (the footgun).
    root = str(tmp_path)
    ex = tmp_path / "profile.example"
    ex.mkdir()
    (ex / "config.yaml").write_text("server:\n  port: 8742\n")
    before = (ex / "config.yaml").read_text()
    profile_lib.set_server_port(8799, root=root)
    assert (ex / "config.yaml").read_text() == before
    # the write landed in the live profile/ instead
    assert profile_lib.configured_server_port(root=root) == 8799


def test_transcript_config_defaults(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text("transcript:\n  provider: otter\n")
    tc = profile_lib.transcript_config(root=str(tmp_path))
    assert tc["provider"] == "otter"
    assert tc["target"] == "datasets/meetings/"  # default applied


def test_transcript_dir_under_profile(tmp_path):
    (tmp_path / "profile").mkdir()
    d = profile_lib.transcript_state_dir(root=str(tmp_path))
    assert d.endswith("/profile/transcript")


def test_doc_sync_config_from_integrations(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "doc_sync:\n"
        "  onedrive_root: \"~/Library/CloudStorage/OneDrive-Acme\"\n"
        "  sharepoint_site: \"PM-OS\"\n"
        "  enabled: true\n"
    )
    dc = profile_lib.doc_sync_config(root=str(tmp_path))
    assert dc["sharepoint_site"] == "PM-OS"
    assert dc["enabled"] is True


def test_doc_sync_config_defaults_disabled(tmp_path):
    (tmp_path / "profile").mkdir()
    assert profile_lib.doc_sync_config(root=str(tmp_path))["enabled"] is False


def test_pendo_config_reads_from_profile(profile_root):
    import profile_lib
    p = os.path.join(profile_root, "profile", "integrations.yaml")
    with open(p, "a") as f:
        f.write("analytics:\n  pendo:\n    provider: pendo\n"
                "    subscription_id: '123'\n    app_ids: {web: 'a1'}\n")
    cfg = profile_lib.pendo_config(root=profile_root)
    assert cfg["subscription_id"] == "123"
    assert cfg["app_ids"]["web"] == "a1"


def test_databricks_config_defaults_empty(profile_root):
    import profile_lib
    cfg = profile_lib.databricks_config(root=profile_root)
    assert cfg["catalog"] == ""
    assert cfg["sources"] == {}


# --- Write helpers (Phase 6, Task 4.1) ---


def test_write_identity_roundtrips(profile_root):
    profile_lib.write_identity({"display_name": "Jay", "email": "jay@v.com",
                                "company": "Vantaca", "timezone": "America/Chicago"},
                               root=profile_root)
    p = profile_lib.profile(root=profile_root)
    assert p["display_name"] == "Jay" and p["company"] == "Vantaca"
    assert p["timezone"] == "America/Chicago"
    assert p["persona"] == "pm"           # untouched field preserved


def test_write_voice_per_channel(profile_root):
    profile_lib.write_voice("teams", "tight and lowercase", root=profile_root)
    assert "tight and lowercase" in profile_lib.voice_text("teams", root=profile_root)
    assert "Warm" in profile_lib.voice_text("email", root=profile_root)   # other channel untouched


def test_set_integration_provider(profile_root):
    profile_lib.set_integration_provider("transcript", "otter", root=profile_root)
    assert profile_lib.provider("transcript", root=profile_root) == "otter"
    # other categories untouched
    assert profile_lib.provider("project_management", root=profile_root) == "jira"


def test_set_active_packs(profile_root):
    profile_lib.set_active_packs(["core", "exec"], root=profile_root)
    assert profile_lib.config(root=profile_root)["active_skill_packs"] == ["core", "exec"]


def test_set_cost_posture(profile_root):
    profile_lib.set_cost_posture("high", root=profile_root)
    assert profile_lib.config(root=profile_root)["models"]["cost_posture"] == "high"
    # sibling model keys preserved
    assert profile_lib.config(root=profile_root)["models"]["judge"] == "opus"


def test_write_preserves_yaml_comments(profile_root):
    # round-trip writer must keep the helpful comments in the file.
    # The fixture's config.yaml has no comment, so seed one first, then prove
    # it survives an unrelated write (set_cost_posture mutates a different key).
    cfg_path = os.path.join(profile_lib.profile_dir(root=profile_root), "config.yaml")
    with open(cfg_path) as f:
        original = f.read()
    with open(cfg_path, "w") as f:
        f.write("# cost_posture controls model spend\n" + original)

    profile_lib.set_cost_posture("low", root=profile_root)

    with open(cfg_path) as f:
        text = f.read()
    assert "# cost_posture controls model spend" in text  # comment survived the write
    assert "#" in text   # at least one comment survived the write


@pytest.mark.parametrize("tier,posture,expected", [
    ("light",    "low",      "haiku"),
    ("light",    "balanced", "haiku"),
    ("light",    "high",     "sonnet"),
    ("standard", "low",      "haiku"),
    ("standard", "balanced", "sonnet"),
    ("standard", "high",     "opus"),
    ("deep",     "low",      "sonnet"),
    ("deep",     "balanced", "opus"),
    ("deep",     "high",     "opus"),
])
def test_resolve_model_matrix(tier, posture, expected):
    assert profile_lib.resolve_model(tier, posture=posture) == expected


def test_resolve_model_override_by_model_id_wins():
    assert profile_lib.resolve_model("light", posture="low",
                                     task_override="opus") == "opus"


def test_resolve_model_override_by_tier_name_wins():
    assert profile_lib.resolve_model("light", posture="low", task_override="deep") == "opus"


def test_resolve_model_defaults_tier_standard_and_posture_balanced():
    # Pin posture explicitly — resolve_model(None) with posture=None reads the
    # LIVE profile/config.yaml cost_posture (gitignored, per-machine), so an
    # implicit call is env-coupled and fails wherever the operator set a non-
    # balanced posture. We assert the default-tier mapping, not the machine's
    # posture.
    assert profile_lib.resolve_model(None, posture="balanced") == "sonnet"
    assert profile_lib.resolve_model("bogus", posture="bogus") == "sonnet"


def test_resolve_model_reads_posture_from_config(profile_root):
    # profile_root config has cost_posture: balanced -> deep worker => opus
    assert profile_lib.resolve_model("deep", root=profile_root) == "opus"


def test_resolve_model_override_arbitrary_model_id_passthrough():
    # A non-tier override string is returned verbatim (raw model id wins).
    assert profile_lib.resolve_model("light", posture="low",
                                     task_override="some-future-model") == "some-future-model"


def test_cost_posture_reads_and_defaults(profile_root, tmp_path):
    assert profile_lib.cost_posture(root=profile_root) == "balanced"   # from fixture config
    # missing config -> default 'balanced'
    assert profile_lib.cost_posture(root=str(tmp_path)) == "balanced"
