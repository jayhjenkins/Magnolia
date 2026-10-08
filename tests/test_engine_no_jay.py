import os, re, glob, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (directory, glob pattern) pairs that make up the shipped engine prompts/code.
SCAN = [
    ("scripts/workers", "*.md"),
    ("scripts/sentinels", "*.md"),
    (".claude/skills", "**/*.md"),
    (".claude/commands", "*.md"),
    ("scripts/adapters", "**/*.py"),
    ("cadence", "**/*.json"),
]


def _tracked_targets():
    """Git-tracked files under SCAN.

    Only what the repo ships is scanned: an owner's gitignored personal skills
    (which legitimately carry their own identity) live on disk beside the engine
    and must not fail this gate. Falls back to a filesystem glob without git.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--"] + [d for d, _ in SCAN],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        files = [os.path.join(ROOT, p) for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = [f for d, pat in SCAN
                 for f in glob.glob(os.path.join(ROOT, d, pat), recursive=True)]
    exts = {os.path.splitext(pat)[1] for _, pat in SCAN}
    return sorted(f for f in files
                  if os.path.isfile(f) and os.path.splitext(f)[1] in exts)


TARGETS = _tracked_targets()

# Matched case-insensitively.
DENY = [
    r"\bjay\b", r"jay-voice", r"712020:aeec48b7-3829-433b-9125-c8c2a4c84e6f", r"board 1096",
    r"~/pm-os", r"/Users/",
    # Real work email addresses.
    r"@vantaca\.com", r"@hoai\.com",
    # Owner-personal scorecard / rocks / activation-metric machinery (gitignored,
    # lives only on the owner's machine) - the shared engine must not depend on it.
    r"metric-quarterly-rocks", r"metric-scorecard-fetch", r"pm-agent-activation",
    r"build_scorecard_dashboard", r"Resident Experience (EOS )?scorecard",
]
# Matched case-sensitively (a specific quarter's named Rocks, not generic "rocks").
DENY_CASE = [r"\bQ[1-4] 20\d\d Rocks\b"]


def test_scans_tracked_engine_files():
    assert TARGETS, "no engine files found to scan"


def test_no_per_person_or_per_team_literals_in_engine_prompts():
    offenders = []
    for f in TARGETS:
        text = open(f, encoding="utf-8").read()
        for pat in DENY:
            if re.search(pat, text, re.IGNORECASE):
                offenders.append(f"{os.path.relpath(f, ROOT)}: /{pat}/")
        for pat in DENY_CASE:
            if re.search(pat, text):
                offenders.append(f"{os.path.relpath(f, ROOT)}: /{pat}/")
    assert not offenders, "Per-person/per-team literals remain:\n" + "\n".join(offenders)


def test_owner_personal_machinery_is_not_shipped():
    """The owner's scorecard/rocks/activation-metric files stay untracked."""
    personal = [
        ".claude/skills/metric-quarterly-rocks",
        ".claude/skills/metric-scorecard-fetch",
        ".claude/skills/workflow-pm-agent-activation-metric",
        ".claude/commands/update-rocks.md",
        ".claude/commands/scorecard-update.md",
        ".claude/commands/pm-agent-activation.md",
        "scripts/build_scorecard_dashboard.py",
        "requirements-rocks.txt",
    ]
    try:
        out = subprocess.run(["git", "ls-files", "--"] + personal, cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return  # no git: nothing to assert about tracking
    assert not out.strip(), "Owner-personal files are tracked:\n" + out


def test_vantaca_still_allowed():
    joined = "".join(open(f, encoding="utf-8").read() for f in TARGETS)
    assert "Vantaca" in joined
