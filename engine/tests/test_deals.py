"""Deterministic Deal creation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.deals import ensure_deal_from_result  # noqa: E402


def answered(value, excerpt=None, confidence=.99):
    return {"status": "ANSWERED", "value": value, "evidence": {
        "excerpt": excerpt or str(value), "confidence": confidence,
        "source_id": "m1", "occurred_at": "2026-08-17T00:00:00+00:00",
    }}


class FakeCRM:
    def __init__(self, *, account_type="Prospect", deals=None):
        self.created, self.links, self.projects = [], [], []
        self._account_type = account_type
        self._deals = deals or []
    def active_deals_for_account(self, account): return []
    def deals_for_account(self, account): return self._deals
    def account_type(self, account): return self._account_type
    def item_name(self, account): return "Lumen"
    def create_deal(self, **kwargs): self.created.append(kwargs); return "d1"
    def link_meeting_deal(self, meeting, deal): self.links.append((meeting, deal))
    def ensure_client_project(self, **kwargs): self.projects.append(kwargs); return "p1"


def test_first_sales_call_creates_discovery_deal_and_updates_context():
    crm = FakeCRM()
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": answered("Sales")}}
    assert ensure_deal_from_result(crm, ctx, result) == "d1"
    assert crm.created[0]["stage"] == "Discovery"
    assert "source" not in crm.created[0]
    assert crm.created[0]["contact_ids"] == ["c1"]
    assert ctx["deal_id"] == "d1" and crm.links == [("m1", "d1")]


def test_client_call_for_prospect_does_not_invent_won_deal():
    crm = FakeCRM()
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": answered("Client")}}
    assert ensure_deal_from_result(crm, ctx, result) is None
    assert crm.created == [] and crm.links == []


def test_first_client_call_for_active_client_does_not_create_won():
    crm = FakeCRM(account_type="Active Client")
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": answered("Client")}}
    assert ensure_deal_from_result(crm, ctx, result) is None
    assert crm.created == [] and crm.links == [] and crm.projects == []


def test_client_call_reuses_single_won_deal_and_ensures_project():
    crm = FakeCRM(
        account_type="Active Client",
        deals=[{"id": "won1", "name": "Lumen", "stage": "Won"}],
    )
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": answered("Project")}}
    assert ensure_deal_from_result(crm, ctx, result) == "won1"
    assert crm.created == []
    assert crm.links == [("m1", "won1")]
    assert crm.projects[0]["deal_id"] == "won1"


def test_active_client_reuses_won_even_when_model_calls_meeting_intro():
    class ExistingClient(FakeCRM):
        def __init__(self):
            super().__init__(
                account_type="Active Client",
                deals=[{"id": "won1", "name": "Quillr", "stage": "Won"}],
            )
        def preferred_deal_for_account(self, account): return "won1"

    crm = ExistingClient()
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": "Intro"}}
    assert ensure_deal_from_result(crm, ctx, result) == "won1"
    assert crm.created == []
    assert crm.links == [("m1", "won1")]
    assert crm.projects[0]["deal_id"] == "won1"


def test_owner_only_existing_client_project_is_flagged_without_stranding_deal():
    class RestrictedCRM(FakeCRM):
        def __init__(self):
            super().__init__(account_type="Active Client", deals=[
                {"id": "won1", "name": "Lumen", "stage": "Won"}])
            self.flagged = []

        def ensure_client_project(self, **kwargs):
            raise RuntimeError("monday GraphQL: USER_UNAUTHORIZED")

        def flag_client_project_needed(self, deal_id):
            self.flagged.append(deal_id)

    crm = RestrictedCRM()
    ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
           "contact_ids": ["c1"], "deal_id": None}
    result = {"meeting": {"call_type": answered("Client")}}
    assert ensure_deal_from_result(crm, ctx, result) == "won1"
    assert crm.created == []
    assert crm.flagged == ["won1"]


def test_partial_or_weak_call_type_cannot_create_deal():
    for call_type in (
            {"status": "PARTIAL", "value": "Sales"},
            answered("Sales", confidence=.2),
            {"status": "ANSWERED", "value": "Sales"}):
        crm = FakeCRM()
        ctx = {"meeting_id": "m1", "prospect_account_id": "a1",
               "contact_ids": ["c1"], "deal_id": None}
        assert ensure_deal_from_result(
            crm, ctx, {"meeting": {"call_type": call_type}}) is None
        assert crm.created == []


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
