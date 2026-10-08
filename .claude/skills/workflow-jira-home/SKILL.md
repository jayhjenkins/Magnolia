---
name: workflow-jira-home
description: Create Jira issues (Features, Units, Bugs, Regression Defects, etc.) on the team's Jira board (site, project, component, lane label and custom fields from profile) via the Jira MCP. Use when the user wants to log a bug, file a feature request, draft a unit, or create a PRD-linked Feature.
triggers:
  - jira
  - create ticket
  - log bug
  - file bug
  - create feature
  - create unit
  - feature request
---

# Jira Issue Creation

Create issues on the team's Jira board using the Jira MCP. The site, project, component, board, lane label and custom-field ids are per-team values that live in the profile, never in this skill.

## Step 0: Read the team's Jira config (always first)

```bash
python3 scripts/profile_lib.py --jira-config
```

This prints `profile/integrations.yaml` → `project_management.jira` as JSON (secrets removed). Use these keys everywhere below — the placeholders in `{braces}` refer to them:

| Key | Meaning |
|---|---|
| `cloud_id` | The Jira site, e.g. `yourorg.atlassian.net`. Pass as `cloudId`; browse links are `https://{cloud_id}/browse/<KEY>`. |
| `project_key` | Project the issue is created in. |
| `component_id` | Default component id. Empty → omit `components`. |
| `board_id` | The team's board, wherever a board id is needed. |
| `auto_label` | The lane label for Features/Epics (see Lane Rule). Empty → no default label. |
| `product_area` | Display name of the lane `auto_label` routes to. |
| `unlabeled_lane` | Display name of the lane unlabeled issues land in (may be empty). |
| `default_assignee` | Jira accountId Features default to. Empty → leave assignee blank. |
| `conventions` | Free-form team nuance (e.g. client-commitment values, process notes). Honor it. |
| `fields.<name>` | This site's custom-field id for each semantic field (table below). Empty → that field does not exist on this site: omit it, never guess an id. |

If `--jira-config` prints no `project_key`/`cloud_id`, Jira isn't configured: tell the user and point them at the `workflow-doctor` skill instead of guessing.

## When to Use

- User wants to log a bug (client-reported or internal regression)
- User wants to draft a Unit (small enhancement, improvement, or single engineering change)
- User wants to create a Feature (PRD-linked product capability)
- User wants to file a Spike, Hotfix, or other defect type
- User says "create a Jira ticket", "log this bug", "file a feature request", "draft a unit", "create a feature"
- User wants a legacy Epic or Story (still supported, not default)

## Draft Mode (Headless/Agent Context)

When invoked by the **ticket-creator worker** (headless agent dispatch), you operate in **draft mode**:
- You do NOT have access to Jira MCP tools
- You draft the issue content in the task body using the `<!-- JIRA_DRAFT -->` format
- The human reviews the draft on the task board and clicks "Publish to Jira"
- Use this skill as a REFERENCE for field names, issue types, and configuration — not for direct publishing

When invoked **interactively** via `/jira:create` (human is in the CLI session), use the normal flow and call Jira MCP directly — the human is already in the loop.

### JIRA_DRAFT Format

```markdown
<!-- JIRA_DRAFT -->
<!-- JIRA_TYPE:Unit -->
<!-- JIRA_SUMMARY:Short summary here -->
<!-- JIRA_PRIORITY:High -->
<!-- JIRA_LABELS: -->
<!-- JIRA_RELEASE_NOTES:Internal Only -->
<!-- JIRA_PARENT:PROJ-12345 -->
<!-- JIRA_FEATURE_NAME: -->
<!-- JIRA_GTM_DATE: -->
<!-- JIRA_EA_DATE: -->
<!-- JIRA_SPEC_REFERENCE: -->
<!-- JIRA_CLIENT_COMMITMENT: -->
<!-- JIRA_ASSIGNEE: -->

### Summary
Short summary here

### Description
Full description with context...

### Fields
- **Type:** Unit
- **Priority:** High
- **Labels:** (none — Units land in the unlabeled lane by default; set to `{auto_label}` only for Features/Epics or Units that mirror a labeled parent)
- **Release Notes:** Internal Only
- **Parent:** PROJ-12345
<!-- /JIRA_DRAFT -->
```

