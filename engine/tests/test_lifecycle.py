"""Canonical Deal and Account lifecycle behavior."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.lifecycle import (  # noqa: E402
    ACCOUNT_TYPE_LABELS, DEAL_STAGE_LABELS, is_closed_deal_stage,
    is_open_deal_stage, is_paused_deal_stage, is_won_deal_stage,
    listening_may_change_stage,
)


def test_deal_stage_classes_are_disjoint_and_completed_is_not_a_deal_stage():
    assert "Paused" in DEAL_STAGE_LABELS
    assert "Completed" not in DEAL_STAGE_LABELS
    assert is_open_deal_stage("Discovery")
    assert is_paused_deal_stage("Paused")
    assert is_won_deal_stage("Won")
    assert is_closed_deal_stage("Lost")


def test_paused_and_terminal_stages_are_human_locked():
    assert listening_may_change_stage("")
    assert listening_may_change_stage("Proposal")
    assert not listening_may_change_stage("Paused")
    assert not listening_may_change_stage("Won")
    assert not listening_may_change_stage("Lost")


def test_completed_past_client_is_an_account_type_only():
    assert "Completed - Past Client" in ACCOUNT_TYPE_LABELS
    assert "Completed - Past Client" not in DEAL_STAGE_LABELS
    assert "AI Investment" in ACCOUNT_TYPE_LABELS


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
