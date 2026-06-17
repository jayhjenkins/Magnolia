"""scripts/sentinel_lib.py — load, parse, and validate sentinel definitions.

Sentinels are a read-only primitive sibling to background workers. A sentinel
READS sources (transcripts, the PM tracker) and returns structured observation
records that a deterministic harness appends to programs (Task 3). It never
writes files. This module owns the definition contract:

  - load_sentinel(name)      parse scripts/sentinels/<name>.md -> dict
  - list_sentinels()         every shipped def
  - validate_sentinel(def)   structural errors ([] = valid)

The read-only contract is enforced structurally here and at the schema gate
(program_schema.validate_sentinels): every declared source must be mode: read,
observation_kinds must be a non-empty subset of program_lib.OBSERVATION_KINDS,
and the def may not grant a write-capable tool. The enum is NOT redefined here;
it is imported from program_lib (the single source of truth).
"""
import glob
import os
import re

from ruamel.yaml import YAML

import program_lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
_SENTINEL_SUBDIR = os.path.join("scripts", "sentinels")

# Tool grants that would let a sentinel write to the filesystem. Matched against
# the bare tool name (the part before any "(...)" arg spec), case-insensitively.
_WRITE_TOOLS = {"write", "edit", "notebookedit", "multiedit"}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def _sentinel_dir(root=None):
    return os.path.join(root or PM_OS_DIR, _SENTINEL_SUBDIR)


def load_sentinel_file(path):
    """Parse a sentinel definition file at an absolute path into a dict.

    Returns the frontmatter mapping plus a `prompt` key holding the markdown body
    after the closing ---. Raises FileNotFoundError if the file is absent and
    ValueError if the frontmatter is missing or does not parse to a mapping. This
    is the path-keyed primitive; load_sentinel resolves a name to a path and
    delegates here, so callers that already hold a path (e.g. the schema gate)
    never reconstruct a root.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"sentinel '{name}' has missing or malformed frontmatter")
    fm = YAML(typ="safe").load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise ValueError(f"sentinel '{name}' frontmatter is not a mapping")
    definition = dict(fm)
    definition["prompt"] = m.group(2) or ""
    return definition


def load_sentinel(name, root=None):
    """Parse scripts/sentinels/<name>.md into a definition dict (see load_sentinel_file)."""
    return load_sentinel_file(os.path.join(_sentinel_dir(root), name + ".md"))


def list_sentinels(root=None):
    """Return parsed definitions for every scripts/sentinels/*.md ([] if none)."""
    return [load_sentinel_file(p)
            for p in sorted(glob.glob(os.path.join(_sentinel_dir(root), "*.md")))]


def _bare_tool(spec):
    """The tool name from an allowed-tools entry: 'Write(*)' -> 'write'."""
    if not isinstance(spec, str):
        return ""
    return spec.split("(", 1)[0].strip().lower()


def validate_sentinel(definition):
    """Return a list of structural problems with a sentinel def ([] = valid).

    Rules (all ASCII-safe messages):
      - kind must be 'sentinel'
      - name must be a non-empty string
      - every source must declare mode: read (a write source is the violation)
      - observation_kinds must be non-empty and a subset of OBSERVATION_KINDS
      - the prompt body must be non-empty
      - allowed_tools, if present, may not grant a write-capable tool
    """
    errors = []
    if not isinstance(definition, dict):
        return ["sentinel definition is not a mapping"]

    name = definition.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")

    if definition.get("kind") != "sentinel":
        errors.append(
            f"kind must be 'sentinel', got: {definition.get('kind')!r}")

    sources = definition.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list")
    else:
        for src in sources:
            if not isinstance(src, dict):
                errors.append(f"source '{src}' is not a mapping")
                continue
            if src.get("mode") != "read":
                errors.append(
                    f"source '{src.get('kind', '?')}' mode must be 'read' "
                    f"(sentinels are read-only), got: {src.get('mode')!r}")

    kinds = definition.get("observation_kinds")
    if not isinstance(kinds, list) or not kinds:
        errors.append("observation_kinds must be a non-empty list")
    else:
        for k in kinds:
            if k not in program_lib.OBSERVATION_KINDS:
                errors.append(
                    f"observation_kind '{k}' not in the closed enum "
                    f"{sorted(program_lib.OBSERVATION_KINDS)}")

    if not str(definition.get("prompt") or "").strip():
        errors.append("prompt body must be non-empty")

    tools = definition.get("allowed_tools")
    if tools is not None:
        if not isinstance(tools, list):
            errors.append("allowed_tools must be a list when present")
        else:
            for spec in tools:
                if _bare_tool(spec) in _WRITE_TOOLS:
                    errors.append(
                        f"allowed_tools grants write-capable tool '{spec}' "
                        f"(sentinels do not write files)")

    return errors
