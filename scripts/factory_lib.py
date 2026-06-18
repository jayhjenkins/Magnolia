"""scripts/factory_lib.py — shared mechanism for the meta-create-* factory skills.

Two jobs:
  1. commit_and_emit_receipt() — stage EXACTLY the scaffolded files (precise
     staging, not `git add -A`), commit, and emit a `receipt` card carrying
     revert_commit + receipt_summary. The existing task_server.undo_receipt
     git-revert handler powers one-tap Undo unchanged. Git stays invisible — the
     board only ever shows Keep / Undo.
  2. validate_worker() — structural gate for a scaffolded worker .md (frontmatter
     parses + required fields). Denylist-cleanliness is enforced separately by the
     standing tests/test_engine_no_jay.py guard, so it is not duplicated here.
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import task_lib  # noqa: E402

_REQUIRED_WORKER_FIELDS = ("name", "description", "priority", "tier", "match",
                           "allowed_tools", "timeout", "max_turns")


class FactoryError(RuntimeError):
    """A factory step failed in an operator-actionable way."""


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def commit_and_emit_receipt(summary, files, kind, root=None):
    """Stage exactly `files`, commit, emit a receipt card. Returns the receipt task id.

    summary : human one-liner shown on the card ("a sample worker").
    files   : repo-relative paths the factory created/modified (precise staging).
    kind    : 'worker' | 'card-type' | 'adapter' (stored as factory_kind).
    Raises FactoryError if `files` is empty or nothing was staged (so a no-op
    scaffold can't strand a dangling receipt)."""
    repo = root or PM_OS_DIR
    if not files:
        raise FactoryError("no files to commit")
    add = _git(repo, "add", "--", *files)
    if add.returncode != 0:
        raise FactoryError(f"git add failed: {add.stderr.strip()[:300]}")
    # `git diff --cached --quiet` returns 0 when NOTHING is staged.
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        raise FactoryError("scaffold produced no committable changes")
    # Capture the pre-commit HEAD so we can roll back if the receipt can't be
    # emitted. On a brand-new repo with no commits this is non-zero (no HEAD yet).
    prev = _git(repo, "rev-parse", "HEAD")
    commit = _git(repo, "commit", "-m", f"factory: {summary}")
    if commit.returncode != 0:
        raise FactoryError(f"git commit failed: {commit.stderr.strip()[:300]}")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Transactional: a committed scaffold with no receipt card strands a change
    # with no Undo affordance (git is invisible; the receipt is the only Undo).
    # If receipt emission fails AFTER the commit, soft-reset to the pre-commit
    # HEAD — which preserves the scaffolded files staged in the working tree —
    # and raise, so we never leave an orphan commit.
    try:
        receipt_id, _ = task_lib.create_task(
            f"Built: {summary}", queue="human", domain="ops", creator="agent",
            description="The factory built this for you. One-tap Undo reverts it cleanly.",
            card_type="receipt")
        task_lib.update_task(receipt_id, changes={
            "revert_commit": sha,
            "receipt_summary": summary,
            "factory_kind": kind,
        })
    except Exception as e:
        if prev.returncode == 0:
            _git(repo, "reset", "--soft", prev.stdout.strip())
            rolled_back = " rolled back the commit;"
        else:
            # Unborn HEAD (no prior commit): there is no prior state to soft-reset
            # to, so this first commit is left in place. Accepted gap — PM-OS is
            # always an established repo with history, so this path is unreachable
            # in practice; not worth an update-ref dance for it.
            rolled_back = ""
        raise FactoryError(
            f"receipt emission failed;{rolled_back} scaffolded files preserved "
            f"in the working tree: {e}") from e
    return receipt_id


def validate_worker(path, root=None):
    """Return a list of structural problems with a scaffolded worker .md ([] = ok).

    Validates the RAW frontmatter block, not task_dispatch's coerced view (which
    injects defaults for priority/timeout/max_turns/match and would mask a missing
    key). A relative `path` is resolved against `root` (defaults to PM_OS_DIR)."""
    import re
    from ruamel.yaml import YAML
    abspath = path if os.path.isabs(path) else os.path.join(root or PM_OS_DIR, path)
    try:
        text = open(abspath, encoding="utf-8").read()
    except OSError as e:
        return [f"could not read worker file: {e}"]
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not m:
        return ["missing or malformed YAML frontmatter"]
    try:
        fm = YAML(typ="safe").load(m.group(1)) or {}
    except Exception as e:
        return [f"frontmatter did not parse: {e}"]
    if not isinstance(fm, dict):
        return ["frontmatter is not a mapping"]
    problems = [f"missing required field: {f}"
                for f in _REQUIRED_WORKER_FIELDS if f not in fm]
    if not (m.group(2) or "").strip():
        problems.append("empty prompt body")
    return problems


def validate_card_type(name, registry_path=None):
    """Return a list of problems with a card type in the registry ([] = ok).

    Runs the full card-schema gate — token-only AND every referenced signal /
    action / body-renderer must already exist. That existence check is exactly
    what enforces 'registry-composition-only': a card type referencing a NEW
    signal/action/renderer (which would need JS) fails here. Also confirms the
    named type is present."""
    import json
    import card_schema
    path = registry_path or card_schema.REGISTRY
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except OSError as e:
        return [f"could not read registry: {e}"]
    except json.JSONDecodeError as e:
        return [f"registry is not valid JSON: {e}"]
    errs = card_schema.validate_doc(
        reg, card_schema._declared_signal_ids(), card_schema._theme_tokens())
    if name not in reg.get("cardTypes", {}):
        errs.append(f"card type '{name}' not found in the registry")
    return errs


def validate_program_type(name, registry_path=None):
    """Return a list of problems with a program type in the registry ([] = ok).

    Runs the full program-schema gate (closed state_model set, phases only on
    pipelines, declared family, token-only chips, every source mode, closed
    emitter action/trigger sets, nudge-cap shape, intake routing) and confirms
    the named type is present. meta-create-program-type runs this GREEN before
    committing a scaffolded entry — the program-type analogue of
    validate_card_type."""
    import json
    import program_schema
    path = registry_path or program_schema.REGISTRY
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except OSError as e:
        return [f"could not read registry: {e}"]
    except json.JSONDecodeError as e:
        return [f"registry is not valid JSON: {e}"]
    errs = program_schema.validate_doc(reg, program_schema._theme_tokens())
    if name not in {t.get("id") for t in reg.get("types", [])}:
        errs.append(f"program type '{name}' not found in the registry")
    return errs


def _protocols_in(contract_module):
    """Return the typing.Protocol subclasses defined in a family's _contract module.

    Restricted to Protocols whose __module__ is the contract module itself, so
    imported Protocol bases don't leak in. The contract is the single source of
    truth — validate_adapter requires exactly one, so an ambiguous contract
    (zero or multiple Protocols) is a hard error rather than a silent pick."""
    import inspect
    return [obj for _, obj in inspect.getmembers(contract_module, inspect.isclass)
            if getattr(obj, "_is_protocol", False)
            and obj.__module__ == contract_module.__name__]


def _conformance_problems(mod, proto):
    """Return a list of ways `mod` fails to satisfy Protocol `proto` ([] = ok).

    Pure interface check: every public Protocol method must be present, callable,
    and expose the Protocol's parameter names. Never invokes the methods."""
    import inspect
    problems = []
    for name, _ in inspect.getmembers(proto, callable):
        if name.startswith("_"):
            continue
        fn = getattr(mod, name, None)
        if not callable(fn):
            problems.append(f"missing or non-callable method: {name}")
            continue
        # Intentionally a name-subset check — not verifying order/arity/kind (YAGNI).
        want = [p for p in inspect.signature(getattr(proto, name)).parameters if p != "self"]
        have = list(inspect.signature(fn).parameters)
        for p in want:
            if p not in have:
                problems.append(f"{name}() missing expected parameter '{p}'")
    return problems


def validate_adapter(family, provider, root=None):
    """Return a list of conformance problems for a scaffolded adapter ([] = ok).

    Imports the family's _contract Protocol and the scaffolded module and checks
    interface conformance. Import-only — does NOT call publish()/sync(), so no
    external creds are needed. The import itself is part of the gate (syntax +
    resolvable imports)."""
    import importlib
    scripts_dir = os.path.join(root, "scripts") if root else SCRIPT_DIR
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        contract = importlib.import_module(f"adapters.{family}._contract")
    except Exception as e:
        return [f"could not import contract for family '{family}': {e}"]
    protos = _protocols_in(contract)
    if len(protos) != 1:
        return [f"expected exactly one Protocol in adapters.{family}._contract, "
                f"found {len(protos)}"]
    proto = protos[0]
    try:
        mod = importlib.import_module(f"adapters.{family}.{provider}")
    except Exception as e:
        return [f"adapter import failed: {e}"]
    return _conformance_problems(mod, proto)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("commit-and-receipt")
    c.add_argument("--summary", required=True)
    c.add_argument("--kind", required=True)
    c.add_argument("files", nargs="+")
    v = sub.add_parser("validate-worker")
    v.add_argument("path")
    vc = sub.add_parser("validate-card-type")
    vc.add_argument("name")
    vp = sub.add_parser("validate-program-type")
    vp.add_argument("name")
    va = sub.add_parser("validate-adapter")
    va.add_argument("family")
    va.add_argument("provider")
    args = ap.parse_args()
    if args.cmd == "commit-and-receipt":
        print(commit_and_emit_receipt(args.summary, args.files, args.kind))
    elif args.cmd == "validate-worker":
        probs = validate_worker(args.path)
        if probs:
            print("\n".join(probs)); sys.exit(1)
        print("ok")
    elif args.cmd == "validate-card-type":
        probs = validate_card_type(args.name)
        if probs:
            print("\n".join(probs)); sys.exit(1)
        print("ok")
    elif args.cmd == "validate-program-type":
        probs = validate_program_type(args.name)
        if probs:
            print("\n".join(probs)); sys.exit(1)
        print("ok")
    elif args.cmd == "validate-adapter":
        probs = validate_adapter(args.family, args.provider)
        if probs:
            print("\n".join(probs)); sys.exit(1)
        print("ok")
