"""live_runs - run a generator to a persisted log, decoupled from any consumer.

The substrate that lets long Magnolia builds survive an SSE client disconnect.

The problem it solves
---------------------
chat_runner._spawn owns the `claude` process group and, by design, kills that
group when its generator consumer closes early (a try/finally on GeneratorExit
at the yield). For an SSE route that directly iterates the runner, a browser
disconnect closes the generator -> the process group dies -> an hour-long build
is murdered by someone navigating away.

The fix is a decoupling: a background daemon thread fully consumes the runner
generator and appends every event to a persisted log. A consumer (the SSE
handler) only *tails* that log. Because the consumer no longer holds the runner
generator, closing the tail can never deliver GeneratorExit to the source - so
_spawn's teardown never fires on disconnect, and the build runs to completion.

Concurrency model
------------------
A module-level dict `_RUNS` (key -> RunState), guarded by `_LOCK`, tracks live
runs. Each RunState carries its own threading.Condition. The source thread, for
each event, appends via the caller's append_fn, then under the condition bumps
`version` and notify_all()s. On normal completion or any exception it sets
`done` (capturing the error) and notify_all()s once more.

`tail` is a pure generator. It snapshots the version UNDER the condition before
deciding to wait, drains all currently-available events first, and only then
checks done-and-fully-drained. This ordering defeats two classic races:

  * drain-before-done: a final event appended in the same instant the source
    flips done is delivered, because we re-read read_fn() while draining and
    re-check len under the lock before concluding the stream is finished.
  * lost wakeup: an event appended (version bumped + notify_all) in the window
    between the tail's read and its wait is not missed - we snapshot version
    under the lock, and if it advanced before we wait we loop again instead of
    sleeping; if it advances while we wait, notify_all wakes us.

Pure stdlib threading. No signals, no platform_lib. ASCII-safe.
"""

import threading


class RunState:
    """Per-run synchronization state. One Condition guards version/done/error."""

    __slots__ = ("cond", "version", "done", "error", "thread")

    def __init__(self):
        self.cond = threading.Condition()
        self.version = 0   # bumped (under cond) on every appended event
        self.done = False  # set (under cond) when the source thread finishes
        self.error = None  # captured exception from the source, if any
        self.thread = None


_RUNS = {}
_LOCK = threading.Lock()


def _get(key):
    with _LOCK:
        return _RUNS.get(key)


def start(key, source_iter, append_fn):
    """Begin consuming `source_iter` into `append_fn` on a daemon thread.

    If a run for `key` is already live, this is a no-op. Otherwise a RunState is
    registered and a daemon thread spawned that iterates the source, calling
    `append_fn(event)` for each event then signalling waiters. On completion or
    any exception the run is marked done (the error stored). The source thread
    never depends on a tail being attached.
    """
    with _LOCK:
        existing = _RUNS.get(key)
        if existing is not None and not existing.done:
            return  # already live - no-op
        state = RunState()
        _RUNS[key] = state

    def _run():
        err = None
        try:
            for event in source_iter:
                append_fn(event)
                with state.cond:
                    state.version += 1
                    state.cond.notify_all()
        except BaseException as exc:  # noqa: BLE001 - capture, never crash the thread
            err = exc
        finally:
            with state.cond:
                state.done = True
                state.error = err
                state.cond.notify_all()

    t = threading.Thread(target=_run, name="live-run-%s" % (key,), daemon=True)
    state.thread = t
    t.start()


def is_live(key):
    """True if a run for `key` is registered and not yet marked done."""
    state = _get(key)
    return state is not None and not state.done


def run_error(key):
    """Return the captured source exception for a DONE run, else None.

    The source thread stores any exception it hit on RunState.error and only
    after marking the run done. This accessor surfaces it so a consumer (the SSE
    handler) can tell an abnormal end from a clean finish: while a run is still
    live, or if it finished cleanly, or if `key` was never started, this is None.
    """
    state = _get(key)
    if state is None or not state.done:
        return None
    return state.error


def tail(key, read_fn, heartbeat=15.0, start_from=0):
    """Yield events for `key`: replay the log, then stream new events live.

    `read_fn()` returns the full event list so far (a snapshot). The tail yields
    every event past its `last_index`, advancing as it goes. When caught up it
    blocks on the run's Condition for up to `heartbeat` seconds; on a silent
    timeout it yields {"kind": "heartbeat"}. It terminates once the run is done
    AND the log is fully drained.

    `start_from` skips the first N events (for callers that already have
    history rendered and only want new events from the current turn).

    Closing the generator (GeneratorExit at a yield) only stops the tail. It
    touches nothing the source thread reads, so the source - and the underlying
    `claude` process group - is unaffected. That is the whole point.

    If `key` has no run state at all, the tail still replays whatever read_fn()
    currently returns and then ends (treated as already-done).
    """
    state = _get(key)
    last_index = start_from
    try:
        while True:
            # 1. DRAIN FIRST. Yield everything available past last_index.
            events = read_fn()
            while last_index < len(events):
                event = events[last_index]
                last_index += 1
                yield event
                # Re-read after the yield: more may have arrived (or the
                # generator may be closing, raising GeneratorExit here).
                events = read_fn()

            if state is None:
                # No run was ever registered: replayed all we had, we're done.
                return

            # 2. Decide under the lock whether to finish or wait. Snapshot the
            # version so an append in the gap below is detected, not slept on.
            with state.cond:
                version_at_check = state.version
                # Fully drained AND source finished -> stream is complete.
                if state.done and last_index >= len(read_fn()):
                    return
                # If new events landed since we drained, loop now (no wait):
                # last_index lags the current log -> there is data to deliver.
                if last_index < len(read_fn()):
                    continue
                # Caught up and not done: park until notified or heartbeat.
                state.cond.wait(timeout=heartbeat)
                # If nothing changed while we waited, it was a silent timeout.
                woke_with_data = state.version != version_at_check
            if not woke_with_data:
                # Silent timeout - re-check for late data before heartbeating so
                # a notify that raced the timeout boundary is not turned into a
                # spurious heartbeat that hides a real event.
                if last_index < len(read_fn()):
                    continue
                if state.done:
                    # Source finished during the wait with nothing new to drain.
                    return
                yield {"kind": "heartbeat"}
    except GeneratorExit:
        # Consumer closed the tail (e.g. SSE client disconnected). Do NOT signal
        # the source thread - it owns the process group and must run on.
        return


def _reset():
    """Test hygiene: drop all run state. Tests use distinct keys, so optional.

    Does not stop daemon threads (they finish on their own and do not block
    process exit); just clears the registry so a fresh process-like state can be
    asserted between tests if a suite chooses to call it.
    """
    with _LOCK:
        _RUNS.clear()
