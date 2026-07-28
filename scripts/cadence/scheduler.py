#!/usr/bin/env python3
"""
scheduler.py - Background clock for the deterministic Cadence reconciler.

Runs as a daemon thread inside task_server.py, mirroring cron_scheduler.py. On
each tick it calls reconcile.reconcile_all() in-process - deterministic, Tier-1,
NEVER spawning an agent. This is deliberately a SEPARATE scheduler from
CronScheduler: the cron path is create-task -> dispatch-an-LLM-agent, whereas
cadence reconcile must stay in-process and write nothing externally beyond the
existing local escalate card.

The reconciler ticks hourly and persists drift + last_run on every tick so the
Cadence tab always shows current state. Emitters (nudges, worker dispatches)
remain cycle-gated so they only fire once per period.

Sentinels (the read-only observers that feed the reconciler) are dispatched as
background subprocesses twice daily on workdays (9am and 1pm local). They run
outside this thread so a slow LLM call never blocks the reconcile loop.

All runtime/log strings are ASCII-safe (hyphen, never em-dash) per invariant #8.
"""

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cadence import reconcile

# ─── Logging ─────────────────────────────────────────────────────────────────

def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stderr.write(f"[{ts}] [cadence-scheduler] {msg}\n")
    sys.stderr.flush()


# ─── Scheduler ───────────────────────────────────────────────────────────────

SENTINEL_HOURS = {9, 13}
SENTINEL_NAMES = ["movement-watch", "tracker-truth"]


def _is_workday(now):
    return now.weekday() < 5


def _should_run_sentinels(now, last_sentinel_hour):
    """True when it is a workday, the current hour is a sentinel hour, and we
    have not already dispatched sentinels this hour."""
    if not _is_workday(now):
        return False
    h = now.hour
    if h not in SENTINEL_HOURS:
        return False
    return last_sentinel_hour != (now.date().isoformat(), h)


def _dispatch_sentinel(name):
    """Fire sentinel_runner.py in the background (fire-and-forget)."""
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sentinel_runner.py",
    )
    try:
        subprocess.Popen(
            [sys.executable, script, name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        _log(f"Failed to dispatch sentinel {name}: {e}")


class CadenceScheduler:
    """Background reconcile + sentinel scheduler that ticks hourly by default."""

    def __init__(self, tick_interval=3600):
        self.tick_interval = tick_interval
        self._thread = None
        self._running = False
        self._last_sentinel_hour = (None, None)

    def start(self):
        """Start the scheduler as a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cadence-scheduler")
        self._thread.start()
        _log(f"Started (tick every {self.tick_interval}s)")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        _log("Stopped")

    def _loop(self):
        """Main loop: tick, sleep, repeat."""
        self.tick()
        while self._running:
            time.sleep(self.tick_interval)
            if self._running:
                self.tick()

    def tick(self):
        """Reconcile every active program, dispatch sentinels when due."""
        now = datetime.now(timezone.utc)

        if _should_run_sentinels(now, self._last_sentinel_hour):
            self._last_sentinel_hour = (now.date().isoformat(), now.hour)
            for name in SENTINEL_NAMES:
                _dispatch_sentinel(name)
            _log(f"Dispatched sentinels: {', '.join(SENTINEL_NAMES)}")

        try:
            results = reconcile.reconcile_all()
        except Exception as e:
            _log(f"Error during reconcile: {e}")
            return

        total = len(results)
        emitted = sum(
            1
            for r in results
            for e in (r.get("emitted") or [])
            if isinstance(e, str) and e.startswith("TASK-")
        )
        broke = sum(1 for r in results if r.get("verdict") == "broken")
        errored = sum(1 for r in results if "error" in r)
        if emitted or errored:
            _log(
                f"Tick complete: {total} program(s) reconciled, "
                f"{broke} broken, {emitted} card(s) emitted, {errored} error(s)"
            )


# ─── Standalone testing ──────────────────────────────────────────────────────

if __name__ == "__main__":
    _log("Running standalone - press Ctrl+C to stop")
    scheduler = CadenceScheduler(tick_interval=10)
    scheduler.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
