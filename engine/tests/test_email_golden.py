"""Versioned Phase 2 email/task golden cases."""
from copy import deepcopy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.business_events import BusinessEvent  # noqa: E402
from runtime.email_reconciliation import prepare_email_result  # noqa: E402


FIXTURE = Path(__file__).with_name("fixtures") / "email-phase2-golden.json"


def _event(payload: dict) -> BusinessEvent:
    content = payload["content"]
    return BusinessEvent(
        source="email", source_id=payload["source_id"],
        occurred_at=payload["occurred_at"],
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        title=payload["title"],
        account_ids=tuple(payload["account_ids"]),
        contact_ids=tuple(payload["contact_ids"]),
        deal_ids=tuple(payload["deal_ids"]), content=content,
        attachments=tuple(payload["attachments"]),
        open_activity_candidates=tuple(payload["open_activity_candidates"]),
        captured_at=dt.datetime(2026, 8, 17, tzinfo=dt.timezone.utc).isoformat(),
    )


def test_phase2_email_golden_cases():
    suite = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert suite["version"] == 1 and len(suite["cases"]) >= 2
    for case in suite["cases"]:
        result = deepcopy(case["model_output"])
        event = prepare_email_result(
            result, event=_event(case["event"]),
            contact_email_ids=case["contact_email_ids"],
            ambiguous_deal=case["ambiguous_deal"],
            completion_confidence=0.9,
            message_sources=[tuple(row) for row in case["message_sources"]],
            outbound_sources=[tuple(row) + (tuple(
                item["url"] for item in case["event"]["attachments"]
                if item.get("url")), tuple(case["event"]["contact_ids"]), tuple(
                    (item["url"], row[1]) for item in case["event"]["attachments"]
                    if item.get("url")))
                for row in case["outbound_sources"]],
            inbound_sources=(
                [tuple(row) for row in case["message_sources"]]
                if not case["outbound_sources"] else []),
        )
        expected = case["expected"]
        assert list(event.account_ids) == expected["account_ids"], case["name"]
        assert list(event.contact_ids) == expected["contact_ids"], case["name"]
        assert list(event.deal_ids) == expected["deal_ids"], case["name"]
        assert sorted((result.get("deal") or {}).keys()) == expected["deal_fields"], case["name"]
        assert [row["title"] for row in result["_validated_new_activities"]] == expected["new_activity_titles"], case["name"]
        completions = result["_validated_activity_completions"]
        assert [row["activity_id"] for row in completions] == expected["completed_activity_ids"], case["name"]
        assert [row["source_timeline_id"] for row in completions] == expected["completion_source_timeline_ids"], case["name"]
        assert event.timeline_event.headline == expected["timeline_headline"], case["name"]
        if "write_unmatched_contact" in expected["prohibited_actions"]:
            assert result["contacts"] == [], case["name"]


if __name__ == "__main__":
    test_phase2_email_golden_cases()
    print("1/1 tests passed")
