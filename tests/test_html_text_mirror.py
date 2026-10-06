import pytest

import html_text_mirror


def _symlink_or_skip(link, target, dir=False):
    try:
        link.symlink_to(target, target_is_directory=dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")


def test_mirror_writes_md_per_html(tmp_path):
    ds = tmp_path / "datasets"
    (ds / "product").mkdir(parents=True)
    (ds / "product" / "r.html").write_text("<title>Roadmap</title><h1>H2</h1><p>Feed GA</p>", encoding="utf-8")
    (ds / "product" / "n.md").write_text("# not mirrored", encoding="utf-8")
    n = html_text_mirror.build(str(tmp_path))
    out = ds / ".qmd-html" / "product" / "r.html.md"
    assert n == 1 and out.is_file()
    body = out.read_text(encoding="utf-8")
    assert 'source: "datasets/product/r.html"' in body and "Feed GA" in body


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


def _frontmatter(path):
    from ruamel.yaml import YAML
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return YAML(typ="safe").load(text.split("---\n", 2)[1])


def test_frontmatter_yaml_safe_for_odd_names(tmp_path):
    ds = tmp_path / "datasets"
    ds.mkdir()
    (ds / "Q3 Plan #2.html").write_text("<title>Plan \U0001F680 ok</title><p>x</p>", encoding="utf-8")
    (ds / "a: b.html").write_text("<p>y</p>", encoding="utf-8")
    assert html_text_mirror.build(str(tmp_path)) == 2
    fm1 = _frontmatter(ds / ".qmd-html" / "Q3 Plan #2.html.md")
    assert fm1["source"] == "datasets/Q3 Plan #2.html"
    assert fm1["title"] == "Plan \U0001F680 ok"
    fm2 = _frontmatter(ds / ".qmd-html" / "a: b.html.md")
    assert fm2["source"] == "datasets/a: b.html"


def test_empty_page_gets_source_only(tmp_path):
    ds = tmp_path / "datasets"
    ds.mkdir()
    (ds / "e.html").write_text("<style>.a{}</style>", encoding="utf-8")
    assert html_text_mirror.build(str(tmp_path)) == 1
    fm = _frontmatter(ds / ".qmd-html" / "e.html.md")
    assert fm == {"source": "datasets/e.html"}


def test_bad_file_does_not_abort(tmp_path, capsys, monkeypatch):
    ds = tmp_path / "datasets"
    (ds / "product").mkdir(parents=True)
    (ds / "product" / "good.html").write_text("<p>Good page</p>", encoding="utf-8")
    _symlink_or_skip(ds / "product" / "broken.html", tmp_path / "nowhere.html")
    (ds / "product" / "unreadable.html").write_text("<p>u</p>", encoding="utf-8")
    real_open = open

    def flaky_open(path, *a, **kw):
        if str(path).endswith("unreadable.html"):
            raise PermissionError("denied")
        return real_open(path, *a, **kw)
    monkeypatch.setattr(html_text_mirror, "open", flaky_open, raising=False)
    stale = ds / ".qmd-html" / "old.html.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("x", encoding="utf-8")
    n = html_text_mirror.build(str(tmp_path))
    assert n == 1
    assert "Good page" in (ds / ".qmd-html" / "product" / "good.html.md").read_text(encoding="utf-8")
    assert not (ds / ".qmd-html" / "product" / "broken.html.md").exists()
    assert not stale.exists()
    assert not (ds / ".qmd-html.tmp").exists()
    assert "unreadable.html" in capsys.readouterr().out


def test_skips_symlinked_and_huge_pages(tmp_path, monkeypatch):
    ds = tmp_path / "datasets"
    ds.mkdir()
    real = tmp_path / "outside.html"
    real.write_text("<p>secret</p>", encoding="utf-8")
    _symlink_or_skip(ds / "link.html", real)
    (ds / "big.html").write_text("<p>" + "x" * 200 + "</p>", encoding="utf-8")
    monkeypatch.setattr(html_text_mirror, "MAX_BYTES", 100)
    assert html_text_mirror.build(str(tmp_path)) == 0


def test_refuses_symlinked_mirror_dir(tmp_path):
    ds = tmp_path / "datasets"
    ds.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("k", encoding="utf-8")
    _symlink_or_skip(ds / ".qmd-html", elsewhere, dir=True)
    (ds / "p.html").write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(RuntimeError):
        html_text_mirror.build(str(tmp_path))
    assert (elsewhere / "keep.txt").exists() and not (elsewhere / "p.html.md").exists()