The draft carries semantic values only. `jira_publish.py` maps them onto this site's custom-field ids from the profile at publish time, and drops any field the profile leaves unmapped.

**Field rules:**
- `JIRA_TYPE`: `Bug`, `Regression Defect`, `Story`, `Unit`, `Epic`, `Feature`, `Spike`, or `Hotfix`
- `JIRA_PRIORITY`: `Highest`, `High`, `Medium`, `Low`, `Lowest` (or empty for default)
- `JIRA_LABELS`: usually empty. The only label PM-OS applies by default is the profile `auto_label`, and only on Features/Epics (see Lane Rule below). For Bugs, Units, Regression Defects, Spikes, Hotfixes — leave this empty. Never invent topical labels (`calendar`, `compliance`, etc.) from the ticket subject — those create permanent noise in a taxonomy you don't own. Add a non-default label only when the user explicitly types it in their prompt.
- `JIRA_RELEASE_NOTES`: `None`, `Internal Only`, or `External` (or empty)
- `JIRA_PARENT`: parent issue key (e.g., `PROJ-12345`) — typically for `Unit` linking to a `Feature` or `Epic`. Optional; leave empty to create unparented.
- `JIRA_FEATURE_NAME`: short label for the Feature/Epic (also accepted as the legacy `JIRA_EPIC_NAME`). Sent to Jira only for **Epics** (the Epic Name field); a Feature's title is its summary.
- `JIRA_GTM_DATE`: `YYYY-MM-DD`, or empty / `TBD` to leave blank (Feature / Epic only)
- `JIRA_EA_DATE`: `YYYY-MM-DD`, or empty / `TBD` to leave blank (Feature / Epic only). Early-access date — typically before GTM. Incomplete dates are fine; they can be filled in later in the Jira UI.
- `JIRA_SPEC_REFERENCE`: absolute URL to the spec/PRD (Feature / Epic only). For PM-OS-driven Features this is the Word/SharePoint URL of `PRD_{slug}.md`. The URL goes in the Jira field, not in the description body — keep the description lean.
- `JIRA_CLIENT_COMMITMENT`: the team's commitment value (see profile `conventions`), or empty (Feature / Epic only)
- `JIRA_ASSIGNEE`: Jira account ID string. **Interactive mode:** use the profile `default_assignee` for Features (override only if the user names someone else); leave empty for non-Feature types. **Draft mode:** leave `<!-- JIRA_ASSIGNEE: -->` empty — `jira_publish.py` applies the profile `default_assignee` at publish time, so the drafting agent does NOT do the lookup. Never invent an assignee; if the profile has none, leave it blank.

**Description hygiene (applies to both draft mode and direct-publish mode):**

The `### Description` body (or `description:` arg in direct MCP calls) becomes the Jira issue body — visible to engineering, QA, and stakeholders. Never include PM-OS-internal references:

- No PM-OS task IDs (`TASK-NNNN`) or phrases like "sibling task", "prior PM-OS task", "spun out of TASK-…"
- No local paths (`datasets/`, `scripts/`, `.claude/`, etc.)
- Reference meetings by **date + participants + customer**, not by local transcript filename

Cross-link via Jira-native references instead (`{project_key}-NNNNN` keys, Confluence URLs, customer names, dates, verbatim quotes).

## Jira Configuration

### Issue Types

Issue type names are passed as `issueTypeName`; ids differ per site, so never hardcode them.

