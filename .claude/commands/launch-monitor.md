---
description: Set up rollout-health monitoring for a feature launch — goal metric, do-no-harm metrics, verified Pendo tagging, baseline, sample-size target, monitor file, and a recurring check
---
MANDATORY: Use the `workflow-launch-monitor` skill at `.claude/skills/workflow-launch-monitor/SKILL.md`.

Set up launch monitoring for the feature named/pathed in the argument below. Identify what's launching, decide on a goal metric plus do-no-harm metrics, validate the Pendo tagging for each before trusting it, establish a baseline, compute how many observations are needed for statistical significance and the resulting duration estimate, write the monitor file into the feature's product package, and set up a recurring check (via the `loop` skill) that tracks it and evaluates once the target is hit.

If a monitor file already exists for this feature, re-invoking this runs a targeted correction pass instead of starting over — see the skill's Workflow note.

$ARGUMENTS
