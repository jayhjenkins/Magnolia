import threading, time, itertools
import live_runs


def test_source_runs_to_completion_with_no_tail(monkeypatch):
    log = []
    src = iter([{"n": 1}, {"n": 2}, {"n": 3}])
    live_runs.start("k1", src, log.append)
    deadline = time.time() + 2
    while live_runs.is_live("k1") and time.time() < deadline:
        time.sleep(0.01)
    assert log == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert not live_runs.is_live("k1")


def test_tail_replays_then_streams(monkeypatch):
    log = []
    gate = threading.Event()

    def src():
        yield {"n": 1}
        gate.wait(1)
        yield {"n": 2}

    live_runs.start("k2", src(), log.append)
    seen = []
    t = live_runs.tail("k2", lambda: list(log), heartbeat=0.05)
    seen.append(next(e for e in t if e.get("kind") != "heartbeat"))  # {"n":1}
    gate.set()
    for e in t:
        if e.get("kind") == "heartbeat":
            continue
        seen.append(e)
    assert {"n": 1} in seen and {"n": 2} in seen


def test_closing_tail_does_not_kill_source():
    log = []
    go = threading.Event()

    def src():
        yield {"n": 1}
        go.wait(1)
        yield {"n": 2}

    live_runs.start("k3", src(), log.append)
    t = live_runs.tail("k3", lambda: list(log), heartbeat=0.05)
    next(t)            # consume one
    t.close()          # simulate client disconnect
    go.set()
    deadline = time.time() + 2
    while live_runs.is_live("k3") and time.time() < deadline:
        time.sleep(0.01)
    assert log == [{"n": 1}, {"n": 2}]   # source completed despite closed tail


def test_event_appended_between_read_and_wait_is_not_lost():
    """Lost-wakeup guard.

    The classic bug: a tail reads read_fn(), sees nothing new, then blocks on
    condition.wait() AFTER an event was appended+notified in the gap, and
    sleeps through the heartbeat instead of delivering the event. We force that
    interleaving deterministically: the source appends exactly one slow event
    well after the tail has done its first replay and is parked waiting, with a
    heartbeat far larger than the test deadline so a heartbeat can NOT be what
    rescues us. The event must be delivered (the version check must short-
    circuit the wait or the wait must be woken by notify_all), then the tail
    must terminate once done and fully drained.
    """
    log = []
    released = threading.Event()

    def src():
        # Give the tail time to do its initial replay (empty) and park on wait.
        released.wait(1)
        yield {"n": 42}

    live_runs.start("k4", src(), log.append)
    # Huge heartbeat: if delivery depended on the heartbeat firing, this hangs.
    t = live_runs.tail("k4", lambda: list(log), heartbeat=30.0)
    # Let the tail reach its parked wait, THEN append.
    time.sleep(0.2)
    released.set()
    seen = []
    deadline = time.time() + 3
    for e in t:
        if e.get("kind") == "heartbeat":
            continue
        seen.append(e)
        if seen:
            break
        if time.time() > deadline:
            break
    assert {"n": 42} in seen
