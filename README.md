# Magnolia — How It Works

*A guide for someone meeting the system for the first time.*

> Companion to [`UX_VISION.md`](./UX_VISION.md) (why the experience feels the way it does) and [`ROADMAP.md`](./ROADMAP.md) (where the system goes). This document explains, from a user's perspective, **what Magnolia is and how it works.**
>
> **Setting it up for the first time?** Per-platform install guides: [`docs/INSTALL-macos.md`](./docs/INSTALL-macos.md) · [`docs/INSTALL-windows.md`](./docs/INSTALL-windows.md) ([chooser](./docs/INSTALL.md)) — prerequisites, where to put the repo, and the `onboard me` handoff.

---

## What Magnolia is

Magnolia is a **chief of staff that happens to be software.** It is not a task board you operate, a dashboard you check, or a bot you prompt. It is a standing system that does the legwork that piles up around you — researching, drafting, triaging your inbox, watching quality, taking small actions — and then walks into your office with only two kinds of things:

- *"Here's what I need you to decide or own."*
- *"Here's what I already handled. FYI."*

Everything in Magnolia serves that one relationship. A good chief of staff is measured by how little of your attention they waste and how much you trust what crosses your desk. That is the entire design goal.

Underneath, it is deliberately humble plumbing: markdown files, git, a little Python, and a headless Claude harness. Work is broken into **skills** (auto-discovered units of know-how) run by **workers** (scoped agents), tracked as **cards** on a board, and improved by a **judge** that learns your taste over time. No heavy framework. Nothing you can't read.

**Who it's for.** Magnolia is built for the product manager first — but PM is only the *first* user, not the only one. It's for anyone at Vantaca carrying a heavy meeting load whose real job keeps getting buried under the tasks, work, and follow-ups those meetings throw off. If your calendar generates more to-dos than you can keep up with, you're who this is for.

---

## Where the work comes from: your meetings

Most of your work doesn't start as a tidy task you typed into a list — it starts in a **meeting.** So that's where Magnolia starts too.

Magnolia connects to your **Otter or Granola** and pulls your meetings down automatically. It reads each transcript and parses it for **work, tasks, and follow-ups** — the "I'll send that over," the "someone should look into this," the decision that quietly implies three next steps — and turns each one into a tracked card on your board. You don't transcribe, and you don't translate notes into a to-do list by hand. The meeting *becomes* the work, on its own.

This is the front door of the whole system: **your meetings are where most of your tasks are born.** Everything downstream — the judge scoring, the trust ladder, the receipts — exists to handle that incoming stream. And over time, through the trust ladder and the card system, Magnolia learns how *you* handle each kind of follow-up and starts doing more of it for you — until the routine work that comes out of your meetings is handled before you'd have gotten to it.

---

## Why it was built

Most AI productivity tooling fails the same way: it generates a pile of output and hands you all of it, equally, with no sense of what's good or what matters. You end up managing the AI instead of the AI managing the work. The pile just moves from your inbox to a new app.

Magnolia exists to close three gaps that turn a clever demo into something you can actually live inside:

1. **Capture ≠ learning.** Thumbs-up/down on AI output is worthless if the next run doesn't get better. Magnolia closes the loop: your reactions become rubric changes and prompt improvements you approve.
2. **Trust was convention, not enforcement.** "The agent probably shouldn't send that email" is not a safety model. Magnolia makes the boundary between what an agent may finish alone and what needs you an *enforced* property of the system.
3. **The board made everything compete for your attention equally.** A finished receipt and an undone decision sat in the same column screaming the same volume. Magnolia organizes by *what each thing wants from you*, so the calm things stay quiet.

The throughline: **earn trust one task-type at a time, always reversibly, and never let the machine's output pile up against your attention.**

---

## Design principles

