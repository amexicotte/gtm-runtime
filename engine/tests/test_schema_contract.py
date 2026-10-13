"""Token-free monday schema contract and lifecycle migration tests."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.lifecycle_schema import _remove_label, _rename_label  # noqa: E402
from runtime.schema_contract import (  # noqa: E402
    normalized_schema, schema_drift, schema_fingerprint, verify_lifecycle_labels,
)


def snapshot():
    return {"captured_at": "ignored", "boards": [
        {"id": "1", "name": "Deals", "items_count": 10,
         "groups": [{"id": "g", "title": "Pipeline"}], "columns": [{
             "id": "deal_stage", "title": "Stage", "type": "status",
             "target_board_ids": [], "settings": {"labels": [
                 {"label": value, "is_deactivated": False}
                 for value in ("New", "Discovery", "Proposal", "Negotiation",
                               "Paused", "Won", "Lost", "Completed")]}},]},
        {"id": "2", "name": "Accounts", "items_count": 10,
         "groups": [], "columns": [{
             "id": "status", "title": "Type", "type": "status",
             "target_board_ids": [], "settings": {"labels": [
                 {"label": value, "is_deactivated": False}
                 for value in ("Active Client", "Partner", "Vendor", "Prospect",
                               "Completed - Past Client", "AI Investment")]}},]},
    ]}


def test_fingerprint_ignores_capture_time_and_item_counts():
    left = snapshot()
    right = copy.deepcopy(left)
    right["captured_at"] = "new"
    right["boards"][0]["items_count"] = 999
    assert normalized_schema(left) == normalized_schema(right)
    assert schema_fingerprint(left) == schema_fingerprint(right)
    assert schema_drift(left, right) == []


def test_status_label_drift_is_reported_without_model_reasoning():
    live = snapshot()
    live["boards"][0]["columns"][0]["settings"]["labels"].append(
        {"label": "Unreviewed", "is_deactivated": False})
    assert any("Unreviewed" in row for row in schema_drift(snapshot(), live))
    assert any("Unreviewed" in row for row in verify_lifecycle_labels(live))


def test_additive_column_drift_does_not_shift_existing_column_paths():
    live = snapshot()
    live["boards"][0]["columns"].append({
        "id": "mercury_customer_id",
        "title": "Mercury Customer ID",
        "type": "text",
        "target_board_ids": [],
        "settings": {},
    })
    differences = schema_drift(snapshot(), live)
    assert len(differences) == 3
    assert all("added:" in row for row in differences)
    assert all("columns[id='mercury_customer_id']" in row for row in differences)
    assert not any("deal_stage" in row for row in differences)


def test_legacy_completed_label_is_tolerated_but_not_canonical():
    assert verify_lifecycle_labels(snapshot()) == []


def test_lifecycle_label_migration_preserves_account_id_and_removes_completed():
    account, changed = _rename_label(
        [{"id": 0, "label": "Past Client", "color": 155}],
        "Past Client", "Completed - Past Client")
    assert changed and account == [{
        "id": 0, "label": "Completed - Past Client", "color": 155}]
    deal, changed = _remove_label([
        {"id": 1, "label": "Won"}, {"id": 6, "label": "Completed"},
        {"id": 7, "label": "Paused"},
    ], "Completed")
    assert changed and [row["label"] for row in deal] == ["Won", "Paused"]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
