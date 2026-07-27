#!/usr/bin/env python3
"""graduation_assess.py — deterministic trust-ladder assessor (no LLM).

Per task-type over a rolling window, computes human-approval rate and judge<->human
agreement. Creates a `graduation` card when a type clears its next tier's thresholds;
auto-demotes a graduated type whose metrics fall below its current tier's entry bar for
`demote_consecutive` consecutive assessments. Reversible by construction. Exit 0 always.

Run weekly by the graduation cron. `now_iso` is injectable for tests (Date.now()-free).
"""
import argparse
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_lib       # noqa: E402
import ladder_lib     # noqa: E402
import chat_transcript  # noqa: E402
import profile_lib    # noqa: E402

JUDGE_GOOD_THRESHOLD = 7
NEXT = {"shadow": "supervised", "supervised": "autonomous"}
ENTRY_KEY = {"supervised": "shadow_to_supervised", "autonomous": "supervised_to_autonomous"}

FRICTION_MAX = 1  # a clean accept tolerates at most one follow-up chat turn


def user_chat_turns(task_id):
    """Count the operator's chat turns on a card (role == 'user' events)."""
    try:
        events = chat_transcript.read_events(task_id)
    except Exception:
        return 0
    return sum(1 for e in events if e.get("role") == "user")


ACTION_TASK_TYPES = {"send-message", "schedule-meeting"}


def effective_react(t):
    """The operator's reaction to a task, explicit or passively inferred.

    Explicit 👍/👎 always wins. Otherwise a terminally *accepted* card
    (status 'done' is set only by a human action — Done / Send / Publish;
    agent:complete leaves status 'open') with at most FRICTION_MAX follow-up
    chat turns is read as an implicit 'up'. Never infers a 'down': abandonment
    and heavy iteration are too ambiguous to punish, so explicit 👎 stays the
    only hard negative.

    Action-type tasks (send-message, schedule-meeting) are excluded from
    implicit approval: the user archiving a draft they never sent is not
    approval of the draft's quality. These types require explicit 👍/👎.

    Returns 'up' | 'down' | None.
    """
    react = t.get("human_react")
    if react in ("up", "down"):
        return react
    tt = t.get("task_type") or ""
    if tt in ACTION_TASK_TYPES:
        return None
    if t.get("id") and t.get("status") == "done" and user_chat_turns(t["id"]) <= FRICTION_MAX:
        return "up"
    return None


def _metrics(tasks):
    """Return (n, approval_rate, agreement_rate, reacted) for judged tasks.

    Uses effective_react (explicit 👍/👎, else inferred from a clean accept).
    Approval = effective_react == 'up' (no judge self-vote). Agreement is over
    tasks with any (explicit or implicit) reaction. reacted is the denominator
    for the min_reacted floor.
    """
    n = len(tasks)
    if n == 0:
        return 0, 0.0, 0.0, 0
    approvals = 0
    agree = reacted = 0
    for t in tasks:
        try:
            score = float(t.get("judge_score"))
        except (TypeError, ValueError):
            score = None
        judge_pos = score is not None and score >= JUDGE_GOOD_THRESHOLD
        react = effective_react(t)
        if react == "up":
            approvals += 1
        if react in ("up", "down"):
            reacted += 1
            if (react == "up") == judge_pos:
                agree += 1
    approval_rate = approvals / n
    agreement_rate = (agree / reacted) if reacted else 0.0
    return n, approval_rate, agreement_rate, reacted


def _within(t, cutoff_iso):
    stamp = t.get("judge_scored_at") or t.get("human_reacted_at") or t.get("updated") or ""
    return stamp >= cutoff_iso


