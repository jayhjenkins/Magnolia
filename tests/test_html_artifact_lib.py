"""Pure helpers for HTML artifacts - path test, format detection/resolution,
guidance text, CSP, html-to-text."""
import html_artifact_lib as h


def test_is_html_path():
    assert h.is_html_path("datasets/product/x.html")
    assert h.is_html_path("  datasets/product/X.HTM ")
    assert not h.is_html_path("datasets/product/x.md")
    assert not h.is_html_path("")
    assert not h.is_html_path(None)


def test_detect_output_format_phrases():
    assert h.detect_output_format("Q4 roadmap", "build it as HTML") == "html"
    assert h.detect_output_format("Pre-read as a page", "") == "html"
    assert h.detect_output_format("Pre-read as an HTML page", "") == "html"
    assert h.detect_output_format("Write the memo", "summarize the html export") is None
    assert h.detect_output_format("Write the memo", "") is None


def test_resolve_output_format_precedence():
    assert h.resolve_output_format({"output_format": "html"}, {"output_format": "md"}) == "html"
    assert h.resolve_output_format({}, {"output_format": "html"}) == "html"
    assert h.resolve_output_format({}, {}) == "md"
    assert h.resolve_output_format({"output_format": "bogus"}, {}) == "md"
    assert h.resolve_output_format(None, None) == "md"


def test_dispatch_block_points_at_contract_and_profile():
    b = h.dispatch_block()
    assert "context-html-artifact" in b and "profile/voice/html.md" in b
    assert ".html" in b
    b.encode("ascii")  # ASCII-safe runtime text


def test_chat_hint_names_path_and_in_place_rule():
    t = h.chat_hint("datasets/product/agent-output/x.html")
    assert "datasets/product/agent-output/x.html" in t
    assert "in place" in t
    t.encode("ascii")


def test_csp_blocks_network_and_same_origin():
    assert "sandbox allow-scripts" in h.CSP
    assert "allow-same-origin" not in h.CSP
    assert "connect-src 'none'" in h.CSP


def test_html_to_text_drops_style_script_and_tags():
    src = ("<html><head><style>.a{color:red}</style><script>var x=1</script></head>"
           "<body><h1>Offers</h1><p>GA in <b>Q4</b>.</p></body></html>")
    out = h.html_to_text(src)
    assert "color:red" not in out and "var x" not in out
    assert "Offers" in out and "GA in Q4." in out