| Type | Hierarchy | Use Case | Where it appears |
|------|-----------|----------|------------------|
| Feature | 1 | **Larger net-new product capability (PRD-scale).** Product-owned, contains Units as children. Replaces Epic. | Roadmap boards — typically not the team kanban. |
| Unit | 0 | **Small enhancement, improvement, or single engineering change.** Independently buildable, testable, deployable. Default for most engineering work. Replaces Story. | The team's board — backlog + kanban. |
| Bug | 0 | **Client-reported problem or error.** Default for `--bug`. | The team's board. |
| Regression Defect | 0 | **Internally-found regression** (QA, internal testing). Use when bug source is internal, not customer. | The team's board. |
| Spike | 0 | Time-boxed investigation. | The team's board. |
| Hotfix | 0 | Emergency fix. | The team's board. |
| Work Item Defect | 0 | Internally-reported problem blocking a work item. | The team's board. |
| Performance Defect | 0 | Performance-class defect. | The team's board. |
| Security Defect | 0 | Security-class defect. | The team's board. |
| Epic | 1 | Legacy / cross-team grouping. Use Feature instead for new work. | Roadmap boards (mirrors Feature). |
| Story | 0 | Legacy flows. Use Unit instead for new engineering work. | The team's board. |

If the site rejects a type name, list the project's types (`getJiraProjectIssueTypesMetadata`) and let the user pick.

### Custom Field Reference

Custom-field ids are site-specific. Resolve each from `fields.<name>` in the `--jira-config` output; if it's empty, leave that field out of the call entirely.

| Semantic field | Profile key | Type | Notes |
|-------|---------|------|-------|
| Epic Name | `fields.epic_name` | string | **Epic only** — the Feature create screen rejects it. Populated from `JIRA_FEATURE_NAME` / legacy `JIRA_EPIC_NAME`. |
| GTM / GA Date | `fields.ga_date` | date | `YYYY-MM-DD` (Feature / Epic only) |
| EA Date | `fields.ea_date` | date | `YYYY-MM-DD` (Feature / Epic only). Early-access date. |
| Spec Reference | `fields.spec_reference` | URL string | Canonical home for the PRD's Word/SharePoint URL (Feature / Epic only). Populate it whenever a published PRD URL exists. |
| Client Commitment | `fields.client_commitment` | labels array | Team-defined values (Feature / Epic only) |
| Release Notes | `fields.release_notes` | select | `None` / `Internal Only` / `External` |
| Regression Area | — | multiselect | Usually many options — set in the Jira UI, not in PM-OS drafts |
| Priority | `priority` | priority | Standard Jira priorities |
| Labels | `labels` | array of string | Lane assignment. `{auto_label}` → the `{product_area}` lane (Features/Epics). Empty → the `{unlabeled_lane}` lane. No auto-prepend; the draft's labels are submitted as-is. |
| Parent | `parent` | issue link | Top-level field on Unit/Sub-task — value is `{"key": "PROJ-XXXXX"}` |
| Assignee | `assignee` | account object | `{"accountId": "..."}`. **Interactive mode:** Features get the profile `default_assignee` unless overridden; leave empty if unset. **Draft mode:** leave blank — `jira_publish.py` fills it at publish time. Non-Feature types: leave unset unless the user specifies. |

### Lane Rule

The profile `auto_label` plays two roles depending on the issue type. If `auto_label` is empty, the team has no lane convention: apply no default labels at all.

**For Features and Epics** — it's an *initiative tag*. Features and Epics usually live on roadmap boards, but the label identifies them as part of the team's lane. PM-OS defaults Features and Epics to `["{auto_label}"]`.

**For Units, Bugs, and other backlog-tier types** — it's a *lane assignment* on the team's board:

- **With `{auto_label}`** → the `{product_area}` lane.
- **Without any labels** → the `{unlabeled_lane}` lane (ad-hoc Bugs, Regression Defects, Units, Spikes, Hotfixes, and other one-off work).

PM-OS defaults these types to `[]`. A Unit that's a child of a labeled Feature should mirror the parent's `{auto_label}` so the Unit lands in the same lane.

**Default by issue type:**

