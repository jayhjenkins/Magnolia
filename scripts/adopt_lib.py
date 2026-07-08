"""Adopt a prior (being-retired) PM-OS install into a fresh Magnolia install.

Two jobs the onboarding concierge drives through this module:

  1. Clone the user's historical transcripts (and, opt-in, sibling subtrees) into
     Magnolia's target dir. COPY only, never symlink; never clobber an existing
     destination file (invariant #6). Re-runs are idempotent.
  2. Reuse the user's already-working Otter feed by RE-POINTING the old
     LaunchAgent at Magnolia's otter_sync.py (macOS only) instead of making them
     reinstall Playwright / otterai.

Discipline (mirrors scripts/feed_guard.py): pure/read-only DETECTION here; no
network, no launchctl at import time. All OS logic routes through platform_lib
(portability_gate scans this file). Detection never executes the old script.
"""
import os
import re
import shutil

import feed_guard
import platform_lib
import profile_lib

# Magnolia's OWN transcript LaunchAgents — excluded from "prior install" detection.
# (Matches the generic labels the granola/otter installers write.)
_MAGNOLIA_LABELS = {"com.magnolia.ottersync", "com.magnolia.granolasync"}

# Common prior-install roots to probe under $HOME. `pm-os` is a candidate to
# PROBE only — never a hardcoded identity assumption (invariant #1).
_COMMON_ROOTS = ["pm-os", "pm_os", ".pm-os", "PM-OS"]

# Best-effort recovery of a MEETINGS_DIR = "..." literal from a prior sync script.
_MEETINGS_DIR_RE = re.compile(r"""MEETINGS_DIR\s*=\s*(?:Path\()?\s*["']([^"']+)["']""")
# Extract each <string>...</string> from a plist's ProgramArguments (best-effort).
_STRING_RE = re.compile(r"<string>([^<]*)</string>")


def _txt_count(root):
    """Count *.txt and *.md recursively under root (best-effort, 0 on any error)."""
    n = 0
    try:
        for _dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith(".txt") or fn.endswith(".md"):
                    n += 1
    except OSError:
        pass
    return n


