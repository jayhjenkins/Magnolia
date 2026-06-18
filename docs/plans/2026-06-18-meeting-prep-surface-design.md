# Meeting Prep Surface ("On Deck") — design brief

> 2026-06-18. A design brief for iteration, not a final spec. It defines a **calendar-driven
> meeting-prep surface**: recurring meetings become countdown-driven cadence programs that prepare
> the operator proactively, surfaced in a meeting-centric view built for just-in-time recall.
>
> Companion: `2026-06-12-cadence-design-brief.md` (this rides on the Cadence subsystem — meeting prep
> is a new program family on that substrate). Origin: design conversation seeded by exec use cases
> Tom Arnett demoed (1:1 coaching prep, product-review prep, L10 data-gather) on 2026-06-11.

---

## Mission

The task board is the **verbs of the operator's life** (status-organized). Cadence is the **state of
their programs** (cycle-organized). This surface adds a third lens: **time- and meeting-organized**.

Its whole reason to exist: *prep buried in a task queue is prep that gets lost.* Two minutes before a
meeting, the operator needs one place that answers, behaviorally and at a glance — **am I prepped? what's
on the list? what homework is undone?** — without thinking. The producer machinery (cadence + cron)
fills it; this surface is where the operator *consumes* it.

This is the most differentiated executive capability in the system: a calm, habitual place that mirrors
what a prepared operator already does mentally before each recurring meeting.

---

## Core user stories

1. **Walking into a meeting,** I want one glance — am I prepped, what's the list, what homework is
   undone — so prep never gets lost in my queue.
2. **For recurring meetings,** I want the system to recognize each one (1:1, product review, L10) and
   run the right prep playbook on the right runway, proactively.
3. **For a *new* recurring meeting,** I want a one-time interview — what I produce, my process, my lead
   time — so it preps that meeting my way going forward.
4. **For a big meeting,** I want multi-touch prep on a runway (e.g. T-2wk, T-1wk, T-1day), not a single
   reminder.
5. **For a data-driven meeting (L10),** I want it to gather from a source I name, process it, and emit a
   status, so I walk in with numbers, not homework.
6. **Across prep sessions for one meeting,** I want each wake-up to know what prior sessions already did
   and produced, so the work builds instead of repeating.
7. **For a one-off meeting,** I want to say "prep this" and get the same treatment without a recurrence.

---

## Design

### Substrate — reuse what exists

- **A recurring meeting = a `meeting-prep` cadence program instance.** One program type,
  `state_model: countdown`. The *playbook* — `{ archetype, skill, touchpoints[offsets], sources }` — is
  instance config, so new meeting kinds need **no new code**. A **`meeting-types` registry** seeds
  default playbooks (1:1, product-review, L10, generic); a **`learn-this-meeting` interview skill**
  (onboarding-style) customizes per meeting and writes the playbook.
- **Touchpoints = countdown phases** at negative offsets from the next occurrence
  (`1:1 → [-1d]`, `product review → [-14d, -7d, -1d]`, `L10 → weekly [-1d]`). Each fires a worker that
  runs the meeting's skill.
- **Homework = normal cards in the Now view**, each carrying a `meeting_id` association. Tasks remain
  the single source of truth; the meeting is metadata + a filter, never a container that owns storage.
  **Wake-ups create cards as the meeting nears** — a 3-weeks-out meeting has zero prep cards until its
  first touchpoint fires.
- **Skills are shared** between recurring (program-driven) and one-off (a plain "prep this meeting"
  task — story 7). Only the triggering differs.
- **Continuity is task creation, not a new feature.** Action items / unfinished homework rolling into
  the next occurrence is handled by the existing task system with a link back. (Explicitly *not* a
  bespoke roll-forward mechanism.)

### Context strategy across touchpoints (story 6)

A single meeting may be prepped across 2–3 separate worker sessions. Each later wake-up must build on
prior work without re-deriving it. The rule: **point by reference, carry state inline, never replay full
prior sessions** (full replay carries tool-call noise and compounds quadratically across touchpoints).

Layered handoff:

| Layer | What | How passed | Cost |
|---|---|---|---|
| **Artifacts** | The deck / metrics pull / topic list each session produced | **By reference** (file paths); agent opens on demand | ~free |
| **Prep ledger** | Per-touchpoint structured entry: *produced · decided · deferred/open · blockers* | **Inline** — read all prior entries | small, linear |
| **This touchpoint** | What *this* wake-up is for (T-7d = refine, T-1d = finalize) | Playbook instruction | tiny |
| **Full session logs** | Raw transcripts | **Retained on disk, pointed to, not loaded** unless an agent genuinely needs "how did we get here" | zero unless used |

The next session reads the **ledger** (the decisions + open threads that a bare artifact loses) plus the
**list of prior associated cards with their `agent_output` paths**, then pulls a specific artifact inline
*only* when it is directly revising it.

