import os

import program_lib as pl


def test_create_and_read_roundtrip(tmp_path):
    root = str(tmp_path)
    pid, path = pl.create_program(
        type="roadmap-initiative", title="Payments revamp",
        owner_role="product", root=root,
        frontmatter_extra={"phase": "execution", "drift": "holding"},
        intent="Rebuild reconciliation.")
    assert pid == "PROG-0001"
    assert os.path.isfile(path)
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["title"] == "Payments revamp"
    assert prog["frontmatter"]["type"] == "roadmap-initiative"
    assert "Rebuild reconciliation." in prog["body"]


def test_next_id_increments(tmp_path):
    root = str(tmp_path)
    a, _ = pl.create_program(type="weekly-priorities", title="A", owner_role="product", root=root)
    b, _ = pl.create_program(type="weekly-priorities", title="B", owner_role="product", root=root)
    assert (a, b) == ("PROG-0001", "PROG-0002")


def test_list_programs_filters_status(tmp_path):
    root = str(tmp_path)
    pl.create_program(type="weekly-priorities", title="active one", owner_role="product",
                      root=root, frontmatter_extra={"status": "active"})
    pl.create_program(type="weekly-priorities", title="archived one", owner_role="product",
                      root=root, frontmatter_extra={"status": "archived"})
    actives = pl.list_programs(status="active", root=root)
    assert [p["frontmatter"]["title"] for p in actives] == ["active one"]


def test_write_roundtrip_validates_yaml(tmp_path):
    root = str(tmp_path)
    pid, path = pl.create_program(type="weekly-priorities", title="X", owner_role="product", root=root)
    prog = pl.read_program(pid, root=root)
    prog["frontmatter"]["title"] = "Edited"
    pl._write_program_file(path, prog["frontmatter"], prog["body"])
    assert pl.read_program(pid, root=root)["frontmatter"]["title"] == "Edited"
