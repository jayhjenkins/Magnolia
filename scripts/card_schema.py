"""Validator for the declarative card registry (the design-system gate).

Enforces §9: card definitions reference theme tokens ONLY (no hardcoded colors),
every referenced signal/action exists, every signal id has a JS predicate
(cross-checked vs signal-ids.txt), and bodies name a known renderer or null.
This is the gate the future factory runs before writing a new card type.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "ui", "task-board", "cardtypes", "registry.json")
SIGNAL_IDS = os.path.join(ROOT, "ui", "task-board", "cardtypes", "signal-ids.txt")
TEMPLATE_CSS = os.path.join(ROOT, "ui", "task-board", "themes", "_TEMPLATE.css")
BODY_RENDERERS = {"diff", "preview", "agreement", "program-preview"}
SLOT_ORDER = ["head", "title", "context", "signals", "body", "actions"]

_COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"           # hex
    r"|\b(?:rgba?|hsla?|oklch|lab)\(" # functional color
    r"|\b\d+(?:\.\d+)?(?:px|rem|em)\b" # sizes
    r"|\b\d+%"                          # percentages
)


def _theme_tokens():
    if not os.path.isfile(TEMPLATE_CSS):
        return set()
    with open(TEMPLATE_CSS, encoding="utf-8") as f:
        return set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", f.read()))


def _declared_signal_ids():
    if not os.path.isfile(SIGNAL_IDS):
        return set()
    with open(SIGNAL_IDS, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


def validate_doc(reg, signal_ids, tokens, body_renderers=BODY_RENDERERS):
    errors = []
    if reg.get("slotOrder", []) != SLOT_ORDER:
        errors.append(f"slotOrder must be exactly {SLOT_ORDER}")
    cat_sig = reg.get("signals", {})
    cat_act = reg.get("actions", {})

    for group_name, group in (("signals", cat_sig), ("actions", cat_act)):
        for name, spec in group.items():
            for tok in spec.get("tokens", []):
                if not tok.startswith("--"):
                    errors.append(f"{group_name}.{name}: '{tok}' is not a theme token")
                elif tok not in tokens:
                    errors.append(f"{group_name}.{name}: token '{tok}' not in theme")
            for k, v in spec.items():
                if isinstance(v, str) and _COLOR_RE.search(v):
                    errors.append(f"{group_name}.{name}.{k}: hardcoded color/size '{v}'")
            if group is cat_sig and name not in signal_ids:
                errors.append(f"signal '{name}' has no predicate in signal-ids.txt")

    for ct, spec in reg.get("cardTypes", {}).items():
        sigs = spec.get("signals", [])
        if sigs != "auto":
            for s in sigs:
                if s not in cat_sig:
                    errors.append(f"cardType '{ct}': unknown signal '{s}'")
        for a in spec.get("actions", []):
            if a not in cat_act:
                errors.append(f"cardType '{ct}': unknown action '{a}'")
        body = spec.get("body")
        if body is not None and body not in body_renderers:
            errors.append(f"cardType '{ct}': unknown body renderer '{body}'")
    return errors


def validate():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    return validate_doc(reg, _declared_signal_ids(), _theme_tokens())


if __name__ == "__main__":
    import sys
    errs = validate()
    if errs:
        print("\n".join(errs)); sys.exit(1)
    print("registry.json OK")
