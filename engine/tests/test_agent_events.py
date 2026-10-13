"""Agent Events ledger and narrative Update retry tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.agent_events import publish_cached_event, render_narrative_update  # noqa: E402
from runtime.business_events import attach_business_event  # noqa: E402


ACTIVATED = "2026-08-17T00:00:00+00:00"


def _result():
    result = {
        "relevant": True,
        "timeline_event": {
            "headline": "Onsite plan confirmed",
            "body": ("Jane confirmed the August onsite plan, approved the discovery "
                     "scope, and asked Harbor to send the preparation document before "
                     "the team meets. Alex will deliver the document on Friday."),
            "labels": ["Decision", "Next step"],
        },
    }
    attach_business_event(
        result, source="meeting", meeting_id="m1", occurred_at="2026-08-18",
        title="Acme onsite", content="source content",
        ctx={
            "prospect_account_id": "a1", "contact_ids": ["c1"],
            "deal_id": "d1", "attendee_emails": ["jane@acme.com"],
        },
        captured_at=dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc),
    )
    return result


class CRM:
    def __init__(self):
        self.effects = {}
        self.updates = []
        self.status = "Ready"
        self.failed = []
        self.fail_on_post = 0

    def claim_agent_event(self, event, result):
        if self.status == "Processed":
            return {"id": "e1", "claimed": False, "effects": self.effects}
        self.status = "Processing"
        return {"id": "e1", "claimed": True, "effects": dict(self.effects)}

    def active_client_project_for_deal(self, account, deal):
        return {"id": "p1"}

    def update_has_event_marker(self, item, effect):
        return any(row[0] == item and effect in row[1] for row in self.updates)

    def post_narrative_update(self, item, body):
        if self.fail_on_post and len(self.updates) + 1 == self.fail_on_post:
            raise RuntimeError("transient update failure")
        self.updates.append((item, body))
        return f"u{len(self.updates)}"

    def record_agent_event_effect(self, event_id, effect, details):
        self.effects[effect] = details

    def complete_agent_event(self, event_id): self.status = "Processed"
    def fail_agent_event(self, event_id, error):
        self.status = "Failed"; self.failed.append(error)


def test_event_posts_once_to_account_contact_deal_and_active_client_project():
    crm, result = CRM(), _result()
    first = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert first.posted == 4 and first.targets == 4
    assert [row[0] for row in crm.updates] == ["a1", "c1", "d1", "p1"]
    assert all("harbor-event:" in body for _, body in crm.updates)
    assert crm.status == "Processed"

    second = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert second.posted == 0 and second.deduplicated == 4
    assert len(crm.updates) == 4


def test_partial_failure_resumes_only_missing_effects_without_duplicate_updates():
    crm, result = CRM(), _result()
    crm.fail_on_post = 2
    try:
        publish_cached_event(crm, result, activation_at=ACTIVATED)
    except RuntimeError as error:
        assert "transient" in str(error)
    else:
        raise AssertionError("expected transient failure")
    assert len(crm.updates) == 1 and len(crm.effects) == 1
    assert crm.status == "Failed"

    crm.fail_on_post = 0
    resumed = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert resumed.posted == 3 and resumed.deduplicated == 1
    assert len(crm.updates) == 4 and crm.status == "Processed"


def test_update_marker_recovers_a_crash_between_post_and_ledger_write():
    crm, result = CRM(), _result()
    event = __import__("runtime.business_events", fromlist=["BusinessEvent"]).BusinessEvent.from_cache(
        result["_event"])
    effect = event.effect_key("account", "a1")
    crm.updates.append(("a1", render_narrative_update(event, effect_key=effect)))
    outcome = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert outcome.posted == 3 and outcome.deduplicated == 1
    assert len(crm.updates) == 4


def test_blank_or_future_activation_performs_no_ledger_or_update_write():
    crm, result = CRM(), _result()
    assert publish_cached_event(crm, result, activation_at="").disabled
    assert publish_cached_event(
        crm, result, activation_at="2026-08-19T00:00:00+00:00").disabled
    assert crm.status == "Ready" and crm.updates == []


def test_irrelevant_event_is_ledgered_as_processed_without_narrative_effects():
    result = {"relevant": False}
    attach_business_event(
        result, source="meeting", meeting_id="m2", occurred_at="2026-08-18",
        title="No material change", content="pleasantries",
        ctx={"prospect_account_id": "a1"},
        captured_at=dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc),
    )
    crm = CRM()
    outcome = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert outcome.posted == 0 and outcome.targets == 0
    assert crm.status == "Processed" and crm.updates == []


def test_missing_relevance_fails_closed_and_client_project_needs_no_deal():
    result = _result()
    result.pop("relevant")
    result["_event"]["deal_ids"] = ()
    crm = CRM()
    crm.client_projects_for_account = lambda account: [{"id": "p2", "active": True}]
    outcome = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert outcome.posted == 0 and crm.updates == []

    result["relevant"] = True
    crm = CRM()
    crm.client_projects_for_account = lambda account: [{"id": "p2", "active": True}]
    outcome = publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert outcome.posted == 3
    assert [item for item, _body in crm.updates] == ["a1", "c1", "p2"]


def test_irrelevant_event_still_stamps_deterministic_interaction():
    class InteractionCRM(CRM):
        def __init__(self): super().__init__(); self.stamps = []
        def stamp_last_interaction(self, **kwargs): self.stamps.append(kwargs)
    result = {"relevant": False}
    attach_business_event(
        result, source="meeting", meeting_id="m3", occurred_at="2026-08-18",
        title="No material change", content="pleasantries",
        ctx={"prospect_account_id": "a1", "contact_ids": ["c1"]},
        captured_at=dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc),
    )
    crm = InteractionCRM()
    publish_cached_event(crm, result, activation_at=ACTIVATED)
    assert len(crm.stamps) == 1
    assert crm.stamps[0]["contact_ids"] == ["c1"]
    assert crm.updates == []


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