1. **Organize by demand on you, not by who executes.** The real question when you sit down is never "let me check the agent queue." It's *what needs me, what got handled, and can I trust it.* That's the primary axis.
2. **The system proposes; you dispose.** Nothing that touches the outside world happens without your say-so until it has explicitly earned that right. Every improvement, promotion, and risky action arrives as a one-tap proposal with a diff.
3. **Trust is earned, measured, and reversible.** No task-type is born autonomous. It climbs by demonstrating quality and agreement, and it falls automatically the moment quality drops.
4. **Accountability never transfers.** Trust can automate the *doing*. It can never automate the fact that *your name is on the PRD.* Artifacts you're accountable for are always yours to bless — no quality score retires that read.
5. **Reversibility over confirmation.** Where something can be safely undone, prefer doing it and showing a Keep/Undo receipt over asking permission up front. Where it can't, gate it.
6. **The engine stays impersonal; identity lives in your profile.** The shared engine never hardcodes who you are. Your name, your tools, your conventions live in a per-person profile, so a teammate can pull the same engine and make it theirs.
7. **Calm is a feature.** A quiet board is the system working, not the system idle.

---

## The Trust Ladder — the heart of the system

**This is the single most important concept, and the one most easily confused with card types. They are not the same thing.**

- **Card types** describe *what a piece of work is* — a task to be done, a receipt of something done, an artifact you own, an action that was taken. (More on these below.)
- **The Trust Ladder** describes *how much the judge inserts itself into producing that work.* It is a trust level that changes the **judge's role**, not the work's identity.

A single card type — say, "draft a PRD" — can sit at any rung of the ladder. The card type doesn't change as trust grows; **the judge's posture toward it does.**

The judge climbs the ladder **per task-type**, and the rung decides one thing: *does the judge interject itself into the process, and how?*

### 🌑 Shadow — the judge watches, and only watches

In shadow mode the judge **does not interject at all.** A worker produces its output and the work flows to you exactly as it would without a judge. The judge silently scores everything in the background — a number, a per-dimension breakdown, and a one-line "why" — and writes it onto the card. That's it.

Shadow mode is the judge building a track record while staying out of the way. You read the scores; you react; agreement quietly accrues. The judge is a new hire shadowing you on the job, taking notes, touching nothing.

### 👁️ Supervised — the judge reviews, and can kick it back

Once the judge has proven it agrees with you on a task-type, it graduates to supervised. Now it **interjects as a reviewer.** Before the output lands, the judge reads it against its rubric. If it's below bar, it **bounces the work back for revision** — an inline `generate → critique → revise` loop inside the same run, bounded to a pass or two. Only output that clears the bar reaches you.

The judge is now a manager doing a real review and able to say "not yet, try again" — but it still doesn't *ship* anything to the outside world on its own. Any external action still routes to you for approval.

### 🚀 Autonomous — the judge becomes the chief of staff

At the top rung, the judge stops being a gate and becomes an **owner.** It doesn't just check the work — it **does the work to the level it needs to be, and finishes it on its own.** The output ships as a **receipt**: past tense, "here's what I did, here's the score, confirm or undo." You weren't in the loop; you're in the audit trail.

This is the chief of staff made literal. For the task-types that have earned it, you stop seeing the work happen and start seeing only that it happened — with one reversible affordance in case the machine was wrong.

### The ladder in one line

| Rung | Judge's role | Does it interject? | Where the work lands |
|---|---|---|---|
| **Shadow** | Silent observer | No — scores only | Your review, unchanged |
| **Supervised** | Reviewer / gate | Yes — kicks it back if weak | Your review, pre-vetted |
| **Autonomous** | Chief of staff / owner | Yes — finishes it itself | A receipt you confirm or undo |

Climbing is **earned** (high judge-vs-you agreement *and* a high rate of you accepting its proposals unmodified) and **reversible** (scores drop → automatic demotion back down a rung). Trust is a budget per task-type: up with agreement, down with surprises.

**One hard floor, independent of the ladder:** anything that writes to the outside world — sending an email, publishing a ticket — is **Tier-2** and always asks you once before its first external action, *no matter how autonomous the task-type is*, until that specific action is explicitly graduated. Blast radius trumps confidence. (This is the safety axis; don't confuse it with the trust ladder either — the ladder is about the judge's involvement, Tier is about how far a mistake could reach.)

---

## Card types — what the work *is*

