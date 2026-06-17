"""Validator for the declarative program-type registry (the Cadence gate).

Sibling of card_schema.py. Makes a malformed program-type registry
structurally impossible: state_model is a known closed-set value, phases
appear only on pipeline types (and pipeline types must have them), every
type's family resolves to a declared family, presentation chips reference
theme tokens ONLY, and every source declares a mode.

As of the reconcile-engine increment this also validates each type's
`emitters` block (the declarative drift -> action rules the reconciler reads):
each emitter must name a non-empty `on` trigger and an `action` in the brief's
closed action set. Still deferred (no producer yet): the read-mode-source ->
no-write-emitter-target cross-check (there are no emitter targets to name yet),
sentinel tool-lists, and the intake block. This is the gate the read-only
Cadence tab and the reconciler both rely on so every row layout and emitter
rule is well-formed.
"""
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "cadence", "programtypes", "registry.json")
TEMPLATE_CSS = os.path.join(ROOT, "ui", "task-board", "themes", "_TEMPLATE.css")
SENTINEL_DIR = os.path.join(ROOT, "scripts", "sentinels")
STATE_MODELS = {"pipeline", "cycle", "target", "register"}
# Brief §3 closed action set — the only actions an emitter may declare.
CLOSED_ACTIONS = {"escalate", "draft-message", "produce-artifact",
                  "propose-update", "draft-ticket"}


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

        if "emitters" in t:
            emitters = t["emitters"]
            if not isinstance(emitters, list):
                errors.append(f"type '{tid}': emitters must be a list")
            else:
                for em in emitters:
                    if not isinstance(em, dict):
                        errors.append(
                            f"type '{tid}': emitter '{em}' is not a dict")
                        continue
                    on = em.get("on")
                    if not isinstance(on, str) or not on.strip():
                        errors.append(
                            f"type '{tid}': emitter has empty or non-string 'on'")
                    action = em.get("action")
                    if action not in CLOSED_ACTIONS:
                        errors.append(
                            f"type '{tid}': emitter action '{action}' not in "
                            f"closed set {sorted(CLOSED_ACTIONS)}")

    return errors


def validate_sentinels(sentinel_dir=None):
    """Validate every sentinel definition in a sentinels dir ([] = all valid).

    Mirrors the registry validation style: load each scripts/sentinels/*.md,
    run sentinel_lib.validate_sentinel, and prefix each error with the file so
    a malformed sentinel fails the gate with an ASCII-safe, actionable message.
    A read-mode-only contract is structural here (a mode: write source is an
    error), matching the brief's sentinel invariant.
    """
    # Deferred import: sentinel_lib imports program_lib, and importing it at
    # module top would widen this gate's import surface for no benefit.
    import sentinel_lib
    sd = sentinel_dir or SENTINEL_DIR
    errors = []
    if not os.path.isdir(sd):
        return errors
    for path in sorted(glob.glob(os.path.join(sd, "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            # Load by absolute path so the gate never reconstructs a root from
            # the dir shape (the dir need not be <root>/scripts/sentinels).
            definition = sentinel_lib.load_sentinel_file(path)
        except Exception as e:
            errors.append(f"sentinel '{name}': could not load: {e}")
            continue
        for e in sentinel_lib.validate_sentinel(definition):
            errors.append(f"sentinel '{name}': {e}")
    return errors


def validate():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    return validate_doc(reg, _theme_tokens()) + validate_sentinels()


if __name__ == "__main__":
    import sys
    errs = validate()
    if errs:
        print("\n".join(errs)); sys.exit(1)
    print("programtypes OK")
