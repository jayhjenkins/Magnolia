"""Profile-driven transcript-feed entrypoint. Dispatches by provider.

`sync()` runs the configured provider once (Otter or Granola). `TranscriptScheduler`
is the in-process hourly clock the board server (task_server.py) starts on every
OS: each tick runs `python transcript_sync.py` as a subprocess. It stands down
when something else already schedules the feed - a transcript LaunchAgent on
macOS (Magnolia's own or a prior install's, detected via feed_guard) or a profile
flagged `transcript.external_feed` - so the feed never double-runs.
"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402


def _run_otter(root=None):
    """Run the ported Otter sync against the LIVE profile.

    Contract note (Finding #1): otter_sync resolves its STATE_DIR/MEETINGS_DIR
    from the live profile at module-import time and does NOT honor a per-call
    ``root``. The ``root`` argument is accepted for signature symmetry with the
    rest of this module (and for test injection), but it is intentionally not
    threaded into the ported runner. This is safe under the single-install
    assumption (one install = one live profile). Re-plumbing otter_sync to
    accept ``root`` is deliberately out of scope to preserve the port.
    """
    # Lazy import: otter_sync pulls in heavy deps (otterai) at module load,
    # so we only import it when actually dispatching to the Otter provider.
    import otter_sync
    return otter_sync.main()  # ported entrypoint


def _run_granola(root=None):
    """Run the Granola sync. Lazy import keeps claude -p / MCP deps out of module load."""
    import granola_sync
    return granola_sync.main(root)


def sync(root=None):
    # ``root`` selects the profile used to resolve the provider. Note that for
    # the Otter provider the ported runner operates on the LIVE profile and does
    # not thread ``root`` (single-install assumption — see _run_otter).
    #
    # Dispatch goes through the adapter loader: provider -> module behind the
    # transcript seam. Otter's adapter delegates back to _run_otter (above), so
    # the headless structured-error contract and its test monkeypatch still hold.
    provider = profile_lib.transcript_config(root)["provider"]
    if provider == "none":
        return {"status": "skipped", "provider": "none"}
    import adapters
    mod = adapters.get("transcript", root)
    if mod is None:
        return {"status": "unsupported", "provider": provider}
    return mod.sync(root)


# ─── In-server hourly scheduler ──────────────────────────────────────────────

SYNC_INTERVAL = 3600       # seconds between ticks
INITIAL_DELAY = 120        # let the server finish booting before the first sync
MAX_RUNTIME = 50 * 60      # a sync still running after this is presumed hung


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stderr.write(f"[{ts}] [transcript-scheduler] {msg}\n")
    sys.stderr.flush()


def external_scheduler(root=None):
    """Describe whatever already schedules the transcript feed, or None.

    - profile `transcript.external_feed: true` (an adopted prior-install feed)
    - any transcript LaunchAgent plist (macOS only; launch_agents_dir() is None
      elsewhere). feed_guard's detection matches Magnolia's own installers
      (com.magnolia.granolasync / ottersync) and prior installs alike; a plist
      renamed aside by feed_guard.disable() no longer counts.
    """
    if profile_lib.transcript_external(root):
        return "profile transcript.external_feed"
    la_dir = platform_lib.launch_agents_dir()
    if not la_dir or not os.path.isdir(la_dir):
        return None
    import feed_guard
    found = feed_guard.detect_competing(launch_agents_dir=la_dir)
    return f"LaunchAgent {found[0]['label']}" if found else None


class TranscriptScheduler:
    """Hourly transcript sync, run as a subprocess so a hang can't block the server.

    Mirrors CadenceScheduler/CronScheduler (daemon thread started by task_server).
    Each tick re-reads the profile, so switching provider in the Engine tab takes
    effect on the next tick without a restart."""

    def __init__(self, tick_interval=SYNC_INTERVAL, initial_delay=INITIAL_DELAY,
                 max_runtime=MAX_RUNTIME, root=None, popen=None):
        self.tick_interval = tick_interval
        self.initial_delay = initial_delay
        self.max_runtime = max_runtime
        self.root = root
        self._popen = popen or subprocess.Popen
        self._proc = None
        self._started_at = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._last_skip = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="transcript-scheduler")
        self._thread.start()
        _log(f"Started (sync every {self.tick_interval}s)")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        _log("Stopped")

    def _loop(self):
        if self._stop.wait(self.initial_delay):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:      # never let the clock die
                _log(f"tick error: {e}")
            if self._stop.wait(self.tick_interval):
                return

    def _skip(self, reason, detail=""):
        if (reason, detail) != self._last_skip:   # log a skip reason once, not hourly
            _log(f"skipping transcript sync: {reason}{' - ' + detail if detail else ''}")
            self._last_skip = (reason, detail)
        return f"skipped:{reason}"

    def tick(self):
        """Start one sync if due. Returns a short outcome string (for tests/logs)."""
        provider = profile_lib.transcript_config(self.root)["provider"]
        if provider in ("none", "", None):
            return self._skip("no-provider")
        ext = external_scheduler(self.root)
        if ext:
            return self._skip("external", ext)
        if self._proc is not None and self._proc.poll() is None:
            if time.monotonic() - self._started_at < self.max_runtime:
                return self._skip("running")
            _log(f"previous {provider} sync exceeded {self.max_runtime}s - killing it")
            platform_lib.kill_process_group(self._proc)
        self._last_skip = None
        log_dir = os.path.join(str(profile_lib.PM_OS_DIR), "logs")
        os.makedirs(log_dir, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            with open(os.path.join(log_dir, f"{provider}_sync.log"), "a",
                      encoding="utf-8") as logf:
                self._proc = self._popen(
                    [sys.executable, os.path.abspath(__file__)],
                    cwd=str(profile_lib.PM_OS_DIR), env=env,
                    stdout=logf, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    **platform_lib.process_group_kwargs(),
                )
        except Exception as e:
            _log(f"could not start {provider} sync: {e}")
            self._proc = None
            return "error"
        self._started_at = time.monotonic()
        _log(f"started {provider} sync (pid {getattr(self._proc, 'pid', '?')})")
        return "started"


if __name__ == "__main__":
    print(sync())
