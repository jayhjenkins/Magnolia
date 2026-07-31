# {FEATURE_NAME}

**Pre-Inception Brief**

| Field | Value |
|-------|-------|
| **PM** | {PM Name} |
| **Date** | {YYYY-MM-DD} |
| **Status** | Draft / Ready for Inception / In Inception |
| **JIRA Epic** | {Link} |
| **Specs Repo Path** | {Path} |

---

## Problem & Outcome

**Problem**: {What problem are we solving? Who has this problem? How do we know it's real? 2-3 sentences with evidence.}

**Desired Outcome**: {What does success look like from the user's perspective? What changes for them? 2-3 sentences.}

**Why Now**: {Why is this the right time to invest in this? What's the cost of delay? 1-2 sentences.}

---

## User Context

**Primary User**: {Who is the user? What is their role/context?}

**Current Experience**: {How do they solve this problem today? What's painful about it?}

**Key Insight**: {What do we know from customer conversations, data, or research that shapes our approach?}

---

## Non-Functional Requirements

> **These are mandatory.** The Dynamic Reporting incident showed that skipping non-functional requirements leads to shipped features with unacceptable performance. PMs must define constraints before inception, not after.

- **Performance**: {Response time targets, throughput expectations. E.g., "Reports must render in <10 seconds."}
- **Data Boundaries**: {What data sources are available? What data is NOT available? E.g., "Limited to data currently in CMP; contract ingestion data is out of scope for Phase 1."}
- **Scale**: {How many users/records/transactions? E.g., "Must support boards with up to 500 units."}
- **Security / Access**: {Who can access this? Any compliance considerations?}
- **Quality Bar**: {What is the minimum acceptable quality for first ship? E.g., "EA release to 5 customers" vs. "GA to all."}

{Delete any bullets that genuinely don't apply. But think twice before deleting Performance or Data Boundaries.}

---

## Phases

> Decompose the feature into shippable increments. Each phase should be independently valuable and achievable within one inception cycle. Target: 3-5 days from inception to done per phase.

### Phase 1: {Name}
{1-2 sentences: What ships? What value does the user get?}

### Phase 2: {Name}
{1-2 sentences: What ships? What value does the user get?}

### Phase 3: {Name} (if applicable)
{1-2 sentences: What ships? What value does the user get?}

---

## Scope

### In Scope
- {Capability or behavior that IS part of this work}
- {Capability or behavior that IS part of this work}

### Out of Scope
- {Capability or behavior that is explicitly NOT part of this work, and why}
- {Capability or behavior that is explicitly NOT part of this work, and why}

---

## Open Questions

> List what you don't know yet. Assign an owner and target date for each. These get resolved during or before inception.

| # | Question | Owner | Target Date |
|---|----------|-------|-------------|
| 1 | {What you need to find out} | {Name} | {Date} |
| 2 | {What you need to find out} | {Name} | {Date} |

---

## Links & References

| Resource | Link |
|----------|------|
| JIRA Epic | |
| Specs Repo Directory | |
| Related Meeting Notes | |
| Customer Conversations | |
| Existing Research/Analysis | |

---

## Guidance by Feature Type

> Use this to calibrate how much detail each section needs.

| Feature Type | Example | Depth Needed | Can Skip |
|-------------|---------|-------------|----------|
| **Bug Fix** | "Button doesn't save" | Skip this template entirely | N/A |
| **Small Enhancement** | "Add filter to existing report" | Light (1-2 sentences per section) | Phases, some NFRs |
| **Medium Feature** | "New notification type" | Standard (full template) | Nothing |
| **Large Initiative** | "Board Engagement Portal" | Full (detailed phases, all NFRs) | Nothing |
| **Greenfield / Discovery** | "Resident social features" | Full + extra Open Questions | Nothing |

---

*This template is a transfer-of-understanding artifact, not a specification. Its purpose is to move product intent from the PM's head into a shared document so inception sessions can focus on decomposition and execution, not discovery.*
