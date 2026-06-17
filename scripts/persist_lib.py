"""Reboot-persistence for the task server.

macOS: per-user LaunchAgent (RunAtLoad + KeepAlive) — RUN-VALIDATED.
Windows: Task Scheduler at logon — DESIGN-VALIDATED ONLY (no Windows box).
Same install()/remove()/is_installed() API on both via platform_lib.
"""
import os
import subprocess
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402

LABEL = "com.pm-os.task-server"
WIN_TASK_NAME = "MagnoliaTaskServer"


def render_launchagent(label, program, working_dir, log_path):
    args = "\n".join(f"        <string>{escape(a)}</string>" for a in program)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>WorkingDirectory</key>
    <string>{escape(working_dir)}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{escape(log_path)}</string>
    <key>StandardErrorPath</key>
    <string>{escape(log_path)}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


def render_scheduled_task(name, program, args, working_dir):
    """Return a PowerShell snippet registering a per-user at-logon task.

    DESIGN-VALIDATED ONLY — not executed/verified on Windows.
    Per-user (no -RunLevel Highest) so it needs no admin/UAC and runs in the
    user's context (can read their files/creds).
    """
    return (
        f"$action = New-ScheduledTaskAction -Execute '{program}' "
        f"-Argument '{args}' -WorkingDirectory '{working_dir}'\n"
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn\n"
        f"$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)\n"
        f"Register-ScheduledTask -TaskName '{name}' -Action $action "
        f"-Trigger $trigger -Settings $settings -Force\n"
    )


def _plist_path():
    """Return the macOS LaunchAgent plist path for LABEL, or None off-darwin."""
    d = platform_lib.launch_agents_dir()
    return os.path.join(d, f"{LABEL}.plist") if d else None


def is_installed():
    """Report whether persistence is configured (macOS: a config-present check —
    the plist exists on disk; NOT a running-state check). Always False elsewhere."""
    if platform_lib.os_kind() == "darwin":
        p = _plist_path()
        return bool(p and os.path.isfile(p))
    # Windows: would query `schtasks /query /tn {WIN_TASK_NAME}` — design-only.
    return False


def install(program, working_dir, log_path, activate=True):
    """Install reboot-persistence for the task server.

    Returns a dict that always carries "mechanism". On macOS it also carries
    "path" (the plist). `activate` gates whether the agent is actually loaded:
    when True (darwin), the plist is (re)loaded via launchctl and the result
    adds "activated" (bool, True iff `launchctl load` succeeded) plus, on
    failure, "activation_error" (a stderr/returncode summary). When activate is
    False the "activated" key is omitted (no activation was attempted)."""
    if not program:
        raise ValueError("program must be a non-empty list (executable + args)")
    kind = platform_lib.os_kind()
    if kind == "darwin":
        plist = render_launchagent(LABEL, program, working_dir, log_path)
        path = _plist_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(plist)
        result = {"mechanism": "launchagent", "path": path}
        if activate:
            # unload first to clear any prior registration; its returncode is
            # expected to be nonzero when nothing is loaded, so we ignore it and
            # base activation status solely on the load call below.
            subprocess.run(["launchctl", "unload", path], capture_output=True)
            load_result = subprocess.run(["launchctl", "load", path],
                                         capture_output=True)
            activated = load_result.returncode == 0
            result["activated"] = activated
            if not activated:
                stderr = getattr(load_result, "stderr", b"") or b""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
                stderr = stderr.strip()
                result["activation_error"] = (
                    stderr or f"launchctl load exited with {load_result.returncode}"
                )
        return result
    if kind == "windows":
        cmd = render_scheduled_task(WIN_TASK_NAME, program[0],
                                    " ".join(program[1:]), working_dir)
        # design-only: hand the command back for Claude to run in PowerShell
        return {"mechanism": "scheduled_task", "command": cmd}
    return {"mechanism": "none", "note": "persistence unsupported on this OS"}


def remove(activate=True):
    """Remove macOS persistence: unload (when activate) and delete the plist.

    Returns True if a plist was found and removed, else False. (No Windows
    branch yet — tracked separately.)"""
    if platform_lib.os_kind() == "darwin":
        path = _plist_path()
        if path and os.path.isfile(path):
            if activate:
                subprocess.run(["launchctl", "unload", path], capture_output=True)
            os.remove(path)
            return True
    return False
