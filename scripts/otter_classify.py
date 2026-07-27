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

VALID_DOMAINS = {
    "recruiting",
    "product/payments",
    "product/home",
    "product/platform",
    "leadership",
    "strategy",
    "customer",
    "general",
}

_DOMAIN_TAXONOMY = """\
- recruiting           (PM candidate interviews, hiring discussions)
- product/payments     (payments product meetings, Pay Standup, Payments L10)
- product/home         (home product feature work, home team meetings)
- product/platform     (platform, API, AI, technical infrastructure)
- leadership           (1:1s with anyone, exec intros, cross-functional syncs, team standups)
- strategy             (roadmap, quarterly planning, vendor strategy, partner intros)
- customer             (customer calls, demos, prospect calls, CS reviews)
- general              (anything else)"""


def _build_classify_prompt(title: str, content_preview: str) -> str:
    name = profile_lib.display_name()
    company = profile_lib.company()
    persona = profile_lib.persona()
    role = "product manager" if persona == "pm" else persona
    identity = f"a {role} at {company}" if company else f"a {role}"
    return (
        f"You are classifying meeting transcripts for {identity}. "
        f"Respond with ONLY the domain path, nothing else.\n\n"
        f"Domains:\n{_DOMAIN_TAXONOMY}\n\n"
        f"Title: {title}\n"
        f"Content preview: {content_preview[:600]}"
    )


def _keyword_classify(title: str, filename_hint: str = "") -> str:
    """Keyword-based fallback classifier. filename_hint supplements the title."""
    t = (title + " " + filename_hint).lower()
    if any(w in t for w in ("interview", "hiring", "candidate")):
        return "recruiting"
    if any(w in t for w in ("l10", "standup", "stand-up")):
        if any(w in t for w in ("pay", "payment", "payments")):
            return "product/payments"
        return "leadership"
    if "1:1" in t or "1-1" in t or "one on one" in t:
        return "leadership"
    if any(w in t for w in ("payments", "payment", "pay standup", "pay release")):
        return "product/payments"
    if "home" in t and "product" not in t:
        return "product/home"
    if any(w in t for w in ("platform", "apollo", "api", "infrastructure")):
        return "product/platform"
    if any(w in t for w in ("customer", "demo", "prospect", "cs review")):
        return "customer"
    if any(w in t for w in ("roadmap", "strategy", "quarterly", "vendor", "partner", "intro to")):
        return "strategy"
    return "general"


def classify_domain(title: str, content_preview: str, filename_hint: str = "") -> str:
    """Classify a transcript into one of the valid domain paths via Claude.
    Falls back to keyword rules on any failure."""
    prompt = _build_classify_prompt(title, content_preview)
    try:
        claude = platform_lib.resolve_claude()
        result = subprocess.run(
            [claude, "-p", prompt, "--max-turns", "1"],
            env=platform_lib.headless_claude_env(),
            capture_output=True, text=True, timeout=30,
            cwd=str(profile_lib.PM_OS_DIR),
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip().lower()
            raw = re.sub(r'["\'\n]', "", raw).strip().rstrip(".")
            if raw in VALID_DOMAINS:
                log.info("    Claude classified -> %s", raw)
                return raw
            log.warning("    Claude returned invalid domain %r, falling back to keywords", raw)
        else:
            log.warning("    Claude classify failed (rc=%d), falling back to keywords", result.returncode)
    except Exception as exc:
        log.warning("    Claude classify error: %s, falling back to keywords", exc)

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
        # ── Otter file ────────────────────────────────────────────────────────
        metadata["otter_id"] = speech_id

        # Title: prefer downloaded_state, fall back to # header
        if downloaded_state and speech_id in downloaded_state:
            metadata["title"] = downloaded_state[speech_id].get("title", "").strip()
        if not metadata.get("title"):
            for line in lines[:5]:
                if line.startswith("# "):
                    metadata["title"] = line[2:].strip()
                    break

        # Date from "Date: YYYY-MM-DD HH:MM" line
        for line in lines[:5]:
            m = re.match(r"Date:\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?", line)
            if m:
                metadata["date"] = m.group(1)
                if m.group(2):
                    metadata["time"] = m.group(2).replace(":", "-")
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
        if speakers:
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
            # Keep parts that look like names (>2 chars, not digits-only, not connectors)
            skip = {"and", "the", "with", "for", "jay"}
            names = [
                p.title()
                for p in parts
                if len(p) > 2
                and not re.match(r"^[\d:]+$", p)
                and p not in skip
                and not any(c.isdigit() for c in p)
            ]
            # Always include Jay Jenkins in manual files
            all_parts = [p.lower() for p in parts]
            participants = []
            if "jay" in all_parts:
                participants.append("Jay Jenkins")
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
            with open(EMAIL_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_email_cache(cache: dict) -> None:
    """Persist the name→email cache to disk."""
    EMAIL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EMAIL_CACHE_FILE, "w") as f:
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
            text=True,
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

    if metadata.get("otter_id"):
        lines.append(f'otter_id: "{metadata["otter_id"]}"')

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
    txt_path.rename(dest)
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
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
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
