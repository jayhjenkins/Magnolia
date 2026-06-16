#!/usr/bin/env python3
"""
scheduler.py - Background clock for the deterministic Cadence reconciler.

Runs as a daemon thread inside task_server.py, mirroring cron_scheduler.py. On
each tick it calls reconcile.reconcile_all() in-process - deterministic, Tier-1,
NEVER spawning an agent. This is deliberately a SEPARATE scheduler from
CronScheduler: the cron path is create-task -> dispatch-an-LLM-agent, whereas
cadence reconcile must stay in-process and write nothing externally beyond the
existing local escalate card.

The default tick is hourly: reconcile_all is idempotent and once-per-cadence-
period guarded, so an hourly tick is plenty - the first tick of a new ISO week
runs each weekly program's cycle. A reconcile error is logged and swallowed; it
must never propagate or kill the thread.

All runtime/log strings are ASCII-safe (hyphen, never em-dash) per invariant #8.
"""

import os
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

class CadenceScheduler:
    """Background reconcile scheduler that ticks hourly by default."""

    def __init__(self, tick_interval=3600):
        self.tick_interval = tick_interval
        self._thread = None
        self._running = False

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
        # On startup, do an initial tick to catch the current period.
        self.tick()
        while self._running:
            time.sleep(self.tick_interval)
            if self._running:
                self.tick()

    def tick(self):
        """Reconcile every active program. Errors are logged and swallowed."""
        try:
            results = reconcile.reconcile_all()
        except Exception as e:
            # A reconcile error must NEVER propagate or kill the thread.
            _log(f"Error during reconcile: {e}")
            return

        total = len(results)
        emitted = sum(len(r.get("emitted") or []) for r in results)
        broke = sum(1 for r in results if r.get("verdict") == "broken")
        errored = sum(1 for r in results if "error" in r)
        # Only log when something actually happened (mirrors cron_scheduler's
        # `if executed > 0` guard) so an idle board stays quiet hour to hour.
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
