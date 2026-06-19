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


def test_onboarding_js_is_ascii():
    open(os.path.join(UI, "js", "onboarding.js"), "rb").read().decode("ascii")
