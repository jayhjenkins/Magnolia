// deriveAttentionState(task) -> { surface, lane }
// surface: 'now' | 'activity'
// lane (within now): 'review' | 'decide' | 'people' | 'agent-queue'
//
// TRUTH TABLE (queue / status / agent_status -> lane)  — mirrors index.html board grouping
//   agent   / done|open  / complete           -> now.review        (Ready for Review; artifact you own)
//   agent   / in-review  / *                  -> now.review        (manually promoted to review)
//   agent   / open|inprog/ running|queued|null -> now.agent-queue   (running shows the running icon)
//   agent   / open       / (not complete)      -> now.agent-queue   (Start Agent lives here)
//   agent   / blocked    / failed              -> now.agent-queue   (Rerun lives here)
//   collab  / done       / *                   -> activity          (completed; visible in Activity feed)
//   collab  / in-review  / *                   -> now.decide        (manually promoted to review)
//   collab  / open|inprog/ complete|needs-human-> now.decide        (slot pick / jira publish ready)
//   collab  / open|inprog/ other               -> now.agent-queue
//   human   / any active                       -> now.people
//   waiting / any active                       -> now.people
function deriveAttentionState(task) {
  const q = task.queue, s = task.status, a = task.agent_status;
  // Cron tasks run silently — never surface in Now (agent-queue or review).
  if (task.tags && task.tags.includes('cron')) return { surface: 'activity', lane: null };
  if (q === 'agent') {
    if (a === 'complete' || s === 'done' || s === 'in-review') return { surface: 'now', lane: 'review' };
    return { surface: 'now', lane: 'agent-queue' };
  }
  if (q === 'collab') {
    if (s === 'done') return { surface: 'activity', lane: null };
    if (s === 'in-review' || a === 'complete' || a === 'needs-human') return { surface: 'now', lane: 'decide' };
    return { surface: 'now', lane: 'agent-queue' };
  }
  // human + waiting
  return { surface: 'now', lane: 'people' };
}
