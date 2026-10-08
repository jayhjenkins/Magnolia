## MANDATORY: Use the workflow-jira-home Skill

Before doing anything else:
1. Read the team's Jira target: `python3 scripts/profile_lib.py --jira-config` (site, project, component, board, lane label and custom-field ids from `profile/integrations.yaml`).
2. Announce: "Using the **workflow-jira-home** skill to create an issue in `{project_key}` on the team's board."
3. Read and follow `.claude/skills/workflow-jira-home/SKILL.md` exactly.

## Purpose

Create Jira issues on the team's Jira project via the Jira MCP. Most issues (Bugs, Units, Regression Defects) land on the team board's backlog and kanban (profile `board_id`). Features and Epics usually live on roadmap boards and carry the profile `auto_label` as an initiative tag. Labels follow the Lane Rule defined in `workflow-jira-home/SKILL.md` — the command does not invent topical labels.

## Arguments

Primary (new hierarchy):
- `/jira:create` — Interactive mode. Asks what kind of issue to create.
- `/jira:create --feature "name"` — Feature (PRD-linked product capability). Replaces Epic for new work.
- `/jira:create --unit "summary"` — Unit (small enhancement, improvement, or single engineering change — the default for most engineering work). Replaces Story. Will prompt for an optional parent Feature/Epic key.
- `/jira:create --bug "summary"` — Bug (client-reported defect).
- `/jira:create --regression "summary"` — Regression Defect (internally-found regression).

Other:
- `/jira:create --spike "summary"` — Time-boxed investigation.
- `/jira:create --hotfix "summary"` — Emergency fix.
- `/jira:create --epic "name"` — Legacy Epic flow (retained for special cases).
- `/jira:create --story "summary"` — Legacy Story flow (retained for special cases).

## What This Creates

**All issues:**
- Component set to the profile `component_id` (omitted if empty)
- Labels follow the Lane Rule: Features/Epics get the profile `auto_label`; Bugs, Units, and other one-offs get no labels (they land in the profile `unlabeled_lane`)
- Defaults to the standard new-issue status for the type
- Optionally sets priority and release notes. Additional labels are only added when the user explicitly names one in their prompt — the command never invents topical tags.

**Units (and legacy Stories):**
- Optional parent issue key (Feature or Epic). If left blank, the Unit is created unparented and the user can wire it in Jira.

**Features (and legacy Epics):**
- Epic Name is set for Epics only (the Feature create screen rejects it)
- Prompts for **Spec Reference** (the PRD/spec Word URL)
- Prompts for **GTM Date** and **EA Date** — either can be left blank or `TBD` to fill in later in the Jira UI
- Prompts for a Client Commitment flag (values from the profile `conventions`)
- Custom fields are written to the ids in the profile `fields` map; any field the profile leaves empty is skipped
- **Assignee** defaults to the profile `default_assignee` if set, else empty — unless a different person is specified

## Examples

```
/jira:create
/jira:create --feature "Mobile Push Notifications"
/jira:create --unit "Wire the dashboard to the new index"
/jira:create --bug "Editor crashes on save"
/jira:create --regression "Amenity image not displaying"
/jira:create --spike "Investigate slow My Requests page load"
/jira:create --hotfix "Login loop on iOS 18.4"
```

Result URLs follow the pattern `https://{cloud_id}/browse/{project_key}-NNNNN`.
