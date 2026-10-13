"""Bounded monday E&A verification-fixture lifecycle tests."""
import datetime as dt
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prepare_phase2_verification_timeline",
    ROOT / "scripts" / "prepare_phase2_verification_timeline.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CRM:
    def __init__(self):
        self.custom = []
        self.timeline = []
        self.mutations = []
    def _items_named(self, key, name):
        assert key == "contact" and name == module.CONTACT_NAME
        return [{"id": "contact-1", "name": name}]
    def email_timeline_items(self, contact_id):
        assert contact_id == "contact-1"
        return {"cursor": "", "items": list(self.timeline)}
    def _post(self, query, variables=None):
        if "query{custom_activity" in query:
            return {"custom_activity": list(self.custom)}
        self.mutations.append((query, variables))
        if "create_custom_activity" in query:
            row = {"id": "activity-1", "name": module.CUSTOM_ACTIVITY_NAME,
                   "type": "custom"}
            self.custom.append(row)
            return {"create_custom_activity": {"id": row["id"]}}
        if "create_timeline_item" in query:
            row = {"id": "timeline-1", "title": variables["title"],
                   "type": "activity", "custom_activity_id": variables["activity"]}
            self.timeline.append(row)
            return {"create_timeline_item": {"id": row["id"]}}
        if "timeline_item(id:" in query:
            return {"timeline_item": dict(self.timeline[0])}
        if "delete_timeline_item" in query:
            self.timeline.clear(); return {"delete_timeline_item": {"id": variables["id"]}}
        if "delete_custom_activity" in query:
            self.custom.clear(); return {"delete_custom_activity": {
                "id": variables["id"], "name": module.CUSTOM_ACTIVITY_NAME}}
        raise AssertionError(query)


def test_read_only_inventory_reports_fixture_missing():
    result = module.prepare(CRM(), create=False)
    assert not result["ready"] and result["custom_activity_id"] == ""


def test_create_is_idempotent_and_uses_fixed_synthetic_payload():
    crm = CRM()
    first = module.prepare(
        crm, create=True, now=dt.datetime(2026, 8, 19, 5, tzinfo=dt.timezone.utc))
    mutation_count = len(crm.mutations)
    second = module.prepare(crm, create=True)
    assert first["ready"] and second["ready"]
    assert first["custom_activity_id"] == "activity-1"
    assert first["timeline_id"] == "timeline-1"
    assert len(crm.mutations) == mutation_count + 1  # direct readback only
    create_vars = next(values for query, values in crm.mutations
                       if "create_timeline_item" in query)
    assert "example.invalid" in create_vars["content"]
    assert "production record" in create_vars["content"]


def test_cleanup_deletes_timeline_before_custom_activity():
    crm = CRM()
    module.prepare(crm, create=True)
    crm.mutations.clear()
    result = module.prepare(crm, create=False, cleanup=True)
    assert result["cleaned_up"] and not result["ready"]
    assert "delete_timeline_item" in crm.mutations[0][0]
    assert "delete_custom_activity" in crm.mutations[1][0]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
