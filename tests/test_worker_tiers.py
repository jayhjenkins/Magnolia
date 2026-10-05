import os, re
WORKERS = os.path.join(os.path.dirname(__file__), "..", "scripts", "workers")
EXPECTED = {
    "grad-assessor": "light", "scheduler": "standard",
    "_default": "standard", "message-writer": "standard", "ticket-creator": "standard",
    "eval-analyst": "deep", "researcher": "deep", "product-analyst": "deep",
}


def _tier(name):
    with open(os.path.join(WORKERS, f"{name}.md")) as f:
        fm = f.read().split("---", 2)[1]
    m = re.search(r"^tier:\s*(\S+)\s*$", fm, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else None


def test_every_worker_declares_expected_tier():
    for name, tier in EXPECTED.items():
        assert _tier(name) == tier, f"{name} tier"
