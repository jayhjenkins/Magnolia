import judge


def test_read_artifact_strips_html(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "PM_OS_DIR", str(tmp_path))
    p = tmp_path / "x.html"
    p.write_text("<style>" + ".a{}" * 9000 + "</style><h1>Offers GA</h1>", encoding="utf-8")
    text, _ = judge.read_artifact("x.html")
    assert "Offers GA" in text and ".a{}" not in text


def test_read_artifact_markdown_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "PM_OS_DIR", str(tmp_path))
    (tmp_path / "x.md").write_text("# T\n\n<b>raw</b>", encoding="utf-8")
    text, _ = judge.read_artifact("x.md")
    assert text == "# T\n\n<b>raw</b>"
