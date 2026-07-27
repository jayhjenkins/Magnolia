"""Validator for the declarative program-type registry (the Cadence gate).

Sibling of card_schema.py. Makes a malformed program-type registry
structurally impossible: state_model is a known closed-set value, phases
appear only on pipeline types (and pipeline types must have them), every
type's family resolves to a declared family, presentation chips reference
theme tokens ONLY, and every source declares a mode.

As of the reconcile-engine increment this also validates each type's
`emitters` block (the declarative drift -> action rules the reconciler reads):
each emitter must name a non-empty `on` trigger and an `action` in the brief's
closed action set. As of the birth-path increment it also validates each
type's optional `intake` block (brief §3): a `route` in the closed routing set,
and for a `candidate` route a `birth_threshold` (min_independent_sources as a
non-negative int with bool rejected, the two explicit-declaration flags as
bools, at least one key present), `bootstrap_emissions` actions in CLOSED_ACTIONS,
and `signals` as non-empty strings. Still deferred (no producer yet): the
read-mode-source -> no-write-emitter-target cross-check (there are no emitter
targets to name yet) and sentinel tool-lists. This is the gate the read-only
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
# Brief §3 closed intake routing set — how a type's exhaust is routed.
INTAKE_ROUTES = {"observe", "capture", "candidate", "ignore"}
# Brief §3 closed emitter trigger set — the only triggers an emitter may declare.
EMITTER_TRIGGERS = {"drift:broken", "candidate-ripe", "phase-advance-proposable",
                    "cycle-fresh", "completion-verified", "silent-too-long"}


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
            else:
                # An optional exit_checkpoint names a per-instance checkpoint id
                # (the fact/proposal door). It is statically a string here; the
                # cross-check against an instance's checkpoints is runtime.
                for p in t.get("phases") or []:
                    if not isinstance(p, dict):
                        continue
                    if "exit_checkpoint" in p and not isinstance(
                            p["exit_checkpoint"], str):
                        errors.append(
                            f"type '{tid}': phase '{p.get('id', '?')}' "
                            f"exit_checkpoint must be a string")
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
                    elif on not in EMITTER_TRIGGERS:
                        errors.append(
                            f"type '{tid}': emitter trigger '{on}' not in "
                            f"closed set {sorted(EMITTER_TRIGGERS)}")
                    action = em.get("action")
                    if action not in CLOSED_ACTIONS:
                        errors.append(
                            f"type '{tid}': emitter action '{action}' not in "
                            f"closed set {sorted(CLOSED_ACTIONS)}")
                    if "max_nudges_per_person_per_week" in em:
                        v = em["max_nudges_per_person_per_week"]
                        # bool is an int subclass; reject it explicitly so a
                        # True/False cannot pose as a count.
                        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                            errors.append(
                                f"type '{tid}': emitter "
                                f"max_nudges_per_person_per_week must be a "
                                f"non-negative int, got {type(v).__name__}")
                    if "min_active_families" in em:
                        v = em["min_active_families"]
                        # The cross-program rollup's >=N-families dispatch gate.
                        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                            errors.append(
                                f"type '{tid}': emitter min_active_families must "
                                f"be a non-negative int, got {type(v).__name__}")
                    if "fire_weekday" in em:
                        v = em["fire_weekday"]
                        if isinstance(v, bool) or not isinstance(v, int) or v < 1 or v > 7:
                            errors.append(
                                f"type '{tid}': emitter fire_weekday must be "
                                f"1-7 (ISO Mon-Sun), got {v!r}")

        # Archive field (Task 3) — when to archive a program after silent cycles.
        if "archive_after_silent_cycles" in t:
            v = t["archive_after_silent_cycles"]
            # bool is an int subclass; reject it explicitly.
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errors.append(
                    f"type '{tid}': archive_after_silent_cycles must be a "
                    f"non-negative int, got {type(v).__name__}")

        # Intake block (brief §3) — how this type's exhaust is routed and,
        # for a `candidate` route, when accumulated evidence births a program.
        if "intake" in t:
            intake = t["intake"]
            if not isinstance(intake, dict):
                errors.append(f"type '{tid}': intake must be a dict")
            else:
                route = intake.get("route")
                if route not in INTAKE_ROUTES:
                    errors.append(
                        f"type '{tid}': intake route '{route}' not in "
                        f"closed set {sorted(INTAKE_ROUTES)}")

                bt = intake.get("birth_threshold")
                if route == "candidate" and not isinstance(bt, dict):
                    errors.append(
                        f"type '{tid}': intake route 'candidate' requires a "
                        f"birth_threshold dict")
                elif isinstance(bt, dict):
                    recognized = ("min_independent_sources",
                                  "or_explicit_declaration",
                                  "explicit_declaration_only")
                    if not any(k in bt for k in recognized):
                        errors.append(
                            f"type '{tid}': intake birth_threshold must declare "
                            f"at least one of {list(recognized)}")
                    if "min_independent_sources" in bt:
                        v = bt["min_independent_sources"]
                        # bool is an int subclass; reject it explicitly so a
                        # True/False cannot pose as a count.
                        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                            errors.append(
                                f"type '{tid}': intake birth_threshold "
                                f"min_independent_sources must be a non-negative "
                                f"int, got {type(v).__name__}")
                    for k in ("or_explicit_declaration",
                              "explicit_declaration_only"):
                        if k in bt and not isinstance(bt[k], bool):
                            errors.append(
                                f"type '{tid}': intake birth_threshold {k} "
                                f"must be a bool, got {type(bt[k]).__name__}")

                if "bootstrap_emissions" in intake:
                    be = intake["bootstrap_emissions"]
                    if not isinstance(be, list):
                        errors.append(
                            f"type '{tid}': intake bootstrap_emissions must be "
                            f"a list")
                    else:
                        for emi in be:
                            if not isinstance(emi, dict):
                                errors.append(
                                    f"type '{tid}': intake bootstrap_emissions "
                                    f"entry must be a dict, got "
                                    f"{type(emi).__name__}")
                                continue
                            action = emi.get("action")
                            if action not in CLOSED_ACTIONS:
                                errors.append(
                                    f"type '{tid}': intake bootstrap_emissions "
                                    f"action '{action}' not in closed set "
                                    f"{sorted(CLOSED_ACTIONS)}")

                if "signals" in intake:
                    signals = intake["signals"]
                    if not isinstance(signals, list):
                        errors.append(
                            f"type '{tid}': intake signals must be a list")
                    else:
                        for sig in signals:
                            if not isinstance(sig, str) or not sig.strip():
                                errors.append(
                                    f"type '{tid}': intake signals entry must "
                                    f"be a non-empty string")

        # Type-level default items (used to seed cycle programs). Optional;
        # when present it must be a list of dicts. Instance items live on
        # program files and are not gated here.
        if "items" in t:
            items = t["items"]
            if not isinstance(items, list):
                errors.append(
                    f"type '{tid}': items must be a list, got "
                    f"{type(items).__name__}")
            else:
                for it in items:
                    if not isinstance(it, dict):
                        errors.append(
                            f"type '{tid}': items entry must be a dict, got "
                            f"{type(it).__name__}")

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