| Type | Default labels |
|---|---|
| Feature, Epic | `["{auto_label}"]` (or `[]` if `auto_label` is empty) |
| Bug, Regression Defect, Hotfix, Work Item Defect, Performance Defect, Security Defect, Spike | `[]` (empty) |
| Unit, Story | `[]` by default. If parented to a Feature/Epic carrying `{auto_label}`, mirror the parent. |

**No-Invent Rule.** Do not synthesize labels from the ticket's topic, product area, customer name, or bug class. The Labels field is routing metadata controlled by the engineering team, not a tagging surface for AI-generated context — context belongs in the description. Only add a non-default label when the user explicitly dictates it in their prompt (e.g., "tag this `mobile-only`"). When in doubt, omit.

**Publish behavior.** `jira_publish.py` submits the draft's labels as-is. No auto-prepend. If the draft has no `JIRA_LABELS`, the issue is created with no labels.

### Workflow Notes

- New issues default to the project's initial status (often Refinement or Backlog); let Jira pick the initial transition
- Some workflows require Release Notes, Regression Area and Components before leaving the first status — check the profile `conventions`
- The profile `component_id` is what makes the issue eligible for the team's boards
- A `Unit` should be parented to a `Feature` (preferred) or `Epic` (legacy). Jira may reject Unit parents of other types — surface the error and let the user pick a valid parent.

---

## Phase 1: Determine What to Create

### If arguments are provided:
- `--feature "name"` → Phase 3 (Feature)
- `--unit "summary"` → Phase 4 (Unit)
- `--bug "summary"` → Phase 2 (Bug), default type=`Bug`
- `--regression "summary"` → Phase 2 (Bug flow), type=`Regression Defect`
- `--epic "name"` → Phase 5 (Legacy Epic)
- `--story "summary"` → Phase 5 (Legacy Story)

### If no arguments (interactive):
Ask the user:

> **Which type fits?**
>
> - **Is something broken or wrong?** → **Bug** (client-reported) or **Regression Defect** (caught internally by QA).
> - **Adding or changing something small** — a tweak, an improvement, a single capability change? → **Unit**. This is the default for most engineering work and is what you usually want.
> - **Net-new product capability** driven by a PRD or larger scope? → **Feature**. Only use this when the work is roadmap-tier; Features usually don't appear on the team kanban.
> - **Need to investigate before scoping?** → **Spike** (time-boxed investigation).
> - **Emergency fix?** → **Hotfix**.
> - Legacy hierarchy needed (Epic / Story)? → mention it explicitly.
>
> What would you like to create?

---

## Phase 2: Create a Bug or Regression Defect

### Step 2.1: Gather Required Info

Ask for (skip any already provided via arguments):

1. **Summary** (required): One-line title
2. **Description** (recommended): What's the issue? Provide context, steps to reproduce, expected vs actual behavior.
3. **Source** (only if type unknown): Was this reported by a client (→ `Bug`) or found internally by QA / product team (→ `Regression Defect`)?

### Step 2.2: Gather Optional Info

Ask if the user wants to set any of these now (they can always be added later in Jira):

- **Priority**: Highest / High / Medium / Low / Lowest
- **Release Notes**: None / Internal Only / External
- **Labels**: usually skip. Bugs default to no labels. Only ask if the user has already mentioned a specific label in their prompt. Do NOT volunteer topical tags. See the Lane Rule above.

Do NOT ask about Regression Area — it has many options and is better set in the Jira UI.

### Step 2.3: Create the Issue

```
mcp__claude_ai_Jira__createJiraIssue(
  cloudId: "{cloud_id}",
  projectKey: "{project_key}",
  issueTypeName: "Bug" | "Regression Defect",
  summary: "<user's summary>",
  description: "<user's description>",
  contentFormat: "markdown",
  additional_fields: {
    "components": [{"id": "{component_id}"}],   // omit if component_id is empty
    "labels": [],  // bugs default to no labels. Only populate if user explicitly named a label.
    // Include only if user provided values (and the field id is mapped):
    "priority": {"name": "<priority>"},
    "{fields.release_notes}": {"value": "<release notes choice>"}
  }
)
```

