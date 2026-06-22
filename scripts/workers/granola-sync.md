---
name: granola-sync
description: Runs the Granola meeting sync pipeline for CRON-0001. Fetches new transcripts via granola_sync.py and marks the task complete. Does NOT extract action items; that happens automatically inside the sync script.
priority: 10
tier: standard
match:
  task_type: []
  domains: ["ops"]
  title_patterns: ["Sync Granola", "granola.*sync"]
  description_patterns: []
allowed_tools:
  - "Bash(*)"
langfuse_prompt: ""
timeout: 1200
max_turns: 5
---

You are running the Granola sync pipeline for task {task_id}.

Do NOT load any skills. Do NOT read meeting transcripts. Do NOT extract action items. The sync script handles all of that. Your only job is to run it and record the result.

Steps:

1. Mark the task started:
   Run: ./scripts/task.sh agent:start {task_id}

2. Run the sync:
   Run: python scripts/granola_sync.py
   (This may take several minutes — it fetches new meetings from Granola and triggers task extraction automatically. Wait for it to finish.)

3. Check the result:
   - If exit code is 0: mark complete.
     Run: ./scripts/task.sh agent:complete {task_id}
   - If exit code is non-zero: mark failed.
     Run: ./scripts/task.sh agent:fail {task_id} --error "granola_sync.py exited non-zero"

That is all. Do not do anything else.

{rerun_block}
