#!/usr/bin/env python3
"""
dispatch_scheduler.py - Safety-net sweep for stranded tasks.

Runs as a daemon thread inside task_server.py, parallel to CronScheduler and
CadenceScheduler. Ticks every 60 seconds, finds tasks in dispatchable queues
(agent/collab) that were created but never dispatched, and fires the dispatcher.

This closes the gap where task_lib.create_task() is called without an inline
_spawn_task_dispatch() -- e.g. tasks created by chat agents via the CLI, or
any future code path that writes a task file without signaling dispatch.

Existing inline dispatch calls stay (belt). This sweep is the suspenders.
"""

import os
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_dispatch import get_actionable_tasks


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stderr.write(f"[{ts}] [dispatch-sweep] {msg}\n")
    sys.stderr.flush()


class DispatchScheduler:
    """Background sweep that catches stranded tasks in dispatchable queues."""

    def __init__(self, tick_interval=60, staleness_threshold=90, dispatch_fn=None):
        self.tick_interval = tick_interval
        self.staleness_threshold = staleness_threshold
        self.dispatch_fn = dispatch_fn
        self._thread = None
        self._running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="dispatch-sweep"
        )
        self._thread.start()
        _log(f"Started (tick every {self.tick_interval}s, staleness {self.staleness_threshold}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        _log("Stopped")

    def _loop(self):
        self.tick()
        while self._running:
            time.sleep(self.tick_interval)
            if self._running:
                self.tick()

    def tick(self):
        if not self.dispatch_fn:
            return
        now = datetime.now(timezone.utc)
        try:
            tasks = get_actionable_tasks()
        except Exception as e:
            _log(f"Error scanning tasks: {e}")
            return

        swept = 0
        for t in tasks:
            if t.get("agent_status") is not None:
                continue
            created = t.get("created", "")
            if created:
                try:
                    created_dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    if (now - created_dt).total_seconds() < self.staleness_threshold:
                        continue
                except (ValueError, TypeError):
                    pass
            try:
                self.dispatch_fn(t["id"])
                swept += 1
                _log(f"Swept stranded task {t['id']}: {t.get('title', '')}")
            except Exception as e:
                _log(f"Sweep dispatch failed for {t['id']}: {e}")
        if swept:
            _log(f"Tick complete: {swept} stranded task(s) dispatched")


if __name__ == "__main__":
    _log("Running standalone - press Ctrl+C to stop")
    scheduler = DispatchScheduler(tick_interval=10)
    scheduler.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
