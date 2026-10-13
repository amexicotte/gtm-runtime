"""Scheduled shadow artifact reconciliation and gate tests."""
import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "reconcile_phase2_shadow_observations",
    ROOT / "scripts" / "reconcile_phase2_shadow_observations.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


RUN = {
    "databaseId": 100, "event": "schedule", "status": "completed",
    "conclusion": "success", "createdAt": "2026-08-19T07:16:10Z",
    "headSha": "abc", "url": "https://github.example/run/100",
}


def write_artifacts(path, *, accepted=True, eligible=True, date="2026-08-19"):
    (path / "email-shadow-review.json").write_text(json.dumps({
        "accepted": accepted, "observation_date": date,
        "scheduled_for": "2026-08-19T02:00:00-04:00",
        "schedule_expression": "0 6 * * *",
        "errors": [] if accepted else ["rejected"],
        "summary": {"threads": 1},
    }))
    (path / "email-shadow-report.json").write_text(json.dumps({
        "observation_eligible": eligible, "observation_date": date,
    }))
    (path / "schedule-gate.json").write_text(json.dumps({
        "eligible": True,
        "scheduled_for": "2026-08-19T02:00:00-04:00",
        "schedule_expression": "0 6 * * *",
    }))


def test_assess_accepts_matching_scheduled_artifacts():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory); write_artifacts(path)
        result = module.assess_run(dict(RUN), path)
    assert result["accepted"] and result["date"] == "2026-08-19"
    assert result["summary"] == {"threads": 1}


def test_assess_accepts_bounded_delay_but_rejects_runtime_ineligibility():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory); write_artifacts(path, eligible=False)
        result = module.assess_run(dict(RUN), path)
    assert not result["accepted"]
    assert any("eligible" in error for error in result["errors"])


def test_post_shadow_gates_require_three_dates_and_set_only_approvals():
    calls = []
    runner = lambda command: calls.append(command) or ""
    try:
        module.apply_post_shadow_gates({
            "accepted_dates": ["2026-08-19", "2026-08-20"],
            "ready_for_live_verification": False,
        }, runner=runner, today=module.dt.date(2026, 8, 22))
    except RuntimeError:
        pass
    else:
        raise AssertionError("two dates must not open post-shadow gates")
    module.apply_post_shadow_gates({
        "accepted_dates": ["2026-08-19", "2026-08-20", "2026-08-21"],
        "ready_for_live_verification": True,
    }, runner=runner, today=module.dt.date(2026, 8, 22))
    assert [command[3] for command in calls] == [
        "EMAIL_SHADOW_OBSERVATION_DATES", "EMAIL_SHADOW_REVIEW_APPROVED",
        "EMAIL_AUTOMATIC_WRITES_APPROVED",
    ]
    assert not any("EMAIL_RUN_MODE" in command for command in calls)


def test_post_shadow_gates_reject_same_day_third_observation():
    state = {
        "accepted_dates": ["2026-08-19", "2026-08-20", "2026-08-21"],
        "ready_for_live_verification": True,
    }
    try:
        module.apply_post_shadow_gates(
            state, runner=lambda command: "", today=module.dt.date(2026, 8, 21))
    except RuntimeError as error:
        assert "completed" in str(error)
    else:
        raise AssertionError("same-day observation must not open live gates")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
