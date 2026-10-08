import seed_default_crons
import cron_lib


def test_seed_includes_both_phase4_crons(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_lib, "CRON_DIR", str(tmp_path / "cron"))
    monkeypatch.setattr(cron_lib, "JOBS_FILE", str(tmp_path / "cron" / "jobs.json"))
    monkeypatch.setattr(cron_lib, "COUNTER_FILE", str(tmp_path / "cron" / "_counter"))
    seed_default_crons.seed()
    names = {j["name"] for j in cron_lib.list_jobs()}
    assert "Weekly self-improvement" in names
    assert "Graduation ladder" in names
    # idempotent
    added = seed_default_crons.seed()
    assert added == 0


def test_seeds_all_defaults_once(monkeypatch, tmp_path):
    jobs = []
    monkeypatch.setattr(cron_lib, "list_jobs", lambda: list(jobs))
    monkeypatch.setattr(cron_lib, "create_job",
                        lambda **kw: jobs.append({"name": kw["name"], **kw}) or jobs[-1])
    n1 = seed_default_crons.seed()
    n2 = seed_default_crons.seed()  # idempotent: second run adds nothing
    assert n1 == len(seed_default_crons.DEFAULTS)
    assert n2 == 0
    assert any("Doctor" in j["name"] for j in jobs)


def test_doctor_cron_is_monday_9am(monkeypatch):
    captured = {}
    import cron_lib as cl
    monkeypatch.setattr(cl, "list_jobs", lambda: [])
    monkeypatch.setattr(cl, "create_job", lambda **kw: captured.update(kw) or kw)
    seed_default_crons.seed()
    doctor = next(d for d in seed_default_crons.DEFAULTS if d["name"] == "Doctor self-heal")
    assert doctor["cron_expr"] == "0 9 * * 1"  # min hour dom mon dow(Mon=1)


def test_graduation_cron_is_twice_weekly(monkeypatch):
    import cron_lib as cl
    monkeypatch.setattr(cl, "list_jobs", lambda: [])
    monkeypatch.setattr(cl, "create_job", lambda **kw: kw)
    grad = next(d for d in seed_default_crons.DEFAULTS if d["name"] == "Graduation ladder")
    assert grad["cron_expr"] == "30 9 * * 1,4"   # Mon + Thu 09:30


def test_seed_cold_against_real_cron_lib(tmp_path, monkeypatch):
    # Exercises the REAL cron_lib path (no mocks) against an EMPTY tmp dir to
    # simulate a fresh Magnolia clone where _counter / jobs.json don't exist yet.
    # Would have caught the FileNotFoundError crash in _next_cron_id().
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()  # starts empty: no _counter, no jobs.json
    monkeypatch.setattr(cron_lib, "CRON_DIR", str(cron_dir), raising=True)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE", str(cron_dir / "_counter"), raising=True)
    monkeypatch.setattr(cron_lib, "JOBS_FILE", str(cron_dir / "jobs.json"), raising=True)

    # Cold run must NOT raise FileNotFoundError and must seed all defaults.
    n = seed_default_crons.seed()
    assert n == len(seed_default_crons.DEFAULTS)
    # Idempotent second run adds nothing.
    assert seed_default_crons.seed() == 0
    # The job actually persisted via the real cron_lib.
    jobs = cron_lib.list_jobs()
    assert any("Doctor" in j["name"] for j in jobs)


def _template_by_name(name):
    d = next(d for d in seed_default_crons.DEFAULTS if d["name"] == name)
    return d["task_template"]


def test_phase4_crons_route_to_expected_workers(monkeypatch):
    """The two Phase 4 cron task templates route to grad-assessor / eval-analyst.

    Forces task_dispatch's deterministic regex fallback (LLM path stubbed to
    None) so routing is reproducible and offline. The regex scorer keys on
    domains + title_patterns only.
    """
    import task_dispatch

    monkeypatch.setattr(task_dispatch, "_match_worker_llm", lambda task, workers: (None, None))
    workers = task_dispatch.load_workers()

    grad_tpl = _template_by_name("Graduation ladder")
    grad_task = {"title": grad_tpl["title"].replace("{date}", "2026-06-08"),
                 "domain": grad_tpl["domain"], "queue": grad_tpl["queue"]}
    worker, score, _ = task_dispatch.match_worker(grad_task, workers)
    assert worker["name"] == "grad-assessor", f"got {worker['name']} (score {score})"

    si_tpl = _template_by_name("Weekly self-improvement")
    si_task = {"title": si_tpl["title"].replace("{date}", "2026-06-08"),
               "domain": si_tpl["domain"], "queue": si_tpl["queue"]}
    worker, score, _ = task_dispatch.match_worker(si_task, workers)
    assert worker["name"] == "eval-analyst", f"got {worker['name']} (score {score})"


def test_defaults_carry_no_operator_specific_jobs():
    """Seeded defaults are engine-generic: no operator-personal scorecard/rocks jobs."""
    import re
    blob = repr(seed_default_crons.DEFAULTS)
    for pat in (r"Resident Experience", r"scorecard", r"\brocks?\b", r"pm-agent-activation"):
        assert not re.search(pat, blob, re.IGNORECASE), f"default cron leaks /{pat}/"
