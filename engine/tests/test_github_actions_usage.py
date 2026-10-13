"""Deterministic tests for GitHub Actions usage monitoring."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.github_actions_usage import (  # noqa: E402
    ET,
    UsageSnapshot,
    alert_artifact_name,
    billing_minutes,
    decide_alert,
    estimated_run_minutes,
    format_slack_message,
    make_snapshot,
)


def test_billing_minutes_sums_only_actions_minute_items():
    assert billing_minutes({"usageItems": [
        {"product": "Actions", "unitType": "minutes", "grossQuantity": 700.2},
        {"product": "Actions", "unitType": "GB-hours", "grossQuantity": 5},
        {"product": "Copilot", "unitType": "minutes", "grossQuantity": 10},
    ]}) == 701


def test_repository_estimate_rounds_each_run_and_rerun_attempt():
    runs = [
        {"run_started_at": "2026-08-01T12:00:00Z",
         "updated_at": "2026-08-01T12:00:01Z", "run_attempt": 1},
        {"run_started_at": "2026-08-02T12:00:00Z",
         "updated_at": "2026-08-02T12:01:01Z", "run_attempt": 1},
        {"run_started_at": "2026-08-03T12:00:00Z",
         "updated_at": "2026-08-03T12:00:30Z", "run_attempt": 3},
    ]
    assert estimated_run_minutes(runs) == 1 + 2 + 3


def test_snapshot_reports_days_remaining_and_month_end_projection():
    snapshot = make_snapshot(
        minutes=1500, limit=2000, source="test",
        now=dt.datetime(2026, 8, 23, 12, tzinfo=ET))
    assert snapshot.period == "2026-08"
    assert snapshot.days_elapsed == 23
    assert snapshot.days_remaining == 8
    assert snapshot.percent == 75
    assert snapshot.projected_minutes == 2022


def test_alert_is_once_per_highest_crossed_threshold():
    snapshot = UsageSnapshot("2026-08", 1840, 2000, 23, 8, 31, "test")
    decision = decide_alert(snapshot, (75, 90, 100), None)
    assert decision.should_alert is True
    assert decision.level == 90
    assert decide_alert(snapshot, (75, 90, 100), 90).should_alert is False
    assert alert_artifact_name(snapshot.period, decision.level) == (
        "github-actions-usage-alert-2026-08-90")


def test_manual_test_alert_does_not_require_threshold_crossing():
    snapshot = UsageSnapshot("2026-08", 100, 2000, 23, 8, 31, "test")
    decision = decide_alert(snapshot, (75, 90, 100), None, force=True)
    assert decision.should_alert is True
    assert decision.level is None
    message = format_slack_message(snapshot, decision, test=True)
    assert "TEST" in message
    assert "8 days" in message
    assert "100 / 2,000 minutes" in message


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
