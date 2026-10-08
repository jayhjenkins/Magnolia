import os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(ROOT, "scripts", "workers", "ticket-creator.md")

def test_ticket_creator_reads_jira_target_from_profile():
    text = open(F, encoding="utf-8").read()
    assert "712020:aeec48b7-3829-433b-9125-c8c2a4c84e6f" not in text
    assert "board 1096" not in text
    assert not re.search(r"\bJay\b", text)
    assert "profile/integrations.yaml" in text
    assert "Vantaca" not in text            # site/team come from the profile, not the worker
    assert "profile_lib.py --jira-config" in text
    assert "Regression Defect" in text and "Unit" in text
