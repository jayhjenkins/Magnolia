import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def _frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} missing YAML frontmatter"
    fm = text.split("---\n", 2)[1]
    return fm


def test_workflow_doctor_frontmatter():
    fm = _frontmatter(REPO / ".claude/skills/workflow-doctor/SKILL.md")
    assert "name: workflow-doctor" in fm
    assert "description:" in fm
    assert "Use when" in fm  # trigger-led description


def test_meta_onboard_frontmatter_and_persona():
    path = REPO / ".claude/skills/meta-onboard/SKILL.md"
    fm = _frontmatter(path)
    assert "name: meta-onboard" in fm
    body = path.read_text()
    assert "Magnolia" in body          # the host persona is specified
    assert "doctor.py detect" in body  # step 4 wiring
    assert "server_lib" in body        # step 5 wiring


def test_meta_onboard_reveals_board_only_at_the_end():
    # Bug fix: Step 5 opened the board mid-flow and ran a "welcome onto your board,
    # come back for finishing steps" beat - a back-and-forth that breaks the
    # single, end-of-flow reveal. The board open (open_url) must appear exactly
    # once, AFTER the Close header, and the one-direction rule must be stated.
    body = (REPO / ".claude/skills/meta-onboard/SKILL.md").read_text()
    assert body.count("open_url") == 1, "the board should be opened exactly once"
    assert body.index("open_url") > body.index("## Close"), "reveal belongs in the Close, not mid-flow"
    assert "one direction" in body.lower() or "no going back and forth" in body.lower()
