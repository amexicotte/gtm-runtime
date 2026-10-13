"""Sanitized email CLI failure-report tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.email_main import _failure_report  # noqa: E402


def test_failure_report_is_actionable_without_error_or_source_content():
    error = RuntimeError("client@example.com confidential email body")
    report = _failure_report(
        shadow=True, error=error,
        now=dt.datetime(2026, 8, 19, 14, tzinfo=dt.timezone.utc))
    rendered = str(report)
    assert report["mode"] == "shadow"
    assert report["observation_date"] == "2026-08-19"
    assert report["fatal_error_code"] == "RuntimeError"
    assert report["threads_failed"] == 1
    assert not report["observation_eligible"]
    assert "client@example.com" not in rendered
    assert "confidential email body" not in rendered


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