Cheap to build because it maps to existing fields:
- **Prep ledger = the program's `state`** — the producing worker appends a tight structured handoff block
  at completion (the worker knows its own decisions better than any post-hoc summarizer).
- **Artifact pointers = the cards' `agent_output` paths.**

So no new storage — a discipline about what the worker writes and what the next worker reads.

### Genuinely new infra (minimal)

1. **Countdown-firing logic** in `scripts/cadence/scheduler.py` — fire phases at offsets *before* a
   computed next-occurrence date (today the scheduler ages *forward* from `phase_entered`). **This is the
   one real new mechanic — prototype it standalone first.**
2. **Recurrence → next-occurrence helper** + a **once-daily calendar-sync task** that reads recurring
   meetings via the **existing M365 MCP** and caches next-occurrence dates onto the program files. The
   pure-Python cadence daemon then reads cached dates.
   - **No new calendar adapter.** The system already reads the calendar (`workflow-schedule-meeting`,
     M365 MCP). The only gaps are (a) recurrence/next-occurrence semantics and (b) a headless path for
     the daemon — and (b) is solved by caching dates via a daily agent task rather than an adapter,
     because MCP tools only run inside an agent turn. An adapter would only be justified for synchronous
     per-tick reads, which prep runways measured in days do not need.

### Placement of data

Playbooks, direct-report roster, "what prepped means," and prep ledgers → `profile/` + program state
(personal, gitignored). Engine stays agnostic.

Boundary with the Schedules tab: fixed-interval jobs (coaching digest, email alerts, signals refresh)
stay in cron. **Cadence counts down to an event; cron is wall-clock.**

---

## UI suggestion (early)

A **meeting-centric surface, separate from the task board** (board = by status; this = by time).

**1. "On Deck" — the daily glance.** Today / this week as a timeline; each meeting wears a readiness chip
(`prepped / prepping / not started / no prep`). Imminent meeting elevated ~5 min out. Readiness = % of
associated cards done.

```
TODAY · Thu Jun 18
──────────────────────────────────────────────
 9:00  ✓ Standup           no prep needed
11:00  ● Product Review    prepping · T-7d touchpoint due
 2:00  ● 1:1 Shelby        prepped · 2 of 3 homework   ◀ NEXT · 5 min
 4:00  ○ Vendor call       not started
──────────────────────────────────────────────
THIS WEEK
 Fri   ● Resident Exp L10  data gather running
```

**2. Prep sheet — the cockpit.** Tap a meeting → a *filtered view* over its associated cards. Owns no
storage.

```
┌─ 1:1 · Shelby Lewis ──────────────── in 5 min · 2:00 PM ─┐
│ ● Prepped — 2 of 3 done                                  │
│                                                           │
│ TALKING POINTS (this week)                                │
│  • V3 rollout — where did activation land?                │
│  • Her PTO next week — coverage plan                      │
│  • + add a topic                                          │
│                                                           │
│ OPEN FROM LAST TIME                                        │
│  ✓ You: shared the scorecard deck                         │
│  ◷ Her: onboarding owner list  (still waiting)            │
│                                                           │
│ YOUR HOMEWORK  (lives in Now, shown here)                 │
│  ✓ Reviewed her project update                            │
│  ✓ Pulled her ticket status                               │
│  ☐ Decide on the contractor ask   ◀ not done             │
│                                                           │
│ CONTEXT (auto-gathered)                                    │
│  • 3 meetings w/ Shelby this week → summary                │
│  • Coaching nudge: get a date on the contractor ask        │
│                                                           │
│ [ Mark prepped ]   [ Add note ]   [ Open full prep ]       │
└─────────────────────────────────────────────────────────────┘
```

Homework is *actioned* in Now (tagged "↳ for [meeting]") and *viewed* here.

---

## Open decisions

1. **Surface placement** — standalone "On Deck" top-level surface vs. a persistent next-meeting rail
   that follows the operator vs. a morning daily-brief digest. Drives the UI build. (Mock 2–3 side by
   side before committing.)
2. **Countdown prototype first?** — prove next-occurrence + offset-firing as a standalone before building
   the rest, since everything rests on it.
3. **Where the prep ledger handoff block is authored** — worker self-emits at completion (recommended)
   vs. a local-model compression pass over the session.

## Suggested sequencing

1. Prototype **countdown-firing + next-occurrence** (the one new mechanic).
2. Daily **calendar-sync task** caching occurrences onto programs (existing M365 MCP).
3. `meeting-prep` **program type + `meeting-types` playbook registry + `learn-this-meeting`** interview.
4. First archetype end-to-end (**1:1**), incl. the prep-ledger handoff discipline.
5. **On Deck + prep sheet** read-views once programs produce associated cards.
6. Add **product-review** (multi-touch runway) and **L10** (data-gather → emit) archetypes.
