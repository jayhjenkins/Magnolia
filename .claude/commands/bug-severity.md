# /bug-severity

## MANDATORY: Use the workflow-bug-severity-assessment Skill

**You MUST use the `workflow-bug-severity-assessment` skill located at `.claude/skills/workflow-bug-severity-assessment/SKILL.md`**

## Before Starting

1. **Announce**: "Using workflow-bug-severity-assessment to evaluate <issue key>."
2. **Read the skill**: Load `.claude/skills/workflow-bug-severity-assessment/SKILL.md`
3. **Follow exactly**: Execute the skill as written

## Purpose

Fetch a Jira bug via the Jira MCP and classify it against Vantaca's Ticket Severity guidelines (Severity 1-4). Impact scoring is out of scope. Optionally, with explicit confirmation, post the assessment as a Jira comment and/or set the Severity field.

## Arguments

- `/bug-severity <jira-url-or-key>` — required. Accepts a full `https://vantaca.atlassian.net/browse/VNT-XXXXX` link or a bare key like `VNT-45191`.

## Examples

```
/bug-severity VNT-45191
/bug-severity https://vantaca.atlassian.net/browse/VNT-45183
```
