import html_text_mirror


def test_mirror_writes_md_per_html(tmp_path):
    ds = tmp_path / "datasets"
    (ds / "product").mkdir(parents=True)
    (ds / "product" / "r.html").write_text("<title>Roadmap</title><h1>H2</h1><p>Feed GA</p>", encoding="utf-8")
    (ds / "product" / "n.md").write_text("# not mirrored", encoding="utf-8")
    n = html_text_mirror.build(str(tmp_path))
    out = ds / ".qmd-html" / "product" / "r.html.md"
    assert n == 1 and out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "source: datasets/product/r.html" in body and "Feed GA" in body


def test_mirror_titles_from_first_line(tmp_path):
    ds = tmp_path / "datasets"
    ds.mkdir()
    (ds / "p.html").write_text('<title>Q3 "Feed" plan</title><p>Body</p>', encoding="utf-8")
    html_text_mirror.build(str(tmp_path))
    body = (ds / ".qmd-html" / "p.html.md").read_text(encoding="utf-8")
    assert 'title: "Q3 \\"Feed\\" plan"' in body
    assert "\n# Q3 \"Feed\" plan\n" in body and "Body" in body


def test_mirror_skips_dot_dirs(tmp_path):
    ds = tmp_path / "datasets"
    (ds / ".hidden").mkdir(parents=True)
    (ds / ".hidden" / "x.html").write_text("<p>x</p>", encoding="utf-8")
    assert html_text_mirror.build(str(tmp_path)) == 0


def test_mirror_removes_stale(tmp_path):
    ds = tmp_path / "datasets"
    stale = ds / ".qmd-html" / "gone.html.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    html_text_mirror.build(str(tmp_path))
    assert not stale.exists()
