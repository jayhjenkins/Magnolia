# Fleet Skill Drafts: {FEATURE_NAME}

**Date:** {YYYY-MM-DD}
**Framing:** Draft skill definitions for fleet agents — what each agent needs to know, what tools it needs, and the step-by-step workflow it follows. These are PM-authored v1 drafts; engineering and the fleet team refine them into production skill files.

**Status:** Draft — pending fleet skill format alignment (see TASK-1693)

<!--
These drafts translate each agentic scenario from ai-agent-scenarios.md into an
instructive skill spec: what the agent IS TOLD, not what is SAID ABOUT it.

Each draft includes:
  - Purpose and trigger (from the scenario)
  - Authority model (who the agent acts for)
  - Required capabilities (plain-language tool list — engineering maps to actual CLIs)
  - Domain knowledge (what the agent must understand)
  - Workflow steps (imperative, 2nd person)
  - Guardrails (what NOT to do)
  - Verification (how to confirm success)

These do NOT include:
  - Actual CLI command syntax (engineering fills those in)
  - API endpoint paths or HTTP methods
  - Authentication/authorization implementation
  - Model selection or prompt engineering details
-->

---

## How to Read These Drafts

Each skill draft below maps 1:1 to a scenario in `ai-agent-scenarios.md`. The scenario describes the job in third person ("the agent does X"); the skill draft restates it as second-person instructions ("you do X") that a fleet agent can follow.

**Engineering's job:** Replace the `[TOOL: ...]` placeholders with actual CLI commands or API tool definitions. Add authentication, error handling, and retry logic per fleet conventions.

**Fleet team's job:** Convert the draft into the production skill format (SKILL.md, config, or whatever the fleet skill system uses). Tune the domain knowledge section for accuracy. Add eval criteria.

---

## Skill Draft 1: {Scenario Title}

### Metadata

| Field | Value |
|-------|-------|
| **Skill name** | `{kebab-case-name}` |
| **Maps to scenario** | Scenario {N} in `ai-agent-scenarios.md` |
| **Trigger** | {When/how this skill activates — schedule, user request, event, etc.} |
| **Authority** | {Who the agent acts for — the user, a customer, the system, or none} |
| **Latency** | {realtime / interactive / background} |
| **Scope** | {internal only / external partners / both} |

### Purpose

{One paragraph: what job this agent performs and what outcome it delivers. Written as instructions to the agent.}

### Required Capabilities

{Plain-language list of what tools/APIs/CLIs the agent needs. Engineering maps these to actual tool definitions.}

- `[TOOL: {plain-language description of capability}]` — {what the agent uses it for}
- `[TOOL: {plain-language description of capability}]` — {what the agent uses it for}
- ...

### Domain Knowledge

{What the agent must understand about the business domain, data model, or user context to do this job correctly. Not implementation details — domain rules, edge cases, terminology.}

- {Domain fact or rule the agent needs}
- {Domain fact or rule the agent needs}
- ...

### Workflow

{Step-by-step instructions in imperative, 2nd person. Each step maps to a scenario step but is phrased as "do this" not "the agent does this."}

1. {First step — e.g., "Retrieve the association's current configuration using [TOOL: get association config]."}
2. {Next step}
3. {Next step}
4. ...

### Guardrails

{What the agent must NOT do. Edge cases to watch for. Failure responses.}

- **Never:** {thing the agent must never do}
- **If {failure condition}:** {how to respond — retry, escalate, abort, fall back}
- **If {failure condition}:** {how to respond}
- ...

### Verification

{How the agent (or a human reviewer) confirms the job completed successfully.}

- [ ] {Success criterion from the scenario}
- [ ] {Success criterion}
- ...

---

## Skill Draft 2: {Scenario Title}

{Same structure as Skill Draft 1}

---

## Skill Draft 3: {Scenario Title}

{Same structure as Skill Draft 1}

---

## Open Questions for Fleet Team

{Questions that surfaced during drafting that the fleet team needs to answer before these skills go to production.}

- {Question about fleet skill format, tool registration, auth model, etc.}
- ...

---

## Changelog

| Date | Changes | By |
|------|---------|-----|
| {YYYY-MM-DD} | Initial drafts from ai-agent-scenarios.md | |
