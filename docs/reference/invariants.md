# Invariants — the laws that must never break

> Load this first, before acting on the engine. Every other doc links here for the laws.

| # | Law | Why | Enforced by |
|---|-----|-----|-------------|
| 1 | The engine never hardcodes person/team identity — it reads from `profile/` via `scripts/profile_lib.py`. | The engine is shared and team-portable; identity lives only in the per-person profile. | `python3 -m pytest tests/test_engine_no_jay.py` — scans `scripts/workers/*.md`, `.claude/skills/**/*.md`, `.claude/commands/*.md`, `scripts/adapters/**/*.py` against a denylist |
| 2 | Gates stay green before any commit. | A red gate means the engine is broken for everyone who pulls. | `python3 -m pytest` · `python3 scripts/card_schema.py` (→ `registry.json OK`) · `python3 -m pytest tests/test_engine_no_jay.py` · `python3 scripts/program_schema.py` (→ `programtypes OK`) |
| 3 | Card definitions reference theme tokens ONLY — never a hardcoded color, radius, or transition. | Guarantees every card is 100% theme-aware across all Moods. | `python3 scripts/card_schema.py` (`scripts/card_schema.py:50-57`) |
| 4 | Capture team/person nuance to the PROFILE, never into a generated artifact. | Keeps artifacts denylist-clean and the nuance editable. | `profile_lib.set_integration_conventions(...)`; enforced by law #1's test |
| 5 | Anything that writes to the outside world is Tier-2: exactly one plain-language confirm before its first external action. | Blast-radius is consented in plain words; no silent external writes. | `scripts/adapters/__init__.py:54-62` — `publish()` raises `NeedsConfirmation`; factory arms it via `profile_lib.set_integration_confirmed(category, False, provider=...)` |
| 6 | Never delete generated artifacts — append a version suffix (`v1`, `v2`). | History is the audit trail; nothing is silently lost. | Convention; reviewed in `conventions.md` |
| 7 | `~/pm-os` and its port `8742` are retired and no longer in use — this repo, on `localhost:8744`, is the only install. Fact, not a rule change; number kept (not reused) so `(invariant #N)` citations elsewhere stay stable. | N/A | N/A |
| 8 | Code stays portable — OS, shell, and encoding specifics go through `scripts/platform_lib.py`, never hand-rolled. | The platform_lib epic built the OS/shell seam; leaking past it (direct `.sh`/bash calls, `os.name`/`sys.platform` branches, `start_new_session`) reintroduces the Windows crashes it fixed. | `python3 scripts/portability_gate.py` (→ `portability OK`) — a denylist scan over runtime code, allowlisting `platform_lib.py` itself |
| 9 | Cadence program-type definitions are declarative and closed-set — known `state_model`, `phases` only on pipelines, every type's `family` declared, presentation chips reference theme tokens ONLY, every source declares a `mode`. | Makes a malformed program-type registry structurally impossible, so the read-only Cadence tab renders every row layout safely. | `python3 scripts/program_schema.py` (→ `programtypes OK`) — sibling of `card_schema.py`, validates `cadence/programtypes/registry.json` |

_Git is never a user-facing concept: generated changes are presented as **Keep / Undo** (see `meta-factory-core`)._
