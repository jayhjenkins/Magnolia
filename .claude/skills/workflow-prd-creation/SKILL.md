---
name: workflow-prd-creation
description: Use when creating standalone PRD from user input or upstream artifacts - gathers requirements through interactive session, ingests Shipping Greatness artifacts (context brief, press releases, FAQ, AI agent scenarios), validates with prd-validation, and writes PRD file using template
---

# PRD Creation

## Purpose

Create individual Product Requirements Document through interactive session:
- Ingest and cross-reference upstream Shipping Greatness artifacts when available
- Structured requirements gathering across 9 phases
- Apply PRD validation rubric
- Generate PRD file from template
- No fabrication - leave unknown sections as TBD

## When to Use

Activate when:
- User invokes `/project:create-prd`
- `/project:build` or `/project:ship-it` invokes this skill as part of the pipeline
- Manual PRD creation needed
- Capturing product requirements from conversation

## Guiding Principles

1. **PRD is a living document** — It will evolve through collaboration and discovery
2. **Ambitious by default** — Set the ceiling as high as possible. Build the fully-featured version. Code is cheap — ambiguity and timidity are expensive
3. **Ambitious scope, incremental delivery** — Set the ceiling high for WHAT we build. Use vertical slices for HOW we ship it. These are different questions.
4. **Slice, don't cut** — Each delivery slice must stand alone as a useful product for a named audience. "Phase 1 = backend only" is not a slice; "Phase 1 = end-to-end for segment X" is.
5. **Value to the Management Company** — Every PRD must articulate how this feature creates or protects value for management companies (our customers). If you cannot answer "why does a management company care?", the PRD is not ready.
6. **Share and link** — PRDs should be accessible with links to Slack channels
7. **No fabrication** — Leave sections blank/TBD rather than making up information
8. **Reflect what was delivered** — At close, PRD should document actual outcomes

## Product Package Folder

All Shipping Greatness artifacts for a given initiative live in a single package folder:

```
datasets/product/packages/{YYYY}/{slug}/
```

The slug is derived from the product/feature name (lowercase, hyphens, no special chars). If this skill is invoked as part of `/project:build` or `/project:ship-it`, the package folder already exists from earlier phases. If invoked standalone, create the folder if it doesn't exist.

**Preflight -- check for an existing initiative before scaffolding a new folder.** Creating a duplicate package for work that already has one is a known failure mode (it splits the PRD across two folders and orphans the older one). Before creating:
1. Glob existing packages: `datasets/product/packages/{YYYY}/*/` (and adjacent years if this could be a continuation). Read the slugs.
2. Match semantically, not just by slug -- the same initiative is often named differently ("FTUE redesign" vs "new-resident onboarding"). If no obvious slug match, run `context-search` over `product_artifacts` for the topic.
3. If a plausible match exists, **STOP and ask the PM**: *"This overlaps with `{existing-slug}` -- resume that package, or start a new one?"* Default to resuming. Only scaffold a new folder once the PM confirms it's genuinely new.

The PRD is written to a **single canonical location**:
- `{package}/PRD_{slug}.md` — the only copy; do not mirror to `prds/{YYYY}/` (mirror retired). Backlog/roadmap tooling discovers PRDs by globbing `datasets/product/packages/{YYYY}/*/PRD_*.md`.

## Upstream Artifact Ingestion

Before starting the interactive session, check the package folder for these artifacts and ingest them if they exist. Pre-populate PRD sections from them — don't re-ask the PM for information already captured upstream:

| Artifact | Path | What to Extract |
|----------|------|----------------|
| Context Brief | `{package}/context-brief.md` | Customer problems, evidence, behavioral baselines, open questions |
| External Press Release | `{package}/press-release-external.md` | Product vision, customer outcome, key benefits, emotional hook |
| Internal Press Release | `{package}/press-release-internal.md` | Stakeholder impact, implementation considerations, support implications |
| One-Pager | `{package}/one-pager.md` | Tagline, target audience, differentiators, success metrics, timeline |
| Living FAQ | `{package}/living-faq.md` | Import all items in the "Open Questions for PM" section that are still UNANSWERED into the PRD Open Questions. Use answered items to inform requirements and customer-facing positioning. |
| AI Agent Scenarios | `{package}/ai-agent-scenarios.md` | Use Case Inventory, scenarios, and API Requirements → populate Agent/API Scenarios section. Do NOT prescribe endpoint shape in the PRD — engineering owns that. |

If upstream artifacts exist, announce what was imported and which sections were pre-populated. Only ask the PM to confirm or refine pre-populated content, not re-enter it.

## Workflow

### Phase 1: Core Identity

**Ask user:**
- **Project Name**: What is this PRD called?
- **One-liner Description**: 1-3 sentence summary for quick context
- **Background**: Why does this project exist? What problem does it solve?

### Phase 2: Objectives

**Ask user:**
- **Target Customer/User**: Who is this for? (Customer/Partner/Developer/etc.)
- **Customer Statement**:
  - I am: (narrow description of customer with motivations/attributes)
  - I'm trying to: (desired outcome)
  - But: (problem/barrier)
  - Because: (root cause)
  - Which makes me feel: (emotion)
- **Success Metrics**: How will we measure success? (User Experience, Technical Capabilities)
- **Opportunity Sizing**: What's the potential impact?

