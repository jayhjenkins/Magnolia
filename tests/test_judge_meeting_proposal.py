"""Judge evaluates meeting PROPOSALS before the human reviews, not just booked meetings.

The chief-of-staff judge must intercept a schedule-meeting task as soon as the
agent produces a proposal (attendees, title, slots) — not wait until the human
selects a slot and a calendar event is created.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import judge


# ── detect_kind ──────────────────────────────────────────────────────────────

def test_detect_kind_meeting_proposal_with_attendees():
    fm = {"task_type": "schedule-meeting", "meeting_attendees": ["a@x.com"]}
    assert judge.detect_kind(fm) == "meeting"


def test_detect_kind_meeting_proposal_with_title_only():
    fm = {"task_type": "schedule-meeting", "meeting_title": "Sync"}
    assert judge.detect_kind(fm) == "meeting"


def test_detect_kind_meeting_booked():
    fm = {"task_type": "schedule-meeting", "meeting_selected_slot": "2026-08-01T15:00Z"}
    assert judge.detect_kind(fm) == "meeting"


def test_detect_kind_meeting_empty_returns_none():
    fm = {"task_type": "schedule-meeting"}
    assert judge.detect_kind(fm) is None


# ── gather_evidence includes proposed slots ──────────────────────────────────

BODY_WITH_SLOTS = """\
## Description
Align on roadmap.

## Suggested Times

<!-- SLOT:1|2026-07-30T15:00:00Z|2026-07-30T15:45:00Z -->
**Option 1:** Thursday, July 30 at 11:00 AM ET _(all free)_

<!-- SLOT:2|2026-07-31T17:45:00Z|2026-07-31T18:30:00Z -->
**Option 2:** Friday, July 31 at 1:45 PM ET _(all free)_
"""

FM_PROPOSAL = {
    "task_type": "schedule-meeting",
    "meeting_title": "Roadmap Sync",
    "meeting_attendees": ["jay@x.com", "zach@x.com"],
    "meeting_duration": 45,
    "meeting_recurring": False,
    "meeting_description": "Align on roadmap priorities.",
    "meeting_selected_slot": "",
}


def test_gather_evidence_proposal_includes_slots():
    ev, note = judge.gather_evidence("meeting", FM_PROPOSAL, BODY_WITH_SLOTS, "T-1")
    assert ev is not None
    assert "Option 1" in ev
    assert "Option 2" in ev
    assert note == "meeting proposal"


def test_gather_evidence_proposal_includes_attendees():
    ev, _ = judge.gather_evidence("meeting", FM_PROPOSAL, BODY_WITH_SLOTS, "T-1")
    assert "jay@x.com" in ev
    assert "zach@x.com" in ev


def test_gather_evidence_booked_uses_chosen_slot():
    fm = {**FM_PROPOSAL, "meeting_selected_slot": "2026-07-30T15:00:00Z"}
    ev, note = judge.gather_evidence("meeting", fm, BODY_WITH_SLOTS, "T-1")
    assert "2026-07-30T15:00:00Z" in ev
    assert note == "booked meeting"


# ── dimensions ───────────────────────────────────────────────────────────────

def test_meeting_dimensions_registered():
    dims = judge.DIMENSIONS_BY_KIND["meeting"]
    assert "necessity" in dims
    assert "invitees" in dims
    assert "details" in dims
    assert "timing" in dims


def test_parse_verdict_meeting_with_dimensions():
    raw = '{"score": 7, "dimensions": {"necessity": 9, "invitees": 8, "details": 6, "timing": 7}, "why": "solid"}'
    v = judge.parse_verdict(raw, "meeting")
    assert v["score"] == 7
    assert v["dimensions"]["necessity"] == 9
    assert v["dimensions"]["timing"] == 7


# ── _extract_proposed_slots ──────────────────────────────────────────────────

def test_extract_proposed_slots():
    slots = judge._extract_proposed_slots(BODY_WITH_SLOTS)
    assert "Option 1" in slots
    assert "Option 2" in slots


def test_extract_proposed_slots_empty():
    assert judge._extract_proposed_slots("no slots here") == ""
