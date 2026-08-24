---
name: workflow-schedule-meeting
description: Find available meeting times via Microsoft 365 MCP and write structured time slot options into a collab task for human selection
---

# Schedule Meeting Workflow

## Purpose

Automate the scheduling legwork: find mutually available times for meeting attendees using the Microsoft 365 MCP, format them as selectable options in the task, and let the operator pick one in the web UI.

## When to Use

- Task has `task_type: schedule-meeting` in its frontmatter
- Task is in the `collab` queue with status `open`

## Core Rule (MANDATORY)

You ALWAYS deliver 3–4 selectable slots and end with `agent:complete`. The web UI renders a slot picker — the operator's interaction is clicking, not answering questions.

Never use `agent:ask` to:
- Ask which slot is best
- Ask permission to widen the search
- Ask whether to propose imperfect options (soft calendar conflicts are fine — note them inline)
- Ask whether to propose despite missing attendee availability (use the operator-only fallback in Step 4)

`agent:ask` is reserved for hard blockers only: unresolvable attendee email, mgc auth failure, or genuinely zero operator availability across 10 business days.

## Workflow

### 1. Read the Task

```bash
./scripts/task.sh show {TASK_ID}
```

Extract from frontmatter:
- `meeting_attendees` — list of email addresses
- `meeting_duration` — minutes (default 30)
- `meeting_title` — calendar event title
- `meeting_description` — event body text
- `source_meeting` — transcript that spawned this task (if any)

**Validate `meeting_description` for calendar appropriateness.** The `meeting_description` field becomes the calendar invite body that all attendees see. If it is empty, reads like internal task notes (e.g., "the operator mentioned wanting to..." or "At end of catch-up, Zach suggested..."), or describes how the task was created rather than what the meeting is *for*, rewrite it:

1. Derive the meeting's purpose from the task title, task description, and source meeting transcript (if available)
2. Write 1-2 concise sentences describing what the meeting is about from the attendees' perspective
3. Update the `meeting_description` frontmatter field with the rewritten version before proceeding

Examples of rewrites:
| Original | Rewritten |
|---|---|
| *(empty)* | "Biweekly sync to review Home product roadmap progress and discuss blockers" |
| "During standup the operator said they'd set up time with Brandon to align on HOAi rollout" | "Align on HOAi rollout plan, timeline, and next steps" |
| "Zach suggested standardizing a recurring touch base to stay aligned on Pay" | "Recurring sync to stay aligned on Pay priorities and surface blockers early" |

### 1b. Detect Recurring Series

Check the task title, description, and source meeting transcript for signals that this is a **recurring** meeting rather than a one-off:
- Keywords: "recurring", "weekly", "biweekly", "bi-weekly", "monthly", "standing", "series"
- A specified count: "5 weeks", "for the next 8 sessions", "through end of quarter"
- Calendar cadence language: "every Monday", "each Tuesday afternoon"

If the meeting is recurring:
1. Note the **frequency** (weekly, biweekly, monthly) and **count** (number of occurrences, or an end date to compute it from). Honor the exact count the task specifies — if it says "5 weeks", propose a 5-occurrence series, not 4.
2. Find slots for the **first occurrence only** — propose 3–4 options for when the series should start.
3. In the `## Suggested Times` output (Step 5), add a recurrence marker above the slots:

   ```markdown
   <!-- RECURRENCE:weekly|5 -->
   **Recurring:** Weekly for 5 weeks
   ```

   The `<!-- RECURRENCE:frequency|count -->` comment tells the UI to create a recurring calendar event, not N independent invites. Supported frequencies: `weekly`, `biweekly`, `monthly`.

4. Each slot's display line should reflect the series start: e.g., "**Option 1:** Starting Tuesday, March 25 at 10:00 AM ET, recurring weekly for 5 weeks _(all attendees free for first session)_"

If the meeting is one-time (no recurrence signal found), skip this step entirely.

### 2. Gather Time Preferences (Optional)

If `source_meeting` exists, read the transcript and look for scheduling hints:
- "next week", "this Thursday", "before Friday"
- "30 minutes", "an hour"
- "morning", "afternoon"

Use these to narrow the search window. If no hints, default to **next 5 business days**.

If the task or transcript mentions alternative days ("Monday or Tuesday", "this week or next"), search ALL mentioned alternatives — do not limit to the first one. When the ask names two consecutive days, set `--start` and `--end` to span both.

### 3. Resolve Attendee Emails

If any attendee entry looks like a name (no `@`), attempt to resolve it in this order:

1. **Check the source transcript** — if `source_meeting` exists, read it and look for the `participant_emails:` frontmatter field. This maps participant names to corporate emails (resolved at ingest via Microsoft Graph). Match attendee names against this mapping.
2. **Check the email cache** — read `datasets/people/email_cache.json` which accumulates all resolved name→email mappings across transcripts.
3. **Search Outlook** — use MCP tool `outlook_email_search` to search your mailbox for messages from/to that person's name and extract their email from the results.
4. **If all fail** — use `agent:ask` to request the email from the operator:
   ```bash
   ./scripts/task.sh agent:ask {TASK_ID} "I need the email address for {name}. Who should I invite?"
   ```
   Then STOP. Do not continue.

