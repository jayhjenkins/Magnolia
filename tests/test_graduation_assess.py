def _judged(task_lib, task_type, score, react=None, n=1, when="2026-06-01T00:00:00Z"):
    ids = []
    for _ in range(n):
        tid, _fp = task_lib.create_task("t", queue="agent", task_type=task_type)
        ch = {"judge_score": score, "judge_kind": "document", "judge_scored_at": when}
        if react:
            ch["human_react"] = react
        task_lib.update_task(tid, changes=ch)
        ids.append(tid)
    return ids


def test_user_chat_turns_counts_only_user_events(tasks_root):
    import task_lib, graduation_assess, chat_transcript
    tid, _ = task_lib.create_task("t", queue="agent", task_type="prd-draft")
    assert graduation_assess.user_chat_turns(tid) == 0  # no transcript yet
    chat_transcript.append_event(tid, {"role": "user", "kind": "msg", "text": "hi"})
    chat_transcript.append_event(tid, {"role": "assistant", "kind": "msg", "text": "ok"})
    chat_transcript.append_event(tid, {"role": "user", "kind": "msg", "text": "tweak it"})
    assert graduation_assess.user_chat_turns(tid) == 2


def test_effective_react_explicit_wins(tasks_root):
    import graduation_assess as g
    assert g.effective_react({"id": "X", "human_react": "down", "status": "done"}) == "down"
    assert g.effective_react({"id": "X", "human_react": "up", "status": "open"}) == "up"


def test_effective_react_implicit_up_on_clean_accept(tasks_root):
    import task_lib, graduation_assess as g
    tid, _ = task_lib.create_task("t", queue="agent", task_type="prd-draft")
    # done + zero chat turns -> implicit up
    assert g.effective_react({"id": tid, "status": "done"}) == "up"


def test_effective_react_none_when_open_or_high_friction(tasks_root):
    import task_lib, graduation_assess as g, chat_transcript
    tid, _ = task_lib.create_task("t", queue="agent", task_type="prd-draft")
    assert g.effective_react({"id": tid, "status": "open"}) is None   # not accepted
    for _ in range(2):
        chat_transcript.append_event(tid, {"role": "user", "kind": "msg", "text": "redo"})
    assert g.effective_react({"id": tid, "status": "done"}) is None    # 2 turns > FRICTION_MAX


def test_metrics_no_self_vote_in_approval(tasks_root):
    # A judged-good task with NO reaction and status open must NOT count as approval.
    import graduation_assess as g
    tasks = [{"id": "A", "judge_score": 9, "status": "open"},
             {"id": "B", "judge_score": 9, "status": "open"}]
    n, approval, agreement, reacted = g._metrics(tasks)
    assert n == 2
    assert approval == 0.0       # judge≥7 alone no longer approves
    assert reacted == 0


def test_metrics_counts_implicit_up(tasks_root):
    import graduation_assess as g
    tasks = [{"id": "A", "judge_score": 9, "status": "done"}]   # clean accept -> implicit up
    n, approval, agreement, reacted = g._metrics(tasks)
    assert approval == 1.0
    assert reacted == 1
    assert agreement == 1.0      # judge_pos == implicit up


def _write_thresholds(path, overrides):
    import json, os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"tiers": {}, "thresholds": overrides, "demote_signals": {}}, f)


def test_min_reacted_floor_blocks_when_approval_lowered(tasks_root, tmp_path):
    # Isolate the floor: drop min_approval/min_agreement to 0 so only min_reacted can block.
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _write_thresholds(p, {"shadow_to_supervised":
                          {"min_judged": 4, "min_approval": 0.0, "min_agreement": 0.0, "min_reacted": 3}})
    # 4 judged (meets min_judged) but only 2 explicit reactions -> reacted(2) < min_reacted(3).
    _judged(task_lib, "prd-draft", 9, react="up", n=2)
    _judged(task_lib, "prd-draft", 3, react=None, n=2)  # no reaction, status open -> not reacted
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert created == []  # blocked solely by the min_reacted floor


def test_min_reacted_floor_passes_at_threshold(tasks_root, tmp_path):
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _write_thresholds(p, {"shadow_to_supervised":
                          {"min_judged": 4, "min_approval": 0.0, "min_agreement": 0.0, "min_reacted": 3}})
    _judged(task_lib, "prd-draft", 9, react="up", n=3)   # reacted(3) == min_reacted(3)
    _judged(task_lib, "prd-draft", 3, react=None, n=1)
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert any(c["task_type"] == "prd-draft" for c in created)  # floor cleared


def test_first_hop_fires_at_four_judged(tasks_root, tmp_path):
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _judged(task_lib, "prd-draft", 9, react="up", n=4)   # exactly min_judged=4, reacted=4>=3
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert any(c["task_type"] == "prd-draft" for c in created)