### Step 2.4: Report Result

Display:
- Issue key (e.g., `PROJ-1234`)
- Direct link: `https://{cloud_id}/browse/PROJ-1234`
- Type: `Bug` or `Regression Defect`
- Status: the project's initial status
- Reminder (if the profile `conventions` says so): which fields must be set in Jira before the issue can move on.

---

## Phase 3: Create a Feature

Use this for larger net-new product capability work — PRD-scale, product-owned, contains Units as children. Replaces Epic for new work.

**Heads-up:** Features usually live on roadmap boards, not on the team kanban. If the work is a small enhancement or single change, use a Unit instead — that's where most engineering work belongs.

### Step 3.1: Gather Required Info

Ask for (skip any already provided):

1. **Feature Name** (required): Short label (e.g., "Mobile Push Notifications"). Used as the summary; it is not sent as Epic Name (the Feature screen rejects that field).
2. **Summary** (required): One-line summary (can match Feature Name or be more descriptive)
3. **Description / Outcome Detail** (required): What is this Feature about and why are we building it? Keep the body lean per the Description hygiene rules — no meeting framing, no version narrative.

### Step 3.2: Gather Feature-Specific Fields

Ask each in turn (skip any already provided via arguments, and skip any whose `fields.<name>` id is empty — the site has no such field). For dates, accept `TBD` or empty as "leave the Jira field blank — fill it in later."

1. **Spec Reference URL** (recommended): The Word/SharePoint URL of the PRD or spec document. Populates the Spec Reference field (`fields.spec_reference`); downstream automation may key off it. Paste the URL, or skip to leave blank.
2. **GTM Date** (optional): `YYYY-MM-DD`, or `TBD` / empty.
3. **EA Date** (optional): `YYYY-MM-DD`, or `TBD` / empty. Early-access date — typically before GTM.
4. **Client Commitment** (optional): Is this committed for a specific event or client? Offer the values listed in the profile `conventions`; otherwise accept what the user types, or skip.
5. **Assignee** (optional): This is interactive mode, so use the profile `default_assignee`; leave empty if unset, unless the user specifies someone else. (In draft mode this field stays blank — `jira_publish.py` fills it at publish time.)

### Step 3.3: Create the Feature

```
mcp__claude_ai_Jira__createJiraIssue(
  cloudId: "{cloud_id}",
  projectKey: "{project_key}",
  issueTypeName: "Feature",
  summary: "<user's summary>",
  description: "<user's description with outcome detail>",
  contentFormat: "markdown",
  additional_fields: {
    "components": [{"id": "{component_id}"}],   // omit if component_id is empty
    "labels": ["{auto_label}"],                 // [] if auto_label is empty
    // Include only if user provided values AND the field id is mapped:
    "{fields.ga_date}": "<YYYY-MM-DD gtm date>",
    "{fields.ea_date}": "<YYYY-MM-DD ea date>",
    "{fields.spec_reference}": "<absolute spec reference url>",
    "{fields.client_commitment}": ["<commitment flag>"],
    // Assignee (interactive mode): profile default_assignee; override only if user named someone else. Omit if the profile has none.
    "assignee": {"accountId": "{default_assignee}"}
  }
)
```

### Step 3.4: Report Result

Display:
- Feature key (e.g., `PROJ-5678`)
- Direct link: `https://{cloud_id}/browse/PROJ-5678`
- Spec Reference: displayed (if set) — confirm it renders as a clickable URL in Jira
- GTM Date / EA Date / Client Commitment: displayed (if set)
- Any field skipped because the profile has no id for it — so the user can fill it in Jira
- Status: the project's initial status

---

## Phase 4: Create a Unit

Use this for engineering work that is **a small enhancement, improvement, or single deployable change** — the default type for most engineering work. Replaces Story. Lands on the team board's backlog and kanban.

