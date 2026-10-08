import os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(ROOT, ".claude", "skills", "workflow-jira-home", "SKILL.md")

# Every Jira prompt surface that must stay instance-agnostic: site, project,
# component, board, lane label and custom-field ids all come from the profile.
JIRA_SURFACES = [
    F,
    os.path.join(ROOT, ".claude", "commands", "jira-create.md"),
    os.path.join(ROOT, "scripts", "workers", "ticket-creator.md"),
    os.path.join(ROOT, ".claude", "commands", "ship-it.md"),
    os.path.join(ROOT, ".claude", "skills", "workflow-bug-severity-assessment", "SKILL.md"),
    os.path.join(ROOT, ".claude", "commands", "bug-severity.md"),
]

TEAM_LITERALS = [
    r"vantaca\.atlassian\.net", r"\bVNT\b", r"\bHXP\b", r"AI[ -]DLC", r"home_aidlc",
    r"customfield_\d+", r"Vantaca Home", r"\b10011\b",
]


def test_jira_home_no_person_or_board_literals():
    text = open(F, encoding="utf-8").read()
    assert "712020:aeec48b7-3829-433b-9125-c8c2a4c84e6f" not in text
    assert not re.search(r"\bJay\b", text)
    assert "board 1096" not in text and "board `1096`" not in text
    assert "~/pm-os" not in text


def test_jira_surfaces_have_no_instance_literals():
    offenders = []
    for path in JIRA_SURFACES:
        text = open(path, encoding="utf-8").read()
        for pat in TEAM_LITERALS:
            for m in re.finditer(pat, text, re.IGNORECASE):
                offenders.append(f"{os.path.relpath(path, ROOT)}: {m.group(0)}")
    assert not offenders, "\n".join(offenders)


def test_jira_home_reads_target_from_profile():
    text = open(F, encoding="utf-8").read()
    assert "profile_lib.py --jira-config" in text        # one CLI read for the board target
    assert "fields.epic_name" in text                     # custom fields by semantic name
    assert "<!-- JIRA_DRAFT -->" in text                 # draft format preserved
    assert "Regression Defect" in text and "Unit" in text


def test_jira_create_command_reads_profile():
    text = open(os.path.join(ROOT, ".claude", "commands", "jira-create.md"), encoding="utf-8").read()
    assert "profile_lib.py --jira-config" in text