def assess(ladder_path=None, now_iso=None):
    """Assess every judged task-type. Returns a list of {task_type, proposed_tier} carded."""
    th = ladder_lib.thresholds(path=ladder_path)
    now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) \
        if now_iso else datetime.now(timezone.utc)
    cutoff_iso = (now - timedelta(days=th["window_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")

    active = task_lib.list_tasks()
    archived = task_lib.list_archived(limit=2000)
    judged = [t for t in (active + archived)
              if t.get("judge_score") is not None and _within(t, cutoff_iso)]

    by_type = {}
    for t in judged:
        by_type.setdefault(t.get("task_type") or t.get("domain") or "uncategorized", []).append(t)

    existing_grad = {t.get("grad_task_type") for t in active
                     if t.get("card_type") == "graduation" and t.get("status") == "open"}
    recently_dismissed = {t.get("grad_task_type") for t in (active + archived)
                          if t.get("card_type") == "graduation"
                          and t.get("status") in ("cancelled", "done")
                          and _within(t, cutoff_iso)}
    created = []
    for task_type, tasks in by_type.items():
        cur = ladder_lib.tier_of(task_type, path=ladder_path)
        n, approval, agreement, reacted = _metrics(tasks)

        # --- promotion check ---
        nxt = NEXT.get(cur)
        if nxt:
            if nxt == "autonomous" and not profile_lib.autonomy_enforcement():
                nxt = None
        if nxt:
            bar = th[ENTRY_KEY[nxt]]
            # min_reacted is a belt-and-suspenders floor: at the default thresholds it
            # is redundant with min_approval (approval counts only real 'up's, so passing
            # min_approval already forces reacted >= min_reacted). It binds only when a
            # user lowers min_approval via a datasets/evals/ladder.json override.
            ready = (n >= bar["min_judged"] and reacted >= bar["min_reacted"]
                     and approval >= bar["min_approval"] and agreement >= bar["min_agreement"])
            if ready and task_type not in existing_grad and task_type not in recently_dismissed:
                _create_graduation_card(task_type, cur, nxt, n, approval, agreement,
                                        [t["id"] for t in tasks[:5]])
                created.append({"task_type": task_type, "proposed_tier": nxt})

        # --- demotion check (only for already-climbed types) ---
        # Demotion is gated on min_judged just like promotion: an under-sized window
        # must not demote a type on noise. Insufficient data resets the streak (treated
        # as not-below) rather than accumulating toward a demotion.
        # (The promotion and demotion metric bands are non-overlapping by construction —
        # the next-tier bar sits well above the current-tier entry bar — so a single
        # window cannot both promotion-card and register as below in practice.)
        if cur != "shadow":
            entry = th[ENTRY_KEY[cur]]
            if n >= entry["min_judged"]:
                below = (agreement < entry["min_agreement"] or approval < entry["min_approval"])
            else:
                below = False  # not enough data this window to justify demotion
            streak = ladder_lib.note_demotion_signal(task_type, below, path=ladder_path)
            if below and streak >= th["demote_consecutive"]:
                ladder_lib.demote(task_type, path=ladder_path)
                ladder_lib.note_demotion_signal(task_type, False, path=ladder_path)  # reset
    return created


def _create_graduation_card(task_type, cur, nxt, n, approval, agreement, example_ids):
    title = f"Graduate '{task_type}': {cur} -> {nxt}?"
    desc = (f"**{task_type}** is ready to climb the trust ladder.\n\n"
            f"- Current tier: **{cur}** -> proposed: **{nxt}**\n"
            f"- Judged tasks (window): **{n}**\n"
            f"- Your approval rate: **{round(approval*100)}%**\n"
            f"- Judge<->you agreement: **{round(agreement*100)}%**\n\n"
            f"Example tasks: {', '.join(example_ids)}\n\n"
            f"Graduating is reversible - it auto-demotes if scores later drop.")
    tid, _ = task_lib.create_task(title, queue="collab", priority="medium", domain="ops",
                                  creator="agent", description=desc, tags=["graduation"],
                                  card_type="graduation")
    task_lib.update_task(tid, changes={
        "grad_task_type": task_type, "grad_current_tier": cur, "grad_proposed_tier": nxt,
        "grad_n": n, "grad_approval_pct": round(approval * 100),
        "grad_agreement_pct": round(agreement * 100), "grad_examples": example_ids,
    })
    return tid


def main():
    ap = argparse.ArgumentParser(description="Deterministic trust-ladder assessor")
    ap.add_argument("--now", default=None, help="ISO now (testing/cron determinism)")
    args = ap.parse_args()
    try:
        created = assess(now_iso=args.now)
        print(f"[graduation_assess] created {len(created)} graduation card(s): "
              f"{[c['task_type'] for c in created]}")
    except Exception as e:
        print(f"[graduation_assess] error (non-fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
