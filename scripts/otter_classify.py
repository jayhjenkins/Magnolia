#!/usr/bin/env python3
"""
Transcript post-processing: domain classification, YAML front matter, file organization.

Usage:
    python3 otter_classify.py --backfill           # process all unclassified transcripts
    python3 otter_classify.py path/to/file.txt     # process a single file
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Engine wiring ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib  # noqa: E402
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402

# ── Paths (profile-driven) ───────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
MEETINGS_DIR = Path(profile_lib.PM_OS_DIR) / profile_lib.transcript_config()["target"]
STATE_FILE = Path(profile_lib.transcript_state_dir()) / "downloaded.json"

log = logging.getLogger(__name__)
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

# ── Classification ─────────────────────────────────────────────────────────────

# Generic fallback taxonomy (no team/product names - invariant #1). A profile
# can replace it wholesale with config.yaml `meeting_domains: {path: description}`.
_GENERIC_DOMAINS = {
    "recruiting": "candidate interviews, hiring discussions",
    "product":    "product feature work, product team meetings, platform / technical work",
    "leadership": "1:1s, exec intros, cross-functional syncs, team standups",
    "strategy":   "roadmap, quarterly planning, vendor strategy, partner intros",
    "customer":   "customer calls, demos, prospect calls, CS reviews",
    "general":    "anything else",
}
_MONTH_DIR = re.compile(r"^\d{4}-\d{2}$")


def domain_taxonomy(root=None, meetings_dir=None) -> dict:
    """{domain path: description} the classifier may choose from.

    1. profile config.yaml `meeting_domains` (dict or list) - the operator's own.
    2. else the generic set, with `product` split into the product areas that
       already exist as folders under <meetings>/product/ (data-driven, so an
       install that has product/<area>/ keeps classifying into those areas).
    `general` is always present as the catch-all."""
    cfg = (profile_lib.config(root) or {}).get("meeting_domains")
    if isinstance(cfg, dict) and cfg:
        tax = {str(k).strip().strip("/"): str(v or "").strip() for k, v in cfg.items() if k}
    elif isinstance(cfg, list) and cfg:
        tax = {str(k).strip().strip("/"): "" for k in cfg if k}
    else:
        tax = dict(_GENERIC_DOMAINS)
        product_dir = Path(meetings_dir or MEETINGS_DIR) / "product"
        try:
            areas = sorted(p.name for p in product_dir.iterdir()
                           if p.is_dir() and not _MONTH_DIR.match(p.name)
                           and not p.name.startswith("."))
        except OSError:
            areas = []
        if areas:
            del tax["product"]
            tax.update({f"product/{a}": f"{a} product area meetings" for a in areas})
    tax.setdefault("general", "anything else")
    return tax


def _taxonomy_text(tax: dict) -> str:
    width = max(len(k) for k in tax) + 2
    return "\n".join(f"- {k.ljust(width)}({v})" if v else f"- {k}" for k, v in tax.items())


def _build_classify_prompt(title: str, content_preview: str) -> str:
    company = profile_lib.company()
    persona = profile_lib.persona()
    role = "product manager" if persona == "pm" else persona
    identity = f"a {role} at {company}" if company else f"a {role}"
    return (
        f"You are classifying meeting transcripts for {identity}. "
        f"Respond with ONLY the domain path, nothing else.\n\n"
        f"Domains:\n{_taxonomy_text(domain_taxonomy())}\n\n"
        f"Title: {title}\n"
        f"Content preview: {content_preview[:600]}"
    )


def _first_present(tax, *candidates):
    for c in candidates:
        if c in tax:
            return c
    return None


def _keyword_classify(title: str, filename_hint: str = "") -> str:
    """Keyword-based fallback classifier. filename_hint supplements the title.
    Only ever returns a domain in the active taxonomy."""
    tax = domain_taxonomy()
    t = (title + " " + filename_hint).lower()
    # A domain (or product area) named in the title wins: "Payments standup".
    for path in tax:
        leaf = path.rsplit("/", 1)[-1].lower()
        if path != "general" and len(leaf) >= 3 and (
                leaf in t or (leaf.endswith("s") and len(leaf) > 4 and leaf[:-1] in t)):
            return path
    rules = [
        (("interview", "hiring", "candidate"), ("recruiting",)),
        (("l10", "standup", "stand-up", "1:1", "1-1", "one on one"), ("leadership",)),
        (("platform", "api", "infrastructure"), ("product/platform", "product")),
        (("customer", "demo", "prospect", "cs review"), ("customer",)),
        (("roadmap", "strategy", "quarterly", "vendor", "partner", "intro to"), ("strategy",)),
    ]
    for words, targets in rules:
        if any(w in t for w in words):
            hit = _first_present(tax, *targets)
            if hit:
                return hit
    return "general"


def classify_domain(title: str, content_preview: str, filename_hint: str = "") -> str:
    """Classify a transcript into one of the valid domain paths via the LLM.
    Falls back to keyword rules on any failure."""
    prompt = _build_classify_prompt(title, content_preview)
    valid = set(domain_taxonomy())
    try:
        model = profile_lib.resolve_model("light")
        cmd, harness_name = harness_lib.build_oneshot_cmd(
            prompt, model, max_turns=1,
        )
        env = platform_lib.headless_harness_env(harness_name)
        cmd, prompt_stdin = harness_lib.stdin_prompt(cmd)
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True, input=prompt_stdin, **platform_lib.text_kwargs(), timeout=30,
            cwd=str(profile_lib.PM_OS_DIR),
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = (harness_lib.unwrap_oneshot_result(result.stdout, harness_name) or "").strip().lower()
            raw = re.sub(r'["\'\n]', "", raw).strip().rstrip(".")
            if raw in valid:
                log.info("    LLM classified -> %s", raw)
                return raw
            log.warning("    LLM returned invalid domain %r, falling back to keywords", raw)
        else:
            log.warning("    LLM classify failed (rc=%d), falling back to keywords", result.returncode)
    except Exception as exc:
        log.warning("    LLM classify error: %s, falling back to keywords", exc)

    domain = _keyword_classify(title, filename_hint=filename_hint)
    log.info("    Keyword classified -> %s", domain)
    return domain


# ── Metadata extraction ────────────────────────────────────────────────────────

def _parse_timestamp_seconds(ts: str) -> int:
    """Parse HH:MM:SS timestamp to total seconds."""
    try:
        parts = ts.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return 0


def extract_metadata(
    txt_path: Path,
    speech_id: Optional[str] = None,
    downloaded_state: Optional[dict] = None,
) -> dict:
    """
    Extract metadata for YAML front matter.
    For Otter files (speech_id provided): uses downloaded_state + file content.
    For manual files: parses plain-text header.
    """
    text = txt_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    metadata: dict = {}

    if speech_id:
        # ── Synced file (Otter / Granola) ─────────────────────────────────────
        # transcript_id is the provider-neutral key; otter_id is still written
        # for Otter meetings (build_front_matter) so older readers keep working.
        metadata["transcript_id"] = speech_id
        metadata["transcript_provider"] = profile_lib.transcript_config()["provider"]

        # Title: prefer downloaded_state, fall back to # header
        if downloaded_state and speech_id in downloaded_state:
            metadata["title"] = (downloaded_state[speech_id].get("title") or "").strip()
        if not metadata.get("title"):
            for line in lines[:5]:
                if line.startswith("# "):
                    metadata["title"] = line[2:].strip()
                    break

        # Date from "Date: YYYY-MM-DD HH:MM" (Otter) or ISO "YYYY-MM-DDTHH:MM" (Granola)
        for line in lines[:5]:
            m = re.match(r"Date:\s*(\d{4}-\d{2}-\d{2})(?:[\sT]+(\d{2}:\d{2}))?", line)
            if m:
                metadata["date"] = m.group(1)
                if m.group(2):
                    metadata["time"] = m.group(2).replace(":", "-")
                break

        # Attendees header (written by granola_sync): "Attendees: A, B"
        for line in lines[:6]:
            if line.lower().startswith("attendees:"):
                raw = line[line.index(":") + 1:].strip()
                names = [p.strip() for p in raw.split(",") if p.strip()]
                if names:
                    metadata["participants"] = names
                break

        # Participants + duration: scan [HH:MM:SS] Speaker: lines
        speakers: set = set()
        last_ts_seconds = 0
        for line in lines:
            m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]\s+(.+?):\s", line)
            if m:
                ts_seconds = _parse_timestamp_seconds(m.group(1))
                last_ts_seconds = max(last_ts_seconds, ts_seconds)
                speaker = m.group(2).strip()
                if speaker and speaker.lower() != "unknown":
                    speakers.add(speaker)
        if speakers and not metadata.get("participants"):
            metadata["participants"] = sorted(speakers)
        if last_ts_seconds > 0:
            metadata["duration_minutes"] = round(last_ts_seconds / 60)

    else:
        # ── Manual file ───────────────────────────────────────────────────────
        for line in lines[:10]:
            if line.startswith("Meeting Title:"):
                metadata["title"] = line[len("Meeting Title:"):].strip()
            elif line.lower().startswith("date:") and "date" not in metadata:
                raw_date = line[line.index(":") + 1:].strip()
                # Try YYYY-MM-DD, then "Feb 10", then "Feb 10, 2026"
                for fmt in ("%Y-%m-%d", "%b %d", "%B %d", "%b %d, %Y", "%B %d, %Y"):
                    try:
                        dt = datetime.strptime(raw_date, fmt)
                        if dt.year == 1900:
                            dt = dt.replace(year=datetime.now().year)
                        metadata["date"] = dt.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            elif line.lower().startswith("attendees:"):
                raw = line[line.index(":") + 1:].strip()
                metadata["participants"] = [p.strip() for p in raw.split(",") if p.strip()]

        # Infer title from filename if not found
        if not metadata.get("title"):
            stem = txt_path.stem
            metadata["title"] = re.sub(r"[-_]", " ", stem).title()

        # Infer participants from filename if no Attendees line
        if not metadata.get("participants"):
            stem = txt_path.stem.lower()
            parts = re.split(r"[-_]", stem)
            # The operator (from the profile) is named in full when their first
            # name appears in the filename.
            operator = (profile_lib.display_name() or "").strip()
            op_first = operator.split()[0].lower() if operator else ""
            # Keep parts that look like names (>2 chars, not digits-only, not connectors)
            skip = {"and", "the", "with", "for"} | ({op_first} if op_first else set())
            names = [
                p.title()
                for p in parts
                if len(p) > 2
                and not re.match(r"^[\d:]+$", p)
                and p not in skip
                and not any(c.isdigit() for c in p)
            ]
            all_parts = [p.lower() for p in parts]
            participants = []
            if op_first and op_first in all_parts:
                participants.append(operator)
            for name in names:
                if name not in participants:
                    participants.append(name)
            if participants:
                metadata["participants"] = participants[:5]

    # Date fallback: infer from YYYY-MM parent folder
    if not metadata.get("date"):
        folder_name = txt_path.parent.name
        m = re.match(r"^(\d{4}-\d{2})$", folder_name)
        if m:
            metadata["date"] = m.group(1) + "-01"
        else:
            metadata["date"] = datetime.now().strftime("%Y-%m-%d")

    return metadata


# ── Email resolution via Microsoft Graph ──────────────────────────────────

EMAIL_CACHE_FILE = Path(profile_lib.PM_OS_DIR) / "datasets" / "people" / "email_cache.json"


def _load_email_cache() -> dict:
    """Load the name→email cache from disk."""
    if EMAIL_CACHE_FILE.exists():
        try:
            with open(EMAIL_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_email_cache(cache: dict) -> None:
    """Persist the name→email cache to disk."""
    EMAIL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EMAIL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def _mgc_lookup_email(display_name: str) -> Optional[str]:
    """Look up a user's email by display name via mgc CLI (Microsoft Graph).

    Returns the email address string, or None if not found / mgc unavailable.
    """
    if not shutil.which("mgc"):
        return None

    # Escape single quotes in names for OData filter
    safe_name = display_name.replace("'", "''")
    try:
        result = subprocess.run(
            [
                "mgc", "users", "list",
                "--filter", f"displayName eq '{safe_name}'",
                "--select", "displayName,mail,userPrincipalName",
                "--top", "1",
            ],
            capture_output=True,
            **platform_lib.text_kwargs(),
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
        users = data.get("value", [])
        if users:
            return users[0].get("mail") or users[0].get("userPrincipalName")
    except (json.JSONDecodeError, KeyError, IndexError):
        pass

    return None


def resolve_participant_emails(participants: list[str]) -> dict:
    """Resolve a list of participant names to email addresses.

    Uses a file-based cache to avoid repeated Graph API calls.
    Returns a dict mapping name → email for successfully resolved names.
    Skips names that can't be resolved (they won't appear in the dict).
    """
    if not participants:
        return {}

    cache = _load_email_cache()
    result = {}
    cache_dirty = False

    for name in participants:
        # Check cache first (includes negative cache as None)
        if name in cache:
            if cache[name] is not None:
                result[name] = cache[name]
            continue

        # Try Graph API lookup
        email = _mgc_lookup_email(name)
        if email:
            result[name] = email
            cache[name] = email
            cache_dirty = True
            log.info("    Resolved email: %s → %s", name, email)
        else:
            # Negative cache so we don't retry failed lookups every sync
            cache[name] = None
            cache_dirty = True
            log.debug("    Could not resolve email for: %s", name)

    if cache_dirty:
        _save_email_cache(cache)

    return result


# ── Front matter ───────────────────────────────────────────────────────────────

def build_front_matter(metadata: dict, domain: str) -> str:
    """Assemble YAML front matter block. Omits keys with None/empty values."""
    lines = ["---"]

    if metadata.get("title"):
        title = metadata["title"].replace('"', '\\"')
        lines.append(f'title: "{title}"')

    if metadata.get("date"):
        lines.append(f'date: "{metadata["date"]}"')

    if metadata.get("duration_minutes") is not None:
        lines.append(f"duration_minutes: {metadata['duration_minutes']}")

    lines.append(f'domain: "{domain}"')

    participants = metadata.get("participants")
    if participants:
        lines.append("participants:")
        for p in participants:
            p_escaped = p.replace('"', '\\"')
            lines.append(f'  - "{p_escaped}"')

    participant_emails = metadata.get("participant_emails")
    if participant_emails:
        lines.append("participant_emails:")
        for name, email in sorted(participant_emails.items()):
            n_escaped = name.replace('"', '\\"')
            e_escaped = email.replace('"', '\\"')
            lines.append(f'  "{n_escaped}": "{e_escaped}"')

    # Provider-neutral id; legacy metadata may only carry otter_id.
    tid = metadata.get("transcript_id") or metadata.get("otter_id")
    if tid:
        lines.append(f'transcript_id: "{tid}"')
        provider = metadata.get("transcript_provider")
        if provider:
            lines.append(f'transcript_provider: "{provider}"')
        if metadata.get("otter_id") or provider == "otter":
            lines.append(f'otter_id: "{metadata.get("otter_id") or tid}"')

    lines.append("---")
    return "\n".join(lines)


def prepend_front_matter(txt_path: Path, front_matter: str) -> None:
    """Prepend YAML front matter to file. Skips if already has front matter."""
    text = txt_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        log.debug("  %s already has front matter, skipping", txt_path.name)
        return
    txt_path.write_text(front_matter + "\n" + text, encoding="utf-8")


# ── File movement ──────────────────────────────────────────────────────────────

def classify_and_move(txt_path: Path, domain: str, date_str: str) -> Path:
    """
    Move txt_path to MEETINGS_DIR/domain/YYYY-MM/filename.
    Creates directory if needed. Returns new path.
    """
    ym = "-".join(date_str.split("-")[:2])
    dest_dir = MEETINGS_DIR / domain / ym
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / txt_path.name
    os.replace(txt_path, dest)
    return dest


# ── Full pipeline ──────────────────────────────────────────────────────────────

def process_file(
    txt_path: Path,
    speech_id: Optional[str] = None,
    downloaded_state: Optional[dict] = None,
) -> dict:
    """
    Full pipeline for one file:
    extract_metadata → classify_domain → build_front_matter →
    prepend_front_matter → classify_and_move → return result dict.
    """
    log.info("Processing: %s", txt_path.name)

    metadata = extract_metadata(txt_path, speech_id=speech_id, downloaded_state=downloaded_state)
    title = metadata.get("title") or txt_path.stem

    # Resolve participant names → emails via Microsoft Graph
    participants = metadata.get("participants", [])
    if participants:
        emails = resolve_participant_emails(participants)
        if emails:
            metadata["participant_emails"] = emails

    # Read content for LLM preview (before prepending front matter)
    text = txt_path.read_text(encoding="utf-8")
    content_preview = "\n".join(text.splitlines()[:30])

    domain = classify_domain(title, content_preview, filename_hint=txt_path.stem)
    front_matter = build_front_matter(metadata, domain)
    prepend_front_matter(txt_path, front_matter)

    date_str = metadata.get("date") or datetime.now().strftime("%Y-%m-%d")
    final_path = classify_and_move(txt_path, domain, date_str)

    return {"domain": domain, "final_path": final_path}


# ── Backfill CLI ───────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _speech_id_from_filename(filename: str, state: dict) -> Optional[str]:
    """Match a filename prefix to a speech_id in downloaded state."""
    for speech_id in state:
        if filename.startswith(speech_id):
            return speech_id
    return None


def backfill() -> None:
    """
    Walk all *.txt files in YYYY-MM subdirectories under MEETINGS_DIR.
    Skip files already in domain subdirectories or that already have YAML front matter.
    """
    state = _load_state()
    results = []

    for txt_path in sorted(MEETINGS_DIR.rglob("*.txt")):
        rel = txt_path.relative_to(MEETINGS_DIR)
        parts = rel.parts

        # Skip files directly in MEETINGS_DIR (no parent folder)
        if len(parts) < 2:
            continue

        # Skip files already in domain subdirectories (depth > 2 parts means domain/YYYY-MM/file)
        if len(parts) > 2:
            log.debug("Skipping (already in domain subdir): %s", rel)
            continue

        # len(parts) == 2: check parent is a YYYY-MM folder
        parent = parts[0]
        if not re.match(r"^\d{4}-\d{2}$", parent):
            log.debug("Skipping (not in YYYY-MM folder): %s", rel)
            continue

        # Skip if already has front matter
        first_line = txt_path.read_text(encoding="utf-8").split("\n", 1)[0]
        if first_line.strip() == "---":
            log.info("Skipping (already classified): %s", txt_path.name)
            continue

        speech_id = _speech_id_from_filename(txt_path.name, state)
        log.info("  Found: %s (speech_id=%s)", txt_path.name, speech_id)

        try:
            result = process_file(txt_path, speech_id=speech_id, downloaded_state=state)
            domain = result["domain"]
            final_path = result["final_path"]
            results.append((domain, txt_path.name, final_path))

            # Update downloaded state for Otter files
            if speech_id and speech_id in state:
                state[speech_id]["domain"] = domain
                state[speech_id]["final_path"] = str(final_path)

            log.info("  [%s] → %s", domain, final_path)

        except Exception as exc:
            log.error("  Failed: %s — %s", txt_path.name, exc)
            import traceback
            traceback.print_exc()

    _save_state(state)

    print("\n── Backfill Summary ──────────────────────────────────────────────────")
    for domain, filename, final_path in results:
        print(f"  [{domain}]")
        print(f"    {filename}")
        print(f"    → {final_path}")
    print(f"\n  Total processed: {len(results)} file(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify and organize Otter transcripts")
    parser.add_argument("--backfill", action="store_true", help="Process all unclassified transcripts")
    parser.add_argument("files", nargs="*", help="Specific TXT files to process")
    args = parser.parse_args()

    if args.backfill:
        backfill()
    elif args.files:
        state = _load_state()
        for f in args.files:
            txt_path = Path(f).expanduser().resolve()
            speech_id = _speech_id_from_filename(txt_path.name, state)
            result = process_file(txt_path, speech_id=speech_id, downloaded_state=state)
            print(f"[{result['domain']}] → {result['final_path']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
