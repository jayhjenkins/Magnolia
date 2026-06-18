#!/usr/bin/env python3
"""starter_sets.py - read cadence/starter-sets.yaml (onboarding-only).

Curated program-type bundles the onboarding concierge offers ONCE at setup
(design brief section 8). This module is NEVER consulted at runtime: a program
type is live iff the operator has >=1 active program of it (activation is
implicit / instance-driven). This reads the curated yaml and guards that every
type id referenced in every bundle exists in the program-type registry - a
dangling starter set (a bundle naming a type the registry does not define) is a
gate failure, surfaced by validate().

Identity-free (invariant #1): bundles name engine program-type ids only, never a
person/team/channel. ASCII-only (invariant #8).
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import program_lib  # noqa: E402 - the registry type-id source of truth

from ruamel.yaml import YAML  # noqa: E402

_yaml = YAML(typ="safe")
STARTER_SETS_PATH = os.path.join(ROOT, "cadence", "starter-sets.yaml")


def load_starter_sets(path=None):
    """Return the parsed starter-sets mapping ({"sets": {...}}), or {} if absent."""
    path = path or STARTER_SETS_PATH
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return _yaml.load(f) or {}


def bundle(name, path=None):
    """Return one bundle dict (label/description/types) by name, or {}."""
    return (load_starter_sets(path).get("sets") or {}).get(name) or {}


def registry_type_ids(root=None):
    """The set of program-type ids the registry defines."""
    reg = program_lib.load_registry(root)
    return {t.get("id") for t in (reg.get("types") or [])}


def validate(path=None, root=None):
    """Return a list of errors ([] = valid).

    Every type id named in every bundle's `types` must exist in the registry.
    A bundle with no `types` list, or a non-list, is itself an error.
    """
    errors = []
    sets = (load_starter_sets(path).get("sets") or {})
    known = registry_type_ids(root)
    for name, spec in sets.items():
        types = (spec or {}).get("types")
        if not isinstance(types, list) or not types:
            errors.append(f"starter set '{name}': 'types' must be a non-empty list")
            continue
        for tid in types:
            if tid not in known:
                errors.append(
                    f"starter set '{name}': type '{tid}' is not in the registry")
    return errors


def main(argv=None):
    errs = validate()
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    print("starter-sets OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
