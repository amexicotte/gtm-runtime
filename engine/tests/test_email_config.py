"""Phase 2 rollout gates cannot be bypassed by selecting live mode."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.config import config  # noqa: E402


SECRETS = {"MONDAY_API_TOKEN": "m", "OPENROUTER_API_KEY": "o",
           "SLACK_BOT_TOKEN": "s"}


def test_email_reasoning_requires_explicit_data_processing_approval():
    with patch.dict(os.environ, SECRETS, clear=True), patch.object(
            config, "EMAIL_REASONING_APPROVED", False):
        try:
            config.require_email_shadow()
        except RuntimeError as error:
            assert "EMAIL_REASONING_APPROVED" in str(error)
        else:
            raise AssertionError("unapproved email reasoning must be blocked")


def test_live_requires_three_reviewed_nights_and_every_approval():
    overrides = {
        "EMAIL_REASONING_APPROVED": True,
        "EMAIL_AUTOMATIC_WRITES_APPROVED": True,
        "EMAIL_SHADOW_REVIEW_APPROVED": True,
        "EMAIL_LISTENING_ACTIVATED_AT": "2026-08-20T00:00:00-04:00",
        "EMAIL_SHADOW_OBSERVATION_DATES": ("2026-08-14", "2026-08-15"),
        "EMAIL_SHADOW_MIN_NIGHTS": 3,
        "EMAIL_IMMEDIATE_ROLLOUT_APPROVED": False,
    }
    patches = [patch.object(config, key, value) for key, value in overrides.items()]
    with patch.dict(os.environ, SECRETS, clear=True):
        for context in patches: context.start()
        try:
            try:
                config.require_email_live()
            except RuntimeError as error:
                assert "3 distinct" in str(error)
            else:
                raise AssertionError("two shadow nights must not enable live mode")
        finally:
            for context in reversed(patches): context.stop()


def test_owner_approved_immediate_rollout_uses_bounded_canary_instead_of_nights():
    overrides = {
        "EMAIL_REASONING_APPROVED": True,
        "EMAIL_AUTOMATIC_WRITES_APPROVED": True,
        "EMAIL_IMMEDIATE_ROLLOUT_APPROVED": True,
        "EMAIL_SHADOW_REVIEW_APPROVED": False,
        "EMAIL_LISTENING_ACTIVATED_AT": "2026-08-20T00:00:00-04:00",
        "EMAIL_SHADOW_OBSERVATION_DATES": (),
    }
    patches = [patch.object(config, key, value) for key, value in overrides.items()]
    with patch.dict(os.environ, SECRETS, clear=True):
        for context in patches: context.start()
        try:
            config.require_email_live()
        finally:
            for context in reversed(patches): context.stop()


def test_shadow_dates_must_be_real_completed_et_dates():
    with patch.object(config, "EMAIL_SHADOW_OBSERVATION_DATES", (
            "not-a-date", "2099-01-01", "2026-08-14")):
        try:
            config.validated_shadow_observation_dates()
        except RuntimeError as error:
            assert "invalid" in str(error) or "past ET" in str(error)
        else:
            raise AssertionError("arbitrary observation strings must be rejected")


def test_global_write_kill_switch_blocks_live_business_effects():
    with patch.object(config, "BUSINESS_LISTENING_WRITE_KILL_SWITCH", True):
        try:
            config.require_business_writes()
        except RuntimeError as error:
            assert "kill switch" in str(error)
        else:
            raise AssertionError("global kill switch must block writes")


def test_portfolio_live_requires_separate_reasoning_and_write_approval():
    with patch.dict(os.environ, SECRETS, clear=True), patch.object(
            config, "PORTFOLIO_REASONING_APPROVED", True), patch.object(
            config, "PORTFOLIO_PATTERN_WRITES_APPROVED", False), patch.object(
            config, "MONDAY_AGENT_EVENTS_BOARD_ID", "123"):
        try:
            config.require_portfolio_live()
        except RuntimeError as error:
            assert "brief writes" in str(error)
        else:
            raise AssertionError("portfolio persistence must require explicit approval")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
