"""Deterministic scheduled-shadow acceptance tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.email_shadow_review import review_shadow_report  # noqa: E402


def review(payload, **kwargs):
    return review_shadow_report(
        payload, expected_date="2026-08-19",
        scheduled_for="2026-08-19T02:00:00-04:00",
        schedule_expression="0 6 * * *", **kwargs)


def report():
    return {
        "mode": "shadow", "observation_eligible": True,
        "observation_date": "2026-08-19", "threads_failed": 0,
        "normalization_failures": 0, "page_saturation_gaps": 0,
        "contact_identity_ambiguity_events": 0, "threads_ingest_only": 0,
        "cap_reached": False, "field_write_batches": 0, "records_changed": 0,
        "activities_created": 0, "activities_completed": 0,
        "narrative_updates_posted": 0, "narrative_updates_deduplicated": 0,
        "contact_discoveries_proposed": 0, "contacts_created": 0,
        "contact_creation_conflicts": 0,
        "threads_candidates": 1, "threads_reasoned": 1,
        "threads_relevant": 1, "threads_irrelevant": 0,
        "threads": [{
            "source_id": "email:abc123",
            "occurred_at": "2026-08-19T05:00:00+00:00",
            "relevant": True,
            "timeline_event": {
                "headline": "Proposal timing confirmed",
                "body": ("The client confirmed the proposal review schedule and asked "
                         "Harbor to reconnect next Tuesday after the internal team meets. "
                         "Next step: follow up after their review."),
                "labels": ["Next step"],
            },
            "ambiguities": [],
            "proposed_fields": [{
                "target": "deal", "field": "deal_stage", "value": "Proposal",
                "confidence": 0.95,
            }],
            "new_activities": [{
                "title": "Follow up after proposal review", "due_date": "2026-08-25",
                "confidence": 0.9,
            }],
            "activity_completions": [], "validation_warning_codes": [],
        }],
        "nudges": {"posted": 0, "cleared": 0, "ambiguous": 0},
        "warnings": [],
    }


def test_accepts_complete_scheduled_shadow_report():
    result = review(report())
    assert result["accepted"] and result["errors"] == []
    assert result["summary"]["proposed_fields"] == 1


def test_rejects_manual_ineligible_or_effectful_report():
    payload = report()
    payload["observation_eligible"] = False
    payload["records_changed"] = 1
    payload["contacts_created"] = 1
    result = review(payload, event_name="workflow_dispatch")
    assert not result["accepted"]
    assert any("not triggered" in error for error in result["errors"])
    assert any("records_changed" in error for error in result["errors"])
    assert any("contacts_created" in error for error in result["errors"])


def test_rejects_prohibited_won_ambiguous_deal_and_raw_address():
    payload = report()
    thread = payload["threads"][0]
    thread["ambiguities"] = ["multiple plausible active Deals"]
    thread["proposed_fields"][0]["value"] = "Closed Won"
    thread["raw"] = {"subject": "Private subject"}
    payload["warnings"] = ["contact client@example.com"]
    result = review(payload)
    assert not result["accepted"]
    assert any("Closed Won" in error for error in result["errors"])
    assert any("despite entity ambiguity" in error for error in result["errors"])
    assert any("email address" in error for error in result["errors"])
    assert any("prohibited raw-email fields" in error for error in result["errors"])


def test_rejects_activity_below_automatic_confidence():
    payload = report()
    payload["threads"][0]["new_activities"][0]["confidence"] = 0.7
    payload["threads"][0]["activity_completions"] = [{
        "activity_id": "123", "confidence": 0.89,
        "expects_client_response": True,
    }]
    result = review(payload)
    assert not result["accepted"]
    assert any("invalid new Activity" in error for error in result["errors"])
    assert any("invalid Activity completion" in error for error in result["errors"])


def test_accepts_delayed_runner_but_rejects_wrong_dst_cron_slot():
    result = review(report())
    assert result["accepted"]
    wrong = review_shadow_report(
        report(), expected_date="2026-08-19",
        scheduled_for="2026-08-19T02:00:00-04:00",
        schedule_expression="0 7 * * *")
    assert not wrong["accepted"]
    assert any("cron expression" in error for error in wrong["errors"])


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