def _parse_agent(path):
    """Parse a prior sync LaunchAgent plist into {label, path, script, python,
    meetings_hint}. Best-effort; degrades to None fields, never executes anything."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        text = ""
    m = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", text)
    label = m.group(1) if m else os.path.basename(path)[:-6]

    # ProgramArguments: interpreter is arg[0] if it looks like a python; the
    # script is the first arg ending in .py. Scope the <string> scan to the
    # ProgramArguments <array> so the Label string is not mistaken for arg[0].
    am = re.search(r"<key>ProgramArguments</key>\s*<array>(.*?)</array>", text, re.DOTALL)
    args = _STRING_RE.findall(am.group(1)) if am else []
    script = next((a for a in args if a.endswith(".py")), None)
    python = None
    if args:
        first = args[0]
        base = os.path.basename(first)
        if base.startswith("python"):
            python = first
    if python is None and script:
        # Fall back to a venv interpreter alongside the script.
        python = os.path.join(os.path.dirname(script), ".venv", "bin", "python")

    # LOW-CONFIDENCE meetings hint: a literal MEETINGS_DIR assignment in the
    # script. Unreadable script or a computed path -> None (degrade, never run it).
    meetings_hint = None
    if script and os.path.isfile(script):
        try:
            with open(script, encoding="utf-8", errors="ignore") as f:
                stext = f.read()
        except OSError:
            stext = ""
        hm = _MEETINGS_DIR_RE.search(stext)
        if hm:
            meetings_hint = os.path.expanduser(hm.group(1))

    return {"label": label, "path": path, "script": script,
            "python": python, "meetings_hint": meetings_hint}


def detect_meetings_candidates(launch_agents_dir=None, home=None):
    """Read-only. Discover where a prior install's transcripts likely live.

    Returns a list of dicts, richest provenance first:
      {"path", "provenance": "launchagent"|"common", "exists", "txt_count", "agent"}
    where agent is the parsed LaunchAgent dict (launchagent source) or None.
    Detection never acts and never executes the old sync script.
    """
    candidates = []

    # Source (a): competing transcript LaunchAgents (prior install's feed).
    for hit in feed_guard.detect_competing(launch_agents_dir, own_labels=_MAGNOLIA_LABELS):
        agent = _parse_agent(hit["path"])
        path = agent["meetings_hint"]  # None when unreadable/computed — caller can ask.
        candidates.append({
            "path": path,
            "provenance": "launchagent",
            "exists": bool(path) and os.path.isdir(path),
            "txt_count": _txt_count(path) if path and os.path.isdir(path) else 0,
            "agent": agent,
        })

    # Source (b): common candidate roots under $HOME.
    base = home or os.path.expanduser("~")
    for root in _COMMON_ROOTS:
        cand = os.path.join(base, root, "datasets", "meetings")
        candidates.append({
            "path": cand,
            "provenance": "common",
            "exists": os.path.isdir(cand),
            "txt_count": _txt_count(cand) if os.path.isdir(cand) else 0,
            "agent": None,
        })

    # Dedup by realpath (richest provenance wins — launchagent entries come first).
    seen = set()
    deduped = []
    for c in candidates:
        if c["path"] is None:
            deduped.append(c)  # a hint-less launchagent is still worth surfacing
            continue
        # Dedup by identity. When the path exists, use (st_dev, st_ino) so
        # case-insensitive filesystems (macOS/Windows) collapse pm-os vs PM-OS
        # (they resolve to one real dir); otherwise fall back to the realpath
        # string so non-existent variants still de-duplicate on exact repeats.
        rp = os.path.realpath(c["path"])
        try:
            st = os.stat(rp)
            key = ("inode", st.st_dev, st.st_ino)
        except OSError:
            key = ("path", os.path.normcase(rp))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _clone_tree(src, dst):
    """Copy every file under src into dst, recreating structure. Returns
    {"copied", "skipped"}. COPY only (shutil.copy2, preserving mtimes); NEVER a
    symlink; NEVER overwrites an existing dest file (invariant #6 — non-destructive,
    idempotent). Missing src -> a no-op."""
    copied = 0
    skipped = 0
    if not os.path.isdir(src):
        return {"copied": 0, "skipped": 0}
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        for fn in filenames:
            src_file = os.path.join(dirpath, fn)
            dst_file = os.path.join(target_dir, fn)
            # Skip a symlinked source file rather than dereference it: the prior
            # install's tree is not fully trusted, and copy2 would follow the link
            # and write the LINK TARGET's content (which may point outside the
            # tree) into Magnolia. We only ever clone real files in the corpus.
            if os.path.islink(src_file):
                skipped += 1
                continue
            if os.path.exists(dst_file):
                skipped += 1
                continue
            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1
    return {"copied": copied, "skipped": skipped}


def _prior_root(src_meetings):
    """Derive the prior install root from its datasets/meetings dir (up two levels)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(src_meetings)))


def _engine_skill_dirs(root):
    """Set of skill folder names already present in this engine's .claude/skills/."""
    skills_dir = os.path.join(root or profile_lib.PM_OS_DIR, ".claude", "skills")
    try:
        return {d for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))}
    except OSError:
        return set()


