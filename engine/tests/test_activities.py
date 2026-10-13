"""Activity extraction, ownership, due-date, and dedupe tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.activities import (  # noqa: E402
    action_items, create_activities, create_activities_from_result,
)


class FakeCRM:
    def __init__(self): self.created = []
    def existing_activity_titles(self, meeting): return {"already exists"}
    def item_names(self, ids): return ["Jeremy Schneck"]
    def owner_ids_for_context(self, **kwargs): return ["account-owner"]
    def create_activity(self, **kwargs): self.created.append(kwargs); return str(len(self.created))


def test_legacy_list_repr_is_parsed_without_model_call():
    assert action_items("['Send recap', 'Jeremy to send export']") == [
        "Send recap", "Jeremy to send export",
    ]


def test_tasks_are_deduped_typed_owned_and_dated():
    crm = FakeCRM()
    meeting = {"id": "m1", "title": "Lumen", "date": "2026-07-31",
               "owner_ids": ["111"]}
    ctx = {"prospect_account_id": "a1", "contact_ids": ["c1"], "deal_id": "d1"}
    made = create_activities(crm, meeting, ctx, [
        "Already exists", "Harbor team to create PRD",
        "Jeremy to provide exports", "Schedule next meeting Thursday",
    ])
    assert made == ["1", "2", "3"]
    assert crm.created[0]["task_type"] == "Do it"
    assert crm.created[1]["task_type"] == "Chase"
    assert crm.created[0]["owner_ids"] == ["account-owner"]
    assert crm.created[0]["due_date"] == "2026-08-07"
    assert crm.created[2]["due_date"] == "2026-08-06"
    assert crm.created[0]["related_ids"] == ["a1", "c1", "d1"]


def test_explicit_and_relative_due_dates_are_honored():
    crm = FakeCRM()
    meeting = {"id": "m1", "title": "Acme", "date": "2026-08-02",
               "owner_ids": []}
    ctx = {"prospect_account_id": "a1", "contact_ids": [], "deal_id": "d1"}
    create_activities(crm, meeting, ctx, [
        "Send recap tomorrow", "Prepare scope by August 15",
        "Follow up in 2 weeks", "Submit plan 9/3",
    ])
    assert [row["due_date"] for row in crm.created] == [
        "2026-08-03", "2026-08-15", "2026-08-16", "2026-09-03",
    ]


def test_structured_action_item_uses_its_title():
    assert action_items([{"title": "Send proposal", "owner": "Alex"}]) == [
        "Send proposal"]


def test_model_action_items_require_complete_answered_evidence():
    meeting = {"id": "m1", "title": "Acme", "date": "2026-08-02",
               "owner_ids": []}
    ctx = {"prospect_account_id": "a1", "contact_ids": [], "deal_id": None}
    invalid = [
        {"status": "PARTIAL", "value": ["Send recap"]},
        {"status": "ANSWERED", "value": ["Send recap"], "evidence": {
            "excerpt": "Send recap", "confidence": .2, "source_id": "m1",
            "occurred_at": "2026-08-17T00:00:00+00:00"}},
        ["Send recap"],
    ]
    for value in invalid:
        crm = FakeCRM()
        assert create_activities_from_result(
            crm, meeting, ctx, {"meeting": {"action_items": value}}) == []
        assert crm.created == []

    crm = FakeCRM()
    valid = {"status": "ANSWERED", "value": ["Send recap"], "evidence": {
        "excerpt": "Send recap", "confidence": .99, "source_id": "m1",
        "occurred_at": "2026-08-17T00:00:00+00:00"}}
    assert create_activities_from_result(
        crm, meeting, ctx, {"meeting": {"action_items": valid}}) == ["1"]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