**Organizer as attendee.** Always include the operator (jay.jenkins@vantaca.com) as an explicit attendee in the `--attendees` list so they appear on the invite — do not assume the calendar provider adds the organizer automatically.

**Optional attendees.** If the task mentions someone with hedging language ("possibly", "maybe", "if available"), note them in the `## Suggested Times` output as "Optional: {name} ({email})" beneath the slot list. Do not block on their availability when finding slots, but do not silently drop them — they should appear on the calendar invite as optional.

### 4. Find Available Times

Run the `find_meeting_times.py` script (uses `mgc` CLI to call Microsoft Graph findMeetingTimes):

```bash
python3 ./scripts/find_meeting_times.py \
  --attendees "email1@co.com,email2@co.com" \
  --duration 30 \
  --max-slots 4
```

The script defaults to the next 5 business days. To narrow by transcript hints:
```bash
python3 ./scripts/find_meeting_times.py \
  --attendees "email@co.com" \
  --duration 30 \
  --start "2026-03-25" \
  --end "2026-03-28" \
  --max-slots 4
```

If the result has zero slots (`"slots": []`) OR `empty_reason` indicates `AttendeesUnavailableOrUnknown` (Graph can't see an attendee's calendar — permissions, free/busy not shared, etc.):

1. **Expand once.** Add 5 more business days via `--start`/`--end` and retry. If that returns slots, use them.

2. **Operator-only fallback.** If still zero/unknown, propose 4 slots from the operator's preferred ad hoc windows (Tue/Thu afternoons, Mon 2:00–4:00 PM ET) that do not conflict with the operator's calendar. Run `find_meeting_times.py` with `--attendees "<the operator's email only>"` to confirm the operator is free, then format those as the suggested times. In the display line for each slot, append `(attendee calendar not visible — they'll RSVP)` instead of the normal `(all attendees free)` parenthetical.

3. **Hard block only.** Only call `agent:ask` if the operator themselves has zero availability across the next 10 business days — that is a real scheduling failure. Do NOT ask whether to widen, whether to propose anyway, or which option is best.

**Do NOT use MCP tools for availability lookup** — the headless dispatch environment does not have MCP access. Always use the `find_meeting_times.py` script.

### 5. Format Suggested Times

Write a `## Suggested Times` section into the task description. Each slot MUST include an HTML comment with machine-parseable data followed by a human-readable line.

**Slot quality rules:**
1. **No weekends or out-of-hours.** Every proposed slot must fall within Monday–Friday, 9:30 AM – 5:00 PM ET. Discard any Graph-returned slot outside this window.
2. **Prefer conflict-free for required attendees.** Rank slots where ALL required (non-operator) attendees are free above slots with attendee conflicts. If 3+ conflict-free slots exist, do not include conflicted ones. If fewer than 3 are conflict-free, fill to 4 total with conflicted slots ranked LAST and prefix the availability note with a warning (e.g., "Dennis has a conflict — Trisha free").
3. **Operator soft conflicts are fine.** The operator's own tentative/soft conflicts may be noted inline but do not deprioritize the slot.

**For each slot, cross-reference the ET time against the Operator's Calendar Structure Reference (below) and append a short contextual note** after the availability info. The note should help the operator evaluate soft tradeoffs at a glance. Keep each note to 1 short sentence max.

Context notes should cover whichever of these is most relevant to that slot:
- Whether it falls in a designated block (1:1 block, focus time, etc.)
- Back-to-back risk with adjacent meetings ("follows your Pay L10 — tight transition")
- Day character ("Tuesday is already your heaviest day")
- Policy fit for recurring meetings ("inside your Thursday 1:1 block" or "outside 1:1 block — fine for ad hoc")
- Clean openings ("open slot, no adjacency issues")

```markdown
## Suggested Times

<!-- SLOT:1|2026-03-25T14:00:00Z|2026-03-25T14:30:00Z -->
**Option 1:** Tuesday, March 25 at 10:00 AM - 10:30 AM ET _(all attendees free)_ — overlaps Home Standup window

<!-- SLOT:2|2026-03-26T18:00:00Z|2026-03-26T18:30:00Z -->
**Option 2:** Wednesday, March 26 at 2:00 PM - 2:30 PM ET _(all attendees free)_ — right before Home L10 at 2:30

<!-- SLOT:3|2026-03-27T15:00:00Z|2026-03-27T15:30:00Z -->
**Option 3:** Thursday, March 27 at 11:00 AM - 11:30 AM ET _(all attendees free)_ — open slot, no adjacency issues

<!-- SLOT:4|2026-03-27T18:00:00Z|2026-03-27T18:30:00Z -->
**Option 4:** Thursday, March 27 at 2:00 PM - 2:30 PM ET _(all attendees free)_ — inside your 1:1 block (good for recurring 1:1s)
```