*Source from meeting signals if available. Leave blank if not known.*

### Phase 3: Scope & Non-Goals

**Ask user:**
- **Use Cases In Scope**: What specific use cases will be supported? Include descriptions.
- **Agent/API Scenarios**: For each human use case, what is the equivalent agent scenario? Reference the use cases and scenarios in `ai-agent-scenarios.md`. (Pre-populate from `ai-agent-scenarios.md` if available. The PRD references the scenarios; it does NOT duplicate endpoint-level API design.)
- **Out of Scope**: What are we explicitly NOT doing? Include reasons.
- **Non-Goals**: What does this product explicitly NOT aim to do? (Distinct from out-of-scope — non-goals define philosophical boundaries, not just "not in this release.")

*Be specific. Think through edge cases.*

### Phase 4: Requirements

**For each milestone, ask:**
- **Milestone Name/Summary**: What does this milestone deliver?
- **Requirements** with:
  - Priority: P0 = Foundation phase (build first), P1 = Expansion phase (build second), P2 = Polish phase (build third). All phases ship — priority determines sequence, not whether something gets built.
  - Dependent Teams
  - User Story: "In order to accomplish X, we will build Y"
  - Acceptance Criteria: How we know requirements are met
  - Figma links (if available)
  - JIRA tickets (if available)

*Only include requirements that are known. Don't fabricate.*

### Phase 4b: Build Sequence

**Organize all requirements into build phases:**
- **Foundation (P0)**: Core capabilities that everything else depends on
- **Expansion (P1)**: Features that extend and enrich the foundation
- **Polish (P2)**: Delight features, optimizations, and refinements

All phases ship. This is sequencing, not cutting. Include dependency tracking between phases.

### Phase 5: Timeline

**Ask user:**
- **Milestones**: List of major milestones (e.g., Architecture, Design, Development, Testing, Launch)
- **Expected Delivery Timeline**: Target dates/quarters for each milestone
- **Teams Leading Each Phase**: Who owns each milestone?

*If timeline is not yet determined, leave as TBD.*

### Phase 6: Links and Resources

**Ask user:**
- **Slack Channels**: Related Slack channels for discussion
- **Figma/Design Links**: Experience design and content
- **Architecture/Technical Design Docs**: Lucidcharts, Miro, etc.
- **JIRA Project/Tracking Links**: Project plan / tracking
- **Any Other Relevant Links**

*Only include links that exist. Don't create placeholder URLs.*

### Phase 7: Metrics and Learning Agenda

**Ask user:**
- **Goals and Hypotheses**: What do you want to happen?
- **Signals**: What would indicate success or validation?
- **Metrics**: What to measure to see these signals?

### Phase 8: Open Questions / Tracked Assumptions

**Populate from Living FAQ:**
- Import every item from the "Open Questions for PM" section of `living-faq.md` that is still UNANSWERED
- Add any new questions surfaced during PRD creation
- Track assumptions the PRD is built on — what happens if they're wrong?

### Phase 9: Appendix / Upstream Artifact Links

**Link to all upstream artifacts** for traceability:
- Context Brief, Press Releases, One-Pager, Living FAQ, AI Agent Scenarios
- Any expansion proposals or red team reports (if they exist from a previous `/build` run)

*Only link artifacts that actually exist. Don't create placeholder links.*

### Fact-Checking Requirements

**CRITICAL**: Do not fabricate information. For any section where information is not provided:
- Leave the section blank or marked "TBD"
- Note in changelog that section needs input
- Prompt user: "Do you have this information available?"

### Validate PRD

**Invoke:** `prd-validation` skill

- Apply 5-point rubric
- Drafting PRDs may have warnings but not blockers
- Actionable PRDs must pass all criteria

### Write PRD File

**Use template:** `datasets/product/templates/prd-template.md`

**Output (single canonical location):**
- `datasets/product/packages/{YYYY}/{slug}/PRD_{slug}.md` — the only copy; do not mirror to `prds/{YYYY}/` (mirror retired)

**Set initial status:** 🚧 Drafting

**Add changelog entry:**
```markdown
| {YYYY-MM-DD} | Initial draft created | {user} |
```

### Optionally Update Backlog

**Ask user:**
"Add to backlog.md? (yes/no)"

If yes: Prepend to `datasets/product/backlog.md`

## PRD Statuses

| Status | When to Use |
|--------|-------------|
| 🚧 Drafting | Initial creation, known to be incomplete |
| 🏃 Actionable | Eng has agreed there's enough to start work |
| 🔒 Closed | Represents what was finally delivered |
| ❗ Abandoned | Project cancelled or superseded |

## Success Criteria

- PRD created with all provided information
- Unknown sections marked as TBD (not fabricated)
- PRD file written to correct location
- Template structure followed
- Changelog entry added
- Optionally added to backlog

## Related Skills

- `prd-validation`: Validates PRD quality
- `product-planning`: Batch PRD creation from meetings
- `meeting-synthesis`: Gathers evidence from meeting transcripts
- `vision-clarifier`: Produces press releases and one-pager (upstream)
- `devils-advocate`: Produces Living FAQ (upstream)
- `agentic-api-designer`: Produces AI Agent Scenarios (upstream)
- `ambition-expander`: Reviews PRD and proposes scope expansion (downstream)
- `red-team-reviewer`: Adversarial PRD validation (downstream)




