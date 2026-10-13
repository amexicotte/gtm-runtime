"""Idempotent setup tests for isolated Phase 2 monday verification records."""
import datetime as dt
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prepare_phase2_reversible_records",
    ROOT / "scripts" / "prepare_phase2_reversible_records.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CRM:
    def __init__(self):
        self.items = {key: [] for key in module.NAMES}
        self.creates = []
        self.changes = []
        self.next_id = 100
    def _items_named(self, key, name):
        return [row for row in self.items[key] if row["name"] == name]
    def create_item(self, key, name, values):
        self.next_id += 1
        item_id = str(self.next_id)
        self.items[key].append({"id": item_id, "name": name})
        self.creates.append((key, name, values))
        return item_id
    def change_columns(self, key, item_id, values):
        self.changes.append((key, item_id, values))


def test_read_only_inventory_does_not_create_missing_records():
    crm = CRM()
    result = module.prepare(crm, create=False, today=dt.date(2026, 8, 19))
    assert not result["ready"]
    assert result["missing"] == ["account", "contact", "deal", "activity"]
    assert crm.creates == [] and crm.changes == []


def test_create_builds_isolated_relations_in_dependency_order():
    crm = CRM()
    result = module.prepare(crm, create=True, today=dt.date(2026, 8, 19))
    assert result["ready"]
    assert [row[0] for row in crm.creates] == [
        "account", "contact", "deal", "activity"]
    ids = result["record_ids"]
    contact_values = crm.creates[1][2]
    deal_values = crm.creates[2][2]
    activity_values = crm.creates[3][2]
    assert contact_values["Accounts"] == [ids["account"]]
    assert deal_values["Contacts"] == [ids["contact"]]
    assert activity_values["Related item"] == [ids["deal"], ids["contact"]]
    assert result["verification_contact_ids"] == [ids["contact"]]


def test_rerun_reuses_exact_records_without_duplicate_creation():
    crm = CRM()
    first = module.prepare(crm, create=True, today=dt.date(2026, 8, 19))
    crm.creates.clear()
    second = module.prepare(crm, create=True, today=dt.date(2026, 8, 19))
    assert second["record_ids"] == first["record_ids"]
    assert crm.creates == []
    assert [row[0] for row in crm.changes[-2:]] == ["contact", "activity"]


def test_reset_changes_only_exact_disposable_records():
    crm = CRM()
    module.prepare(crm, create=True, today=dt.date(2026, 8, 19))
    crm.changes.clear()
    result = module.prepare(
        crm, create=False, reset=True, today=dt.date(2026, 8, 19))
    assert result["ready"]
    assert ("deal", result["record_ids"]["deal"], {
        "Stage": "New", "Last interaction": "2026-08-18",
        "Last Interaction Source Event": "",
    }) in crm.changes
    activity = [row for row in crm.changes
                if row[0] == "activity" and "Status" in row[2]][0]
    assert activity[1] == result["record_ids"]["activity"]
    assert activity[2]["Status"] == "Open"


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
