---
description: Audit all in-flight and next-up Jira Feature cards for PM-OS/ship-it origin and AI-enablement — produces two dated reports with per-card verdicts and summary stats
---
MANDATORY: Use the `workflow-pm-agent-activation-metric` skill at `.claude/skills/workflow-pm-agent-activation-metric/SKILL.md`.

Pull every Feature card currently in development (In Development / Now) and queued up next (Next status), assess each for PM-OS `/ship-it` origin and general AI-enablement using the validated decision trees, and output two dated markdown audit files to `datasets/product/agent-output/`.

$ARGUMENTS
