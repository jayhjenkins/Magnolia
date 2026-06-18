"""Tests for the onboarding-complete marker in profile_lib.

The gate keys off a live profile/ existing AND config carrying `onboarded: true`
- NOT mere profile/ existence (meta-onboard creates profile/ early, at step 0).
A legacy migration stamps the marker on an already-populated install so it is
never re-gated into onboarding.

Every test uses a temp `root` so the real profile is never touched.
"""
import os

import profile_lib


def _mk_live_profile(root, *, identity_name="", onboarded=None):
    import shutil
    src = os.path.join(profile_lib.PM_OS_DIR, "profile.example")
    dst = os.path.join(root, "profile")
    shutil.copytree(src, dst)
    if identity_name or onboarded is not None:
        import ruamel.yaml
        y = ruamel.yaml.YAML()
        # config.yaml carries the onboarded flag
        cfgp = os.path.join(dst, "config.yaml")
        with open(cfgp) as fh:
            cfg = y.load(fh) or {}
        if onboarded is not None:
            cfg["onboarded"] = onboarded
        with open(cfgp, "w") as fh:
            y.dump(cfg, fh)
        if identity_name:
            pp = os.path.join(dst, "profile.yaml")
            with open(pp) as fh:
                prof = y.load(fh) or {}
            prof["display_name"] = identity_name
            with open(pp, "w") as fh:
                y.dump(prof, fh)


def test_not_complete_when_no_live_profile(tmp_path):
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is False


def test_complete_when_marker_set(tmp_path):
    _mk_live_profile(str(tmp_path), onboarded=True)
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_not_complete_when_profile_exists_without_marker(tmp_path):
    _mk_live_profile(str(tmp_path), onboarded=False)
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is False


def test_mark_onboarded_sets_the_flag(tmp_path):
    _mk_live_profile(str(tmp_path))
    profile_lib.mark_onboarded(root=str(tmp_path))
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_legacy_migration_stamps_populated_profile(tmp_path):
    # An existing install: real identity, no onboarded flag -> migration marks it.
    _mk_live_profile(str(tmp_path), identity_name="Real Person")
    changed = profile_lib.migrate_legacy_onboarded(root=str(tmp_path))
    assert changed is True
    assert profile_lib.onboarding_complete(root=str(tmp_path)) is True


def test_legacy_migration_skips_when_no_live_profile(tmp_path):
    assert profile_lib.migrate_legacy_onboarded(root=str(tmp_path)) is False