### Step 4.1: Gather Required Info

Ask for (skip any already provided):

1. **Summary** (required): One-line title
2. **Description** (required): What is this Unit doing? Include acceptance criteria when known.
3. **Parent issue key** (optional, recommended): The Feature or Epic this Unit belongs under (e.g., `PROJ-42920`). Leave blank if not yet known — the Unit will be created unparented and you can wire it in Jira.

### Step 4.2: Gather Optional Info

Ask:

- **Priority**: Highest / High / Medium / Low / Lowest
- **Release Notes**: None / Internal Only / External
- **Labels**: usually skip. Units default to no labels. If this Unit is a child of a Feature/Epic carrying `{auto_label}`, mirror the parent's label. Otherwise leave empty. Do NOT volunteer topical tags. See the Lane Rule above.

### Step 4.3: Create the Unit

```
mcp__claude_ai_Jira__createJiraIssue(
  cloudId: "{cloud_id}",
  projectKey: "{project_key}",
  issueTypeName: "Unit",
  summary: "<user's summary>",
  description: "<user's description>",
  contentFormat: "markdown",
  additional_fields: {
    "components": [{"id": "{component_id}"}],   // omit if component_id is empty
    "labels": [],  // set to ["{auto_label}"] only if parented to a Feature carrying it
    // Include only if parent provided:
    "parent": {"key": "<PROJ-XXXXX>"},
    // Include only if user provided values (and the field id is mapped):
    "priority": {"name": "<priority>"},
    "{fields.release_notes}": {"value": "<release notes choice>"}
  }
)
```

### Step 4.4: Report Result

Display:
- Unit key + URL
- Parent (if set) — confirm it linked correctly
- If unparented: "Heads-up — this Unit has no parent Feature/Epic yet. Wire it up in Jira when you know where it belongs."

---

## Phase 5: Legacy Epic / Story

Retained for cases where the user explicitly asks for Epic or Story. New work should prefer Feature / Unit (Phases 3 / 4).

The flow is identical to Phase 3 (Epic mirrors Feature) and Phase 4 (Story mirrors Unit). Substitute `issueTypeName: "Epic"` or `"Story"` accordingly. **Epics additionally set Epic Name:** `"{fields.epic_name}": "<epic name>"` (if the id is mapped). The legacy `JIRA_EPIC_NAME` field name is still accepted.

**Label defaults follow the Lane Rule:** Epic defaults to `["{auto_label}"]` (mirrors Feature). Story defaults to `[]` (mirrors Unit).

---

## Error Handling

- **MCP unavailable**: "The Jira MCP is not connected. Make sure you're running inside this project with MCP integrations enabled."
- **Jira not configured** (no `cloud_id` / `project_key` from `--jira-config`): say so and point at the `workflow-doctor` skill. Never guess a site or project.
- **Permission denied**: "You don't have permission to create issues in {project_key}. Check your Jira access."
- **Field validation error** (e.g. "cannot be set"): display the error from Jira and drop the rejected field — it may not be on that issue type's create screen. If a custom-field id looks wrong, suggest correcting `fields.<name>` in `profile/integrations.yaml`.
- **Component not found**: surface the error and suggest checking `component_id` in the profile.
- **Parent issue invalid or wrong type**: Jira rejects Units parented to anything other than a Feature/Epic. Show the error, suggest a valid parent (Feature preferred), and offer to retry without the parent.
- **Unknown issue type**: Normalize common variants (`unit` → `Unit`, `regression defect` → `Regression Defect`, `feature` → `Feature`) before failing. If still unrecognized, list the valid types from the table above.

## Related Skills

- `prd-creation` — Create PRDs that can be linked to Features
- `publish-package` — Read-only lookup of existing Word URLs for a PRD package (for Spec Reference / Feature descriptions); Word copies are created only from the board editor 3-dot menu
- `product-planning` — Meetings-to-backlog pipeline that may generate Unit / Bug drafts
