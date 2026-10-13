"""Fail-closed tests for committed Phase 2 live activation evidence."""
import datetime as dt
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from finalize_phase2_rollout import finalize  # noqa: E402


CONTACT = "10000000011"


def state(mode):
    return {"record_ids": {"contact": CONTACT},
            "verification": {"mode": mode, "accepted": True, "errors": []}}


def evidence():
    shadow = {"accepted_dates": ["2026-08-16", "2026-08-17", "2026-08-18"],
              "ready_for_live_verification": True}
    first_report = {"mode": "live", "threads_failed": 0, "threads_reasoned": 1}
    retry_report = {"mode": "live", "threads_failed": 0, "threads_reasoned": 0}
    cleanup = {"cleaned_up": True, "ready": False}
    return shadow, first_report, retry_report, cleanup


def test_complete_sanitized_evidence_enables_committed_state():
    shadow, first_report, retry_report, cleanup = evidence()
    result = finalize(
        shadow=shadow, preflight=state("preflight"), first_state=state("first"),
        first_report=first_report, retry_state=state("retry"),
        retry_report=retry_report, cleanup=cleanup,
        now=dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
    assert result["live_enabled"] is True
    assert result["accepted_dates"] == shadow["accepted_dates"]
    assert result["evidence"]["retry_reasoning_calls"] == 0
    assert result["validation_mode"] == "three_night_shadow_and_canary"


def test_owner_approved_immediate_canary_needs_no_calendar_wait():
    shadow = {"accepted_dates": [], "ready_for_live_verification": False}
    first_report = {"mode": "live", "threads_failed": 0, "threads_reasoned": 1}
    retry_report = {"mode": "live", "threads_failed": 0, "threads_reasoned": 0}
    cleanup = {"cleaned_up": True, "ready": False}
    result = finalize(
        shadow=shadow, preflight=state("preflight"), first_state=state("first"),
        first_report=first_report, retry_state=state("retry"),
        retry_report=retry_report, cleanup=cleanup, immediate_validation=True,
        now=dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
    assert result["live_enabled"] is True
    assert result["accepted_dates"] == []
    assert result["validation_mode"] == "bounded_live_canary"


def test_same_day_observation_and_reasoning_retry_fail_closed():
    shadow, first_report, retry_report, cleanup = evidence()
    shadow["accepted_dates"][-1] = "2026-08-19"
    retry_report["threads_reasoned"] = 1
    try:
        finalize(
            shadow=shadow, preflight=state("preflight"),
            first_state=state("first"), first_report=first_report,
            retry_state=state("retry"), retry_report=retry_report,
            cleanup=cleanup,
            now=dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "three accepted past" in str(error)
        assert "zero reasoning" in str(error)


def test_wrong_contact_or_missing_cleanup_fails_closed():
    shadow, first_report, retry_report, cleanup = evidence()
    wrong = state("first"); wrong["record_ids"]["contact"] = "production"
    cleanup["cleaned_up"] = False
    try:
        finalize(
            shadow=shadow, preflight=state("preflight"), first_state=wrong,
            first_report=first_report, retry_state=state("retry"),
            retry_report=retry_report, cleanup=cleanup,
            now=dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "exact Contact" in str(error)
        assert "not cleaned" in str(error)


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
