"""Validator for the declarative program-type registry (the Cadence gate).

Sibling of card_schema.py. Makes a malformed program-type registry
structurally impossible: state_model is a known closed-set value, phases
appear only on pipeline types (and pipeline types must have them), every
type's family resolves to a declared family, presentation chips reference
theme tokens ONLY, and every source declares a mode.

This validates only what the early Cadence slices use — emitter, sentinel,
and intake blocks are deliberately deferred to later slices (the seed
registry has none). This is the gate the read-only Cadence tab relies on so
every row layout it renders is well-formed.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "cadence", "programtypes", "registry.json")
TEMPLATE_CSS = os.path.join(ROOT, "ui", "task-board", "themes", "_TEMPLATE.css")
STATE_MODELS = {"pipeline", "cycle", "target", "register"}


def _theme_tokens():
    if not os.path.isfile(TEMPLATE_CSS):
        return set()
    with open(TEMPLATE_CSS, encoding="utf-8") as f:
        return set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", f.read()))


def validate_doc(reg, tokens):
    errors = []

    families = reg.get("families")
    if not isinstance(families, list) or not families:
        errors.append("families must be a non-empty list")
        family_ids = set()
    else:
        family_ids = set()
        for fam in families:
            missing = [k for k in ("id", "label", "order") if k not in fam]
            if missing:
                errors.append(
                    f"family '{fam.get('id', '?')}': missing {', '.join(missing)}")
            if "id" in fam:
                family_ids.add(fam["id"])

    for t in reg.get("types", []):
        tid = t.get("id", "?")
        state_model = t.get("state_model")
        if state_model not in STATE_MODELS:
            errors.append(
                f"type '{tid}': unknown state_model '{state_model}' "
                f"(must be one of {sorted(STATE_MODELS)})")

        has_phases = "phases" in t
        if state_model == "pipeline":
            if not has_phases:
                errors.append(f"type '{tid}': pipeline state_model requires phases")
        elif has_phases:
            errors.append(
                f"type '{tid}': phases are only allowed on pipeline state_model")

        if t.get("family") not in family_ids:
            errors.append(
                f"type '{tid}': unknown family '{t.get('family')}'")

        presentation = t.get("presentation", {})
        for chip, tok in presentation.get("chip_tokens", {}).items():
            if not isinstance(tok, str) or not tok.startswith("--"):
                errors.append(
                    f"type '{tid}': chip '{chip}' value '{tok}' is not a theme token")
            elif tok not in tokens:
                errors.append(
                    f"type '{tid}': chip '{chip}' token '{tok}' not in theme")

        for src in t.get("sources", []):
            if "mode" not in src:
                errors.append(
                    f"type '{tid}': source '{src.get('kind', '?')}' has no mode")

    return errors


def validate():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    return validate_doc(reg, _theme_tokens())


if __name__ == "__main__":
    import sys
    errs = validate()
    if errs:
        print("\n".join(errs)); sys.exit(1)
    print("programtypes OK")