def adopt_meetings(src_meetings, root=None, also=None):
    """Clone a prior datasets/meetings tree into Magnolia's target, plus optional
    sibling subtrees. COPY only, never symlink; never clobbers; idempotent.

    Returns {"copied", "skipped", "target", "extras": {...}}.
    """
    base = root or profile_lib.PM_OS_DIR
    target = os.path.join(base, profile_lib.transcript_config(root)["target"])
    extras = {}

    # Copy-onto-self guard: adopting a tree onto itself is a no-op.
    if os.path.realpath(src_meetings) == os.path.realpath(target):
        return {"copied": 0, "skipped": 0, "target": os.path.abspath(target), "extras": extras}

    result = _clone_tree(src_meetings, target)

    if also:
        prior_root = _prior_root(src_meetings)
        for name in also:
            if name in ("tasks", "research"):
                src = os.path.join(prior_root, "datasets", name)
                dst = os.path.join(base, "datasets", name)
                extras[name] = _clone_tree(src, dst)
            elif name == "voice":
                src = os.path.join(prior_root, "profile", "voice")
                dst = os.path.join(profile_lib.profile_dir(root), "voice")
                extras["voice"] = _clone_tree(src, dst)
            elif name == "skills":
                src = os.path.join(prior_root, ".claude", "skills")
                dst = os.path.join(base, ".claude", "skills")
                existing = _engine_skill_dirs(root)
                copied = 0
                skipped = 0
                diverged = []
                try:
                    skill_dirs = sorted(
                        d for d in os.listdir(src)
                        if os.path.isdir(os.path.join(src, d)))
                except OSError:
                    skill_dirs = []
                for d in skill_dirs:
                    if d in existing:
                        # Engine already ships this skill — never copy over it.
                        diverged.append(d)
                        continue
                    sub = _clone_tree(os.path.join(src, d), os.path.join(dst, d))
                    copied += sub["copied"]
                    skipped += sub["skipped"]
                extras["skills"] = {"copied": copied, "skipped": skipped}
                extras["skills_diverged"] = diverged

    return {
        "copied": result["copied"],
        "skipped": result["skipped"],
        "target": os.path.abspath(target),
        "extras": extras,
    }


def _xml_escape(s):
    """Escape a value for safe insertion into a plist <string>. Paths almost
    never contain these, but the values come from a parsed plist / detected
    paths, so escape defensively to keep the rendered plist well-formed."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _weekday_hourly_interval_block():
    """Build the weekday (Mon-Fri) hourly 9..17 StartCalendarInterval block —
    the same cadence the granola installer writes, in pure Python."""
    lines = []
    for weekday in range(1, 6):
        for hour in range(9, 18):
            lines.append(
                "        <dict><key>Weekday</key><integer>{}</integer>"
                "<key>Hour</key><integer>{}</integer>"
                "<key>Minute</key><integer>0</integer></dict>".format(weekday, hour))
    return "\n".join(lines)


def _launchctl_load(plist_path):
    """Load a LaunchAgent via launchctl. Isolated so tests can monkeypatch it;
    only ever called on darwin from redirect_otter_feed."""
    import subprocess
    subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
    subprocess.run(["launchctl", "load", plist_path], capture_output=True)


def redirect_otter_feed(agent, root=None, launch_agents_dir=None, activate=True):
    """Re-point a prior Otter feed at Magnolia's otter_sync.py (macOS only).

    Sequence: disable the OLD agent first (feed_guard.disable), then render +
    write Magnolia's plist and (if activate) launchctl-load it, then set the
    external-feed flag so the Doctor stops nagging.

    Returns {"supported": True, "plist", "label", "python", "disabled"} on darwin,
    or {"supported": False, "reason": ...} off darwin (writing NOTHING).
    """
    if platform_lib.os_kind() != "darwin":
        return {"supported": False,
                "reason": "LaunchAgents are macOS-only; on Windows the live feed "
                          "is a manual follow-up"}

    base = root or profile_lib.PM_OS_DIR
    label = "com.magnolia.ottersync"
    python = agent.get("python") or "python3"
    la_dir = launch_agents_dir or platform_lib.launch_agents_dir()
    plist_path = os.path.join(la_dir, label + ".plist")

    template_path = os.path.join(base, "scripts", "templates",
                                 "transcript-otter-sync.plist.template")
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    rendered = (template
                .replace("__LABEL__", label)
                .replace("__PYTHON__", _xml_escape(python))
                .replace("__PM_OS_DIR__", _xml_escape(base))
                .replace("__INTERVAL_BLOCK__", _weekday_hourly_interval_block()))

    # Disable the OLD agent FIRST (rename aside; launchctl unload gated on activate).
    disabled = feed_guard.disable(agent["path"], activate=activate)

    # Then write Magnolia's plist and load it.
    os.makedirs(la_dir, exist_ok=True)
    os.makedirs(os.path.join(base, "logs"), exist_ok=True)
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    if activate:
        _launchctl_load(plist_path)

    # Flip the external-feed flag so the Doctor verifies by output marker, not deps.
    profile_lib.set_transcript_external(True, root)

    return {"supported": True, "plist": plist_path, "label": label,
            "python": python, "disabled": disabled}
