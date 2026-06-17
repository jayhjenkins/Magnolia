"""Guard the single-transcript-feed guarantee.

Scans for OTHER downloaders that could write transcripts outside Magnolia.
Detection is conservative — it reports candidates; disabling is a separate,
user-confirmed step (Claude calls disable() only after the human says yes).
"""
import glob
import os
import re
import subprocess
import platform_lib

# Signals that a LaunchAgent is a transcript downloader.
_SIGNAL_RE = re.compile(r"otter|granola|transcript|meeting[-_]?sync", re.IGNORECASE)


def detect_competing(launch_agents_dir=None, own_labels=None):
    if platform_lib.os_kind() != "darwin":
        return []
    own = set(own_labels or [])
    d = launch_agents_dir or os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
    found = []
    for path in sorted(glob.glob(os.path.join(d, "*.plist"))):
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        # Label extraction is best-effort (falls back to filename); the
        # _SIGNAL_RE match below is the actual detection gate.
        m = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", text)
        label = m.group(1) if m else os.path.basename(path)[:-6]
        if label in own:
            continue
        if _SIGNAL_RE.search(text):
            found.append({"label": label, "path": path})
    return found


def disable(path, activate=True):
    """Disable a competing LaunchAgent (user-confirmed). Renames it aside; never deletes.

    Returns a dict:
      {"disabled_path": <new path>, "unloaded": <True | False | None>}
    where ``unloaded`` is the launchctl unload result (True on success, False on
    a nonzero return code) when ``activate=True``, and None when not attempted.
    """
    unloaded = None
    if activate and platform_lib.os_kind() == "darwin":
        result = subprocess.run(["launchctl", "unload", path], capture_output=True)
        unloaded = result.returncode == 0

    # Collision-safe: never overwrite an existing backup. Version the suffix
    # (.disabled-by-magnolia, .disabled-by-magnolia.2, .3, ...) so a prior
    # disable's backup is preserved — upholds the "never destroys" guarantee.
    base = path + ".disabled-by-magnolia"
    disabled = base
    n = 2
    while os.path.exists(disabled):
        disabled = "{}.{}".format(base, n)
        n += 1

    os.rename(path, disabled)
    return {"disabled_path": disabled, "unloaded": unloaded}
