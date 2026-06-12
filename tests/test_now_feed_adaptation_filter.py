"""Now feed (GET /api/tasks) hides cards of an off adaptation.

The third discovery seam: when an adaptation is toggled OFF, the cards
produced by its built card-type must disappear from the board's Now feed.
The list handler filters tasks by adaptations_lib.is_live("card-type", card_type),
which returns True for core/unowned types (so they always show) and False for a
card-type owned by an adaptation that is not on.

Mirrors test_quick_add_route's _FakeHandler pattern. The Activity view
(GET /api/activity) is a SEPARATE handler and must keep showing everything;
that separation is asserted here too.
"""
import json


class _FakeHandler:
    def __init__(self):
        self.status = None
        self._chunks = []

    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, b): self._chunks.append(b)
    def json(self): return json.loads(b"".join(self._chunks).decode("utf-8"))


# A mixed bag: core/unowned types plus one card-type owned by an off adaptation.
_MIXED_TASKS = [
    {"id": "t-1", "title": "plain task", "card_type": "task"},
    {"id": "t-2", "title": "a receipt", "card_type": "receipt"},
    {"id": "t-3", "title": "untyped - no card_type field"},
    {"id": "t-4", "title": "stock alert A", "card_type": "stock-alert"},
    {"id": "t-5", "title": "stock alert B", "card_type": "stock-alert"},
]


def _patch(monkeypatch, *, off_card_type):
    """Stub the list source and is_live so only off_card_type is not-live."""
    import task_server, task_lib
    monkeypatch.setattr(task_lib, "list_tasks", lambda **kw: [dict(t) for t in _MIXED_TASKS])
    monkeypatch.setattr(task_server, "_enrich_sharepoint_url", lambda t: None)
    monkeypatch.setattr(
        task_server.adaptations_lib, "is_live",
        lambda surface, ref: not (surface == "card-type" and ref == off_card_type),
    )
    return task_server


def test_now_feed_drops_off_adaptation_cards(monkeypatch):
    srv = _patch(monkeypatch, off_card_type="stock-alert")
    h = _FakeHandler()
    srv.handle_list_tasks(h, {})

    assert h.status == 200
    ids = {t["id"] for t in h.json()}
    # Core/unowned types stay; the off adaptation's card-type is gone.
    assert ids == {"t-1", "t-2", "t-3"}


def test_now_feed_keeps_cards_when_adaptation_is_on(monkeypatch):
    # Nothing is off: every card-type is live, so all tasks show.
    srv = _patch(monkeypatch, off_card_type="__none__")
    h = _FakeHandler()
    srv.handle_list_tasks(h, {})

    assert h.status == 200
    ids = {t["id"] for t in h.json()}
    assert ids == {"t-1", "t-2", "t-3", "t-4", "t-5"}


def test_untyped_task_defaults_to_core_and_always_shows(monkeypatch):
    # Even if "task" itself were somehow off, an untyped task defaults to "task";
    # but core types are never owned, so default to "task" means it stays.
    srv = _patch(monkeypatch, off_card_type="stock-alert")
    h = _FakeHandler()
    srv.handle_list_tasks(h, {})

    ids = {t["id"] for t in h.json()}
    assert "t-3" in ids  # the untyped task survives


def test_is_live_queried_once_per_distinct_card_type(monkeypatch):
    """Performance: each distinct card_type is checked at most once, not per task
    (t-4 and t-5 share 'stock-alert'; is_live should see it a single time)."""
    import task_server, task_lib
    monkeypatch.setattr(task_lib, "list_tasks", lambda **kw: [dict(t) for t in _MIXED_TASKS])
    monkeypatch.setattr(task_server, "_enrich_sharepoint_url", lambda t: None)

    refs = []

    def _spy(surface, ref):
        refs.append((surface, ref))
        return True

    monkeypatch.setattr(task_server.adaptations_lib, "is_live", _spy)

    h = _FakeHandler()
    task_server.handle_list_tasks(h, {})

    # Five tasks, but only three distinct card-types (task, receipt, stock-alert);
    # the untyped task defaults to "task".
    assert len(refs) == len(set(refs)), "is_live called more than once per distinct card_type"
    assert ("card-type", "stock-alert") in refs
    assert ("card-type", "task") in refs
    assert ("card-type", "receipt") in refs


def test_is_live_called_with_card_type_surface(monkeypatch):
    """The filter must query is_live with surface 'card-type' and the task's
    card_type value (matching how the manifest stores a card-type's ref)."""
    srv = _patch(monkeypatch, off_card_type="stock-alert")
    calls = []
    monkeypatch.setattr(
        srv.adaptations_lib, "is_live",
        lambda surface, ref: calls.append((surface, ref)) or True,
    )
    h = _FakeHandler()
    srv.handle_list_tasks(h, {})

    assert ("card-type", "stock-alert") in calls
