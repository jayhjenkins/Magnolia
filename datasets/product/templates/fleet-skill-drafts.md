# Fleet Skill Drafts: {FEATURE_NAME}

**Date:** {YYYY-MM-DD}
**Status:** 🚧 DRAFT — pre-build, unvalidated. This is a v1 sketch of what a fleet skill would look like for this feature, written from the PRD's agentic use cases before any code exists. There is no expectation that it's correct, complete, or runnable — the point is to force the workflow to be thought through now, and to give Fleet a running start instead of a blank page.

<!--
Shaped after Fleet's real skill format (hoai-v2, .claude/skills/build-fleet-skill/SKILL.md,
read 2026-08-25) so the handoff is recognizable to the fleet team: YAML frontmatter, a Goal,
CRITICAL guards, numbered Steps, values inlined directly in prose (no {{Variable}} placeholders).

What's DIFFERENT from a production skill, on purpose:
  - No CLI commands or endpoint names — those don't exist yet. Steps describe capabilities in
    plain language ("retrieves the association's current configuration"), not tool calls.
  - No fixtures, no QA validation, no Jira/PR. Fleet's own `build-fleet-skill` pipeline (live API
    verification, SDK/CLI wiring, fixture generation, QA gate against a live tenant) is what turns
    this draft into something real, once the feature is actually built.
  - Marked DRAFT throughout so nobody mistakes this for a shipped or even buildable skill.

What's the SAME as production convention, because these are genuine product decisions worth
making now rather than later:
  - Halt/refusal prose never names a human recipient — the skill describes what it found and
    stops; it doesn't know who reviews the note.
  - Org-specific knobs (dollar thresholds, voice policy, consent rules) are called out separately
    and default to "policy layer, not skill" unless there's a specific reason to inline them.
  - Trigger surface is one of: chat-initiated, action-item-triggered, orchestrator-dispatched.
-->

---

## How to Use This Document

**For the PM:** writing each draft below is the point, not just reading it back. If a step is hard to write in plain English, that's a sign the scenario in `ai-agent-scenarios.md` is underspecified — fix it there, not here.

**For the Fleet team:** treat each draft as a strawman skeleton, not a spec to implement literally. Your own `build-fleet-skill` pipeline still runs in full — live API verification, the real SKILL.md, fixtures, QA validation. This draft exists so you're starting from "here's roughly the shape" instead of "here's a PRD, please reverse-engineer the agent workflow yourself."

---

## Skill Draft 1: {Scenario Title}

> Maps to Scenario {N} in `ai-agent-scenarios.md`. DRAFT — unvalidated, pre-build.

```yaml
---
name: {kebab-case-action-verb-name}
description: "Use when {trigger, in plain English — e.g. 'a community's monthly digest is due and the community manager wants a drafted email ready to review'}."
status: draft — not yet buildable, no live API to verify against
---
```

### Goal

{1-2 sentences: what this skill accomplishes, in plain English. E.g. "Draft a resident-facing monthly digest email from the community's feed activity, using the community's brand voice, and hold it for the community manager to review and send."}

### Trigger Surface

{One of: chat-initiated (a user asks the agent directly) / action-item-triggered (a workflow step routes to it) / orchestrator-dispatched (a parent skill invokes it for one of its own steps) — or name more than one if the scenario supports it.}

### CRITICAL Guards

{The hard stops, inlined as concrete rules — not placeholders. Never name a human recipient: the skill halts and adds a note describing what it found; it doesn't know who reviews it.}

- **Never:** {action the skill must not take, and the condition that makes it a hard stop}
- **If {condition}:** {halts with a note describing what it found — no named recipient}
- ...

### Steps

{Numbered, imperative, plain English — derived from the scenario's steps in `ai-agent-scenarios.md`. Describe capabilities, not CLI calls or endpoints (those don't exist yet).}

1. {First step}
2. {Next step}
3. {Next step}
...

### Org-Specific Knobs

{Values that would plausibly vary by management company. Default assumption: these live in the policy layer, not inlined into the skill, unless there's a specific product reason.}

| Knob | Likely default | Policy layer or inlined? |
|------|-----------------|---------------------------|
| {knob} | {default value} | Policy layer (default) |

### Capabilities This Skill Will Need

{Pulled straight from the API Requirements in `ai-agent-scenarios.md` (Task 3) — plain language, no endpoint shape. These don't exist yet; that's expected at this stage.}

- {Capability}
- ...

### Candidate Test Scenarios

{One happy path, one refusal/halt, one edge case — mirrors the shape Fleet's own fixtures will eventually take, without inventing fixture JSON.}

- **Happy path:** {use case} → {expected behavior}
- **Refusal / halt:** {use case} → {expected behavior}
- **Edge case:** {use case} → {expected behavior}

### Open Questions

{Anything the PM doesn't have a confident answer to yet.}

- {Question}

---

## Skill Draft 2: {Scenario Title}

{Same structure as Skill Draft 1}

---

## Skill Draft 3: {Scenario Title}

{Same structure as Skill Draft 1}

---

## Changelog

| Date | Changes | By |
|------|---------|-----|
| {YYYY-MM-DD} | Initial drafts from ai-agent-scenarios.md | |
