import os, re, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = (
    glob.glob(os.path.join(ROOT, "scripts", "workers", "*.md")) +
    glob.glob(os.path.join(ROOT, ".claude", "skills", "**", "*.md"), recursive=True) +
    glob.glob(os.path.join(ROOT, ".claude", "commands", "*.md")) +
    glob.glob(os.path.join(ROOT, "scripts", "adapters", "**", "*.py"), recursive=True) +
    glob.glob(os.path.join(ROOT, "cadence", "**", "*.json"), recursive=True)
)
DENY = [r"\bjay\b", r"jay-voice", r"712020:aeec48b7-3829-433b-9125-c8c2a4c84e6f", r"board 1096", r"~/pm-os", r"/Users/"]

def test_no_per_person_or_per_team_literals_in_engine_prompts():
    offenders = []
    for f in TARGETS:
        text = open(f, encoding="utf-8").read()
        for pat in DENY:
            if re.search(pat, text, re.IGNORECASE):
                offenders.append(f"{os.path.relpath(f, ROOT)}: /{pat}/")
    assert not offenders, "Per-person/per-team literals remain:\n" + "\n".join(offenders)

def test_vantaca_still_allowed():
    joined = "".join(open(f, encoding="utf-8").read() for f in TARGETS)
    assert "Vantaca" in joined
