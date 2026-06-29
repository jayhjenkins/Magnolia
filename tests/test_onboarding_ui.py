import os
import re

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "ui", "task-board")


def _read(name):
    with open(os.path.join(UI, name), encoding="utf-8") as f:
        return f.read()


def test_onboarding_html_is_no_longer_the_placeholder():
    html = _read("onboarding.html")
    assert "PLACEHOLDER" not in html
    assert 'src="/js/onboarding.js"' in html


def test_onboarding_html_tracks_the_active_mood():
    # Same pre-paint Mood bootstrap + theme links as index.html, so the room
    # inherits the saved Mood (invariant #3 - token-only theming).
    html = _read("onboarding.html")
    assert "pmos-mood" in html
    assert "/themes/organic.css" in html
    assert "data-theme" in html


def test_onboarding_html_defines_spring_token_locally():
    # --spring lives only in index.html's inline :root, not the theme files, so a
    # standalone page MUST define it (and a --mono fallback) itself.
    html = _read("onboarding.html")
    assert "--spring" in html


def test_onboarding_html_has_the_room_anchors():
    html = _read("onboarding.html")
    for el in ('id="onboard-start"', 'id="onboard-thread"', 'id="onboard-input"',
               'id="onboard-send"', 'id="onboard-body"', 'id="board-underlay"'):
        assert el in html, el


def test_onboarding_html_carries_the_self_destruct_copy():
    html = _read("onboarding.html")
    assert "This onboarding will self destruct in 3 seconds :)" in html


def test_onboarding_html_is_ascii():
    # Invariant #8 - runtime/source ASCII (no em-dash, no smart quotes).
    raw = open(os.path.join(UI, "onboarding.html"), "rb").read()
    raw.decode("ascii")


def test_onboarding_js_exists_and_posts_the_run_route():
    js = _read("js/onboarding.js")
    assert "/api/onboarding/run" in js


def test_onboarding_js_handles_the_completion_event():
    js = _read("js/onboarding.js")
    assert "onboarding_complete" in js


def test_onboarding_js_reads_the_sse_done_sentinel():
    js = _read("js/onboarding.js")
    assert "event:" in js and "done" in js  # mirrors chat.js handleFrame


def test_onboarding_js_runs_the_window_shade_reveal():
    js = _read("js/onboarding.js")
    assert "shade-pull" in js and "shade-lift" in js
    assert "board-underlay" in js  # mounts the iframe beneath


def test_onboarding_js_anchors_new_turns_to_top():
    # Bug fix: the kickoff is auto-fired hidden, so the first turn is one long
    # assistant-only message with nothing above it. The ported pin-to-bottom
    # scroll (scrollThread) chased the streaming tail and scrolled clean past
    # the top of that message, hiding the intro + first question. The fix
    # anchors each new assistant turn's top near the viewport top and only
    # follows the bottom while the reader is already parked there.
    js = _read("js/onboarding.js")
    assert "anchorTurnTop" in js   # new turn -> align its top, not the bottom
    assert "userPinned" in js      # follow the bottom only when parked there
    # scrollThread must be gated by the pin flag - no unconditional jump.
    m = re.search(r"function scrollThread\(\)\s*\{[^}]*\}", js)
    assert m and "userPinned" in m.group(0)


def test_onboarding_js_cache_busts_the_board_fetch():
    # '/' served the onboarding room moments before completion; a cached '/' would
    # re-show the room (with "Onboard me"). The redirect + underlay iframe must use
    # a cache-busted board url so the browser fetches the fresh board.
    js = _read("js/onboarding.js")
    assert "boardUrl" in js
    assert "location.replace(boardUrl)" in js   # redirect uses the busted url
    assert "iframe.src = boardUrl" in js         # so does the revealed underlay


def test_onboarding_js_renders_events_sequentially():
    # Bug fix: the assistant turn used a fixed turn-steps box (top) + turn-text box
    # (bottom), so all think/tool events bucketed above all text regardless of
    # arrival order. The renderer now appends segments to a single .turn-flow in
    # arrival order (startSteps / startText open a new segment when the kind flips).
    js = _read("js/onboarding.js")
    assert "turn-flow" in js
    assert "startSteps" in js and "startText" in js
    assert "curKind" in js  # tracks the open segment so a kind-flip starts a new one


def test_onboarding_js_skips_replayed_events_across_turns():
    # Bug fix: live_runs.tail replays the WHOLE transcript from index 0 on every
    # POST, so without a cursor a later turn re-renders earlier turns. A monotonic
    # renderedCount skips events already shown.
    js = _read("js/onboarding.js")
    assert "renderedCount" in js
    assert "connIdx <= renderedCount" in js  # skip already-rendered replayed events


def test_onboarding_js_is_ascii():
    open(os.path.join(UI, "js", "onboarding.js"), "rb").read().decode("ascii")
