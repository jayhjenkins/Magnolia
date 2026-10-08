"""Onboarding + doctor skills must not assume the operator's machine.

A coworker on Windows with Granola hit an onboarding flow that hardcoded ports,
led with Otter, and used macOS-only commands. These guards keep the skills
profile-driven and OS-neutral.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
ONBOARD = REPO / ".claude/skills/meta-onboard/SKILL.md"
DOCTOR = REPO / ".claude/skills/workflow-doctor/SKILL.md"


def _read(p):
    return p.read_text(encoding="utf-8")


def test_no_hardcoded_ports_in_onboarding_or_doctor():
    for p in (ONBOARD, DOCTOR):
        assert not re.search(r"\b87\d\d\b", _read(p)), f"{p.name} hardcodes a port"


def test_onboarding_bootstraps_via_profile_lib_not_cp():
    body = _read(ONBOARD)
    assert "profile_lib.py --bootstrap" in body
    assert "cp -R profile.example profile" not in body


def test_onboarding_never_starts_a_second_server():
    body = _read(ONBOARD)
    assert "server_lib.start" not in body
    assert "free_port" not in body
    assert "persist_lib.install" not in body


def test_granola_is_a_first_class_transcript_path():
    body = _read(ONBOARD)
    assert "python3 scripts/granola_sync.py" in body
    assert "paid" in body.lower()  # Granola transcripts need a paid plan - said up front
    assert "set_integration_provider('transcript', 'granola')" in body


def test_onboarding_asks_role_team_products():
    body = _read(ONBOARD)
    for field in ("'role'", "'team'", "'products'"):
        assert field in body


def test_onboarding_offers_every_starter_bundle_and_real_pack_list():
    body = _read(ONBOARD)
    assert "starter_sets.load_starter_sets" in body
    assert "packs_lib.pack_catalog" in body
    assert "voice/html.md" in body


def test_doctor_does_not_lead_with_otter_or_use_macos_only_commands_unlabelled():
    body = _read(DOCTOR)
    assert body.index("Granola") < body.index("Otter")
    assert "run_task_server.sh" not in body