Cards are the unit of work on the board. They classify along axes that are *orthogonal* to the trust ladder:

**By tense — Task vs Receipt:**

- A **Task** is *future tense*: "Do X." It demands you. It may legitimately pile up, because each one wants something.
- A **Receipt** is *past tense*: "Did X. Here's what changed. Here's the score. Confirm / Undo." Its default state is *fine.* It wants a glance, then auto-archives unless you flag it.

As a task-type climbs the trust ladder, its card **changes tense** — from a Task you review to a Receipt you confirm after. The promotion ladder is literally visible as *where a card lands on your screen.*

**By demand — who the work routes to:**

- **Human** — a decision, a meeting, an approval. Needs your judgment.
- **Agent** — autonomous research, drafting, analysis. The machine owns it.
- **Collab** — an agent prepped something that touches an external system; it needs your approval before it acts. (This is where every Tier-2 action waits.)
- **Waiting** — owed by someone else; the ball is in their court.

**By accountability — the line automation must not cross:**

- **Artifacts you sign** (PRDs, plans, memos, research) are **always a review,** forever, regardless of score. The judge sets *how deeply* you read (skim a 9, scrutinize a 5) — never *whether.*
- **Actions already taken** (a sent message, a published ticket) can become receipts once trusted, because they're reversible and you're not personally on the hook for the prose.

The judge uses all of this to route your *attention*: a high-scored, low-risk action becomes a receipt you never touched; anything low-scored gets pulled into your lane with "judge flagged this." Review stays the heavyweight daily lane; the receipts stream is a skim.

---

## The out-of-the-box eval loops

Magnolia evaluates its own work using boring, durable infrastructure — **native files + git + the board**, no special platform required:

- **Prompts** live in files; **git is their version history.**
- **Traces** are the Claude Code session logs.
- **Scores** ride in each card's frontmatter.
- **The UI** is the board's read-only **Quality tab.**

Two loops run by default:

1. **Scoring (real-time).** Every time a task completes, the judge scores it immediately — number, per-dimension breakdown, one-line why — onto the card. You see quality the moment work lands.
2. **Calibration (weekly, batch).** A recurring job compares the judge's scores against your reactions on the same cards and computes an **agreement %** per task-type. Divergence is the signal that either the rubric is wrong or output genuinely varies.

The Quality tab turns this into a **promotion board**: a row per task-type showing score trend, judge-vs-you agreement, current rung (Shadow / Supervised / Autonomous), and trajectory toward graduating or being demoted. It answers "can I trust this more or less than last week" at a glance — and it's *read-only,* because the actual calibration happens as a free byproduct of the reviews you already owe your artifacts. You never sit down to "calibrate."

