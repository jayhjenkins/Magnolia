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
    assert h.detect_output_format("Fix layout", "use it as a page break") is None
    assert h.detect_output_format("Fix layout", "save as page 2") is None
    assert h.detect_output_format("Escape", "treat as html entities") is None
    assert h.detect_output_format("Fix the filter as a page-level setting", "") is None
    assert h.detect_output_format("Post it as a page in Confluence", "") is None
    assert h.detect_output_format("Publish it as a page on the wiki", "") is None
    assert h.detect_output_format("Newsletter", "render as HTML email") is None
    assert h.detect_output_format("Pre-read", "Build it as HTML.") == "html"
    assert h.detect_output_format("Pre-read", "Ship it as a page, please") == "html"


def test_resolve_output_format_precedence():
    assert h.resolve_output_format({"output_format": "html"}, {"output_format": "md"}) == "html"
    assert h.resolve_output_format({}, {"output_format": "html"}) == "html"
    assert h.resolve_output_format({}, {}) == "md"
    assert h.resolve_output_format({"output_format": "bogus"}, {}) == "md"
    assert h.resolve_output_format(None, None) == "md"
    assert h.resolve_output_format("html", ["html"]) == "md"
    assert h.resolve_output_format("bad", {"output_format": "html"}) == "html"


def test_dispatch_block_points_at_contract_and_profile():
    b = h.dispatch_block()
    assert "context-html-artifact" in b and "profile/voice/html.md" in b
    assert ".html" in b
    assert b.isascii()  # ASCII-safe runtime text


def test_chat_hint_names_path_and_in_place_rule():
    t = h.chat_hint("datasets/product/agent-output/x.html")
    assert "datasets/product/agent-output/x.html" in t
    assert "in place" in t
    assert t.isascii()


def test_csp_exact():
    assert h.CSP == (
        "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; "
        "default-src 'none'; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com data:; "
        "img-src https: data:; "
        "script-src 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "connect-src 'none'"
    )
    assert h.CSP.isascii()


def test_html_to_text_drops_style_script_and_tags():
    src = ("<html><head><style>.a{color:red}</style><script>var x=1</script></head>"
           "<body><h1>Offers</h1><p>GA in <b>Q4</b>.</p></body></html>")
    out = h.html_to_text(src)
    assert "color:red" not in out and "var x" not in out
    assert "Offers" in out and "GA in Q4." in out
    assert "Offers\nGA in Q4." in out


def test_html_to_text_table_cells_separated():
    out = h.html_to_text("<table><tr><th>Name</th><th>Value</th></tr>"
                         "<tr><td>A</td><td>1</td></tr></table>")
    assert "NameValue" not in out and "A1" not in out
    assert out.splitlines() == ["Name Value", "A 1"]


def test_html_to_text_drops_noscript_svg_template():
    src = ("<p>keep</p><noscript>ns-gone</noscript>"
           "<svg><g><svg><text>inner-gone</text></svg><text>outer-gone</text></g></svg>"
           "<template>tpl-gone</template><p>after</p>")
    out = h.html_to_text(src)
    for gone in ("ns-gone", "inner-gone", "outer-gone", "tpl-gone"):
        assert gone not in out
    assert out.splitlines() == ["keep", "after"]


def test_html_to_text_new_block_tags_break_lines():
    out = h.html_to_text("<dl><dt>Term</dt><dd>Def</dd></dl><figure>Fig"
                         "<figcaption>Cap</figcaption></figure><details><summary>S</summary>D</details>")
    assert out.splitlines() == ["Term", "Def", "Fig", "Cap", "S", "D"]


def test_html_to_text_entities_and_nbsp():
    out = h.html_to_text("<p>R&amp;D&nbsp;&nbsp; team</p>")
    assert out == "R&D team"