**Critical format requirements:**
- `<!-- SLOT:N|startISO|endISO -->` — the web UI JavaScript parses these HTML comments to render the time picker. They must be on their own line, exactly this format.
- Start/end times in the comments are UTC (ISO 8601 with Z suffix)
- Display times are converted to ET (America/New_York) for readability
- Include day of week + date + time range in the display text
- Context note goes after the availability parenthetical, separated by " — " (em dash is OK here in UI display text, not in prose content)

### 6. Update the Task

Append the suggested times to the task description:

```bash
./scripts/task.sh update {TASK_ID} \
  --comment "Found {N} available time slots for: {attendee list}. Select a slot in the task board UI to create the calendar event."
```

Also update the description body to include the `## Suggested Times` section. Use `task_lib.update_task_description()` or write the updated body directly.

### 7. Complete Agent Work

```bash
./scripts/task.sh agent:complete {TASK_ID}
```

The task stays in the `collab` queue with `agent_status: complete` for the operator to review and select a time slot in the web UI.

## The Operator's Calendar Structure Reference

Use this reference when annotating suggested time slots in Step 5.

### Work Hours & Hard Constraints
- **Work hours:** 9:00 AM - 5:00 PM ET, Monday-Friday
- **Hard start:** 9:30 AM (school drop-off buffer before 9:30)
- **Hard end:** 5:00 PM
- **Lunch Hold:** Noon-1:00 PM daily
- **School pickup windows:** 11:45 AM-12:15 PM (early) or 12:45-1:15 PM (standard) — variable days, avoid 11:45 AM-1:15 PM when uncertain

### Daily Anchors (Every Day)
- 9:00-9:30 AM — School drop-off buffer (no meetings)
- 10:00-10:30 AM — Home Standup (tentative)
- 10:30-11:00 AM — Pay Standup (tentative)
- 2:00-2:25 PM — HOAi Voice Sync (optional daily standup)

### Focus Time Blocks (Hard-Protected)
- **Monday 11:00 AM-Noon** — Focus Time
- **Wednesday 9:30 AM-Noon** — Extended Focus Time (most protected block of the week)
- **Friday 9:30 AM-Noon** — Focus Time
- **Friday 2:00-5:00 PM** — Protected (no new meetings unless urgent)

### Day Characters
- **Monday** — Leadership & Strategy. Key meetings: Weekly Prep (9:30), RE Product Collab (1 PM), Product Leadership Connect (2:30), AI-DLC/CMP L10 (3 PM bi-weekly), Haoyu 1:1 (4 PM bi-weekly). 3rd Monday: exec-facing monthly reviews.
- **Tuesday** — Team Operations (heaviest day). Morning packed: Dave RE Q&A + Payments L10 (9 AM), Release Planning (10), Pay Triage (11 bi-weekly). Afternoons lighter but use sparingly.
- **Wednesday** — Deep Work & Home L10. Morning fully protected (focus). Afternoon: RE/Portal Refinement (1 PM), Home L10 (2:30-4), L10 Follow-Ups (4:30-5:30).
- **Thursday** — People & Product Rhythm. CS Standup (9:30), Product L10 (10 bi-weekly), Sprint Demo (11 bi-weekly). **1:1 Block: 1:00-3:00 PM** (designated home for all recurring 1:1s). Trisha 1:1 (4 PM bi-weekly).
- **Friday** — Light & Reset. Focus until noon. One recurring check-in (1:30 PM). Afternoon protected.

### 1:1 Scheduling Policy
- **Recurring 1:1s** must go in the **Thursday 1:00-3:00 PM block** (exceptions: Haoyu Mon 4 PM, Trisha Thu 4 PM — already established)
- **Ad hoc / one-off 1:1s** are flexible — prefer Tuesday or Thursday afternoons, or Monday mid-afternoon. Avoid Wednesday mornings and Friday afternoons.

### Preferred Scheduling Slots (for ad hoc meetings)
- **Best:** Tuesday afternoon, Thursday afternoon (outside 1:1 block), Monday 2:00-4:00 PM
- **Avoid:** Wednesday before noon, Friday after 2 PM, any Focus Time block

## Error Handling

| Error | Action |
|-------|--------|
| `mgc` not found | `agent:fail` with install instructions |
| mgc auth expired | `agent:fail` — "Run `mgc login --scopes 'Calendars.ReadWrite User.Read.All'`" |
| No attendee emails resolvable | `agent:ask` the operator for email addresses |
| Zero availability found | Expand range once, then fall through to operator-only fallback (Step 4). Do not ask. |
| `AttendeesUnavailableOrUnknown` | Same — propose against the operator's calendar, tag each slot "(attendee calendar not visible — they'll RSVP)". |
| All slots have soft conflicts | Propose them anyway with conflict notes inline. Do not ask "want to widen?". |

## Success Criteria

- Task updated with 2-4 selectable time slots
- HTML comments in exact `<!-- SLOT:N|start|end -->` format
- Display times in ET with day-of-week
- Agent status set to `complete`
- Task remains in collab queue for human selection