def test_ready_type_gets_graduation_card(tasks_root, tmp_path):
    import task_lib, graduation_assess, ladder_lib
    p = str(tmp_path / "ladder.json")
    _judged(task_lib, "prd-draft", 9, react="up", n=8)  # >=6, 100% approval+agreement
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert any(c["task_type"] == "prd-draft" and c["proposed_tier"] == "supervised" for c in created)
    cards = [t for t in task_lib.list_tasks() if t.get("card_type") == "graduation"]
    assert len(cards) == 1
    assert cards[0].get("grad_proposed_tier") == "supervised"


def test_not_ready_no_card(tasks_root, tmp_path):
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _judged(task_lib, "prd-draft", 9, react="up", n=3)  # below min_judged=4
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert created == []


def test_idempotent_no_duplicate_card(tasks_root, tmp_path):
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _judged(task_lib, "prd-draft", 9, react="up", n=8)
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-11T00:00:00Z")
    cards = [t for t in task_lib.list_tasks() if t.get("card_type") == "graduation"]
    assert len(cards) == 1  # not re-carded


def test_auto_demote_after_consecutive_bad_windows(tasks_root, tmp_path):
    import task_lib, graduation_assess, ladder_lib
    p = str(tmp_path / "ladder.json")
    ladder_lib.set_tier("prd-draft", "supervised", path=p)
    _judged(task_lib, "prd-draft", 3, react="down", n=8)  # approval 0% << supervised entry bar
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert ladder_lib.tier_of("prd-draft", path=p) == "supervised"   # 1st bad window: no demote yet
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-17T00:00:00Z")
    assert ladder_lib.tier_of("prd-draft", path=p) == "shadow"  # 2nd consecutive: demoted


def test_demotion_emits_receipt(tasks_root, tmp_path):
    import task_lib, graduation_assess, ladder_lib
    p = str(tmp_path / "ladder.json")
    ladder_lib.set_tier("prd-draft", "supervised", path=p)
    _judged(task_lib, "prd-draft", 3, react="down", n=8)
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-17T00:00:00Z")  # triggers demotion
    receipts = [t for t in task_lib.list_tasks()
                if t.get("card_type") == "receipt" and t.get("receipt_kind") == "ladder-demotion"]
    assert len(receipts) == 1
    r = receipts[0]
    assert r["status"] == "open"  # actionable — Keep/Undo, not auto-archived

    fm = task_lib.read_task(r["id"])["frontmatter"]
    assert fm["demote_task_type"] == "prd-draft"
    assert fm["demote_from_tier"] == "supervised"
    assert fm["demote_to_tier"] == "shadow"


def test_no_demote_on_insufficient_data(tasks_root, tmp_path):
    import task_lib, graduation_assess, ladder_lib
    p = str(tmp_path / "ladder.json")
    ladder_lib.set_tier("prd-draft", "supervised", path=p)
    _judged(task_lib, "prd-draft", 3, react="down", n=2)  # n below the entry-bar min_judged for this tier
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-17T00:00:00Z")
    assert ladder_lib.tier_of("prd-draft", path=p) == "supervised"  # sparse window must NOT demote


def test_no_autonomous_proposal_when_autonomy_off(tasks_root, tmp_path, monkeypatch):
    import task_lib, graduation_assess, ladder_lib, profile_lib
    p = str(tmp_path / "ladder.json")
    ladder_lib.set_tier("prd-draft", "supervised", path=p)
    _judged(task_lib, "prd-draft", 9, react="up", n=15)
    monkeypatch.setattr(profile_lib, "autonomy_enforcement", lambda root=None: False)
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert created == []
    cards = [t for t in task_lib.list_tasks() if t.get("card_type") == "graduation"]
    assert len(cards) == 0


def test_autonomous_proposal_when_autonomy_on(tasks_root, tmp_path, monkeypatch):
    import task_lib, graduation_assess, ladder_lib, profile_lib
    p = str(tmp_path / "ladder.json")
    ladder_lib.set_tier("prd-draft", "supervised", path=p)
    _judged(task_lib, "prd-draft", 9, react="up", n=15)
    monkeypatch.setattr(profile_lib, "autonomy_enforcement", lambda root=None: True)
    created = graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    assert any(c["proposed_tier"] == "autonomous" for c in created)


def test_dismissed_grad_not_recreated(tasks_root, tmp_path):
    import task_lib, graduation_assess
    p = str(tmp_path / "ladder.json")
    _judged(task_lib, "prd-draft", 9, react="up", n=8)
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-10T00:00:00Z")
    cards = [t for t in task_lib.list_tasks() if t.get("card_type") == "graduation"]
    assert len(cards) == 1
    task_lib.cancel_task(cards[0]["id"], reason="rejected", actor="human")
    graduation_assess.assess(ladder_path=p, now_iso="2026-06-11T00:00:00Z")
    active_grads = [t for t in task_lib.list_tasks()
                    if t.get("card_type") == "graduation" and t.get("status") == "open"]
    assert len(active_grads) == 0


def test_action_types_no_implicit_approval(tasks_root):
    import graduation_assess as g
    t = {"id": "X", "status": "done", "task_type": "send-message"}
    assert g.effective_react(t) is None
    t["human_react"] = "up"
    assert g.effective_react(t) == "up"