(There's a deliberate dataset discipline here too: the judge can *propose* high-scoring outputs as new golden examples, but **you confirm inclusion.** The judge never adds to its own ground truth.)

---

## How the prompts improve

Capturing feedback is useless if the next run doesn't get better. Magnolia closes that loop:

A weekly pass reads the low-scored traces (flagged by the judge *or* by you) plus your free-text notes, and an **improvement agent clusters the failures by *where* they went wrong** — was it the wrong worker? a specific skill? the voice? the output shape? Then it proposes a concrete fix **at the right altitude** and lands it as a one-tap proposal with a diff:

- a skill edit,
- a shared `voice.md` / house-style note appended to the relevant workers,
- a worker scoping change,
- a new quality-gate skill,
- a golden example added to the eval set,
- or a change to the judge's own rubric.

A recurring tone complaint becomes *one* shared voice file, not six scattered edits. **Nothing auto-applies.** You accept or reject the diff. That's the principle "the system proposes; you dispose" pointed at its own prompts.

---

## How the judge improves

The judge is itself a versioned prompt, and it gets better the same way everything else does — **through your accept/reject, never through hand-tuning.**

When the judge's scores and your reactions diverge in a clustered, systematic way, the improvement loop drafts a **rubric change** and surfaces it as a proposal with the diff: *"PRD-draft rubric: v4 → v5 proposed."* You approve or decline. You never open the judge's prompt and edit it by hand — governance is accept/reject only.

This is what makes the trust ladder safe to climb: the judge proves itself against *real ground truth* (your agreement), not a proxy. And there's a strict ordering — the judge must be calibrated on a task-type **before** that task-type's actions are allowed to graduate to autonomous, so the system never optimizes against a judge that doesn't yet match your taste.

---

## How it finds new things to do for you — the human column

This is the quiet engine that makes Magnolia *grow* into your chief of staff instead of staying a fixed tool.

The **human task column is treated as a source of opportunity, not just a to-do list.** A recurring **human-queue audit** reads everything sitting in your human lane and asks three questions of each item:

1. **Could an agent just own this?** Many "human" tasks — *draft a follow-up, research X, summarize this, write the request* — were never really human tasks. They get proposed for conversion to agent work.
2. **Is this really just a message?** If the task is "send a note to Priya," the agent **drafts the message for you,** the judge scores the draft, and your action collapses to *review + send.* You rarely get a naked "go do this" — it arrives with a draft attached and a one-tap send.
3. **Is this stale and dead?** A task that's sat untouched for a week gets surfaced as *"kill it / convert it / snooze it?"*

Each finding lands at the top of your board as a **one-tap recommendation.** Over time, the things you keep doing by hand get noticed, drafted, and — once the task-type earns its way up the trust ladder — quietly absorbed. The system watches *how you actually work* and keeps proposing to take more of it off your plate. That's the chief of staff learning the job.

---

## The other half: your standing programs (Cadence)

Everything above is about **verbs** — the discrete work that comes out of your meetings and flows across your board. But a chief of staff also holds the **state of your standing commitments**: the roadmap initiative that's supposed to move discovery → planning → execution on time, the weekly priorities drumbeat, the "did the feature we shipped actually move the metric," the EOS rocks and L10 prep. These aren't tasks you finish — they're loops you *hold*, week after week.

That's the second half of Magnolia, called **Cadence**. The distinction is the whole point:

- The **board** is the verbs of your life — items routed through your attention.
- **Cadence** is the state of your programs — standing loops that quietly compare what you *declared* (the dates, the targets, the commitments) against what's *actually happening* (your transcripts, your tracker, your metrics), on a schedule.

A Cadence program sits there and watches. It does **not** add to your pile. It only puts a verb on your board when something genuinely needs you — a date is about to slip, a commitment has gone quiet, a metric is diverging, an L10 is coming and the prep isn't done. The rest of the time it's calm confirmation: *"W24 digest sent, 9/9 priorities verified done."* A healthy program is a quiet one.

It's the same discipline as the rest of the system. Cadence never reaches out into the world on its own — when it wants to nudge someone or send a digest, it drafts a card and routes it through the **exact same trust ladder and Tier-2 confirm** as everything else. It proposes; you dispose. And it's self-maintaining: it notices when a new initiative is worth tracking and proposes starting a program, notices when one is complete and proposes archiving it, and runs a janitor that keeps the whole portfolio honest (including catching when one of its own watchers has gone blind). You see all of it on a calm, read-only **Cadence tab**, grouped by the kinds of work you run.

The throughline with the board is identical: *the routine legwork of keeping your commitments on track happens quietly, and only what truly needs your judgment ever crosses your desk.*

---

## Putting it together — a day inside Magnolia

You sit down. At the top of the board is a short stack of **proposals** — "this task has sat 7 days, kill it?", "3 human tasks look like message drafts, let agents take them?", "the PRD draft scored 4/10, review before it ships", "rubric change proposed, here's the diff." You tap through them: dispose, don't do.

Below that is **Review** — the artifacts you're accountable for, depth-sorted by the judge, each with a score and a one-line why. This is the real work of your day, and it's exactly as much as you actually need to bless.

Then **People** — the messages, each already drafted, waiting on review + send.

Everything else already happened. It's in the **receipts stream**, scored and reversible, waiting for a skim. The autonomous task-types ran without you. The supervised ones got vetted before they reached you. The shadow ones are quietly building the track record that will, eventually, earn them a rung up.

And every week, the system gets a little better at its job — and proposes to take a little more of yours.
