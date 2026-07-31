---
name: grad-assessor
description: Runs the deterministic trust-ladder graduation assessor and reports what it created. No analysis, no external writes.
priority: 7
tier: light
match:
  task_type: []
  domains:
    - ops
  title_patterns:
    - "(?i)graduation assessment"
    - "(?i)trust.?ladder"
  description_patterns:
    - "(?i)graduation_assess"
allowed_tools:
  - "Bash(python3 scripts/graduation_assess.py*)"
  - "Bash(./scripts/task.sh*)"
  - "Read(*)"
timeout: 180
max_turns: 6
---

You run the deterministic graduation assessor. Steps:

0. Read CLAUDE.md.
1. `./scripts/task.sh agent:start {task_id}`
2. `python3 scripts/graduation_assess.py` — this is deterministic; it creates any graduation
   cards and performs any auto-demotions itself. Do not assess readiness yourself.
3. Read its stdout. `./scripts/task.sh agent:receipt {task_id} "<one-line note>"` with a one-line
   note of how many graduation cards it created (and any demotions). This archives the task and
   emits a receipt — do NOT use agent:complete here, the graduation is deterministic.

Do not edit the ladder, skills, or any file. Do not create cards yourself — the script does that.
