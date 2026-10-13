"""Pure acceptance tests for the bounded Phase 2 live-state verifier."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_phase2_live_state", ROOT / "scripts" / "verify_phase2_live_state.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def state(*, live=False):
    ids = {"account": "a1", "contact": "c1", "deal": "d1", "activity": "t1"}
    fixture = {
        "custom_activity_id": "custom", "timeline_id": "timeline",
        "occurred_at": "2026-08-19T04:51:10+00:00",
        "event_key": "email:thread:fixture",
    }
    effects = {
        "field_writes": module._effect(fixture["event_key"], "field_writes", "crm", "fields"),
        "new_activities": module._effect(fixture["event_key"], "new_activities", "activity", "create"),
        "completion": module._effect(fixture["event_key"], "completion", "activity", "t1"),
        "interaction": module._effect(fixture["event_key"], "stamp", "interaction", "d1"),
        "narrative_account": module._effect(fixture["event_key"], "narrative", "account", "a1"),
        "narrative_contact": module._effect(fixture["event_key"], "narrative", "contact", "c1"),
        "narrative_deal": module._effect(fixture["event_key"], "narrative", "deal", "d1"),
    }
    values = {
        "account": {"Review Status": {"text": ""}},
        "contact": {"Review Status": {"text": ""}, "Last Interaction": {"text": ""}},
        "deal": {
            "Stage": {"text": "Discovery" if live else "New"},
            "Review Status": {"text": "Verified" if live else ""},
            "Last interaction": {"text": "2026-08-19" if live else "2026-08-18"},
            "Last Interaction Source Event": {"text": fixture["event_key"] if live else ""},
        },
        "activity": {
            "Status": {"text": "Done" if live else "Open"},
            "Review Status": {"text": "Verified" if live else ""},
            "Completed At": {"text": fixture["occurred_at"] if live else ""},
            "Completion Source Type": {"text": "email" if live else ""},
            "Completion Source Event Key": {"text": fixture["event_key"] if live else ""},
            "Completion Source Timeline ID": {"text": "timeline" if live else ""},
            "Completion Evidence": {"sha256": "hash"},
            "Completion Confidence": {"text": "1" if live else ""},
            "Awaiting Client Reply": {"text": ""},
            "Related item": {"linked_item_ids": ["c1", "d1"]},
        },
    }
    records = {}
    for key in ids:
        marker_ids = [f"u-{key}"] if live and key != "activity" else []
        records[key] = {"id": ids[key], "values": values[key],
                        "update_ids": marker_ids,
                        "marker_update_ids": marker_ids, "updates": []}
    agent = None
    if live:
        details = {
            effects["field_writes"]: {"kind": "field_writes", "records_changed": ["deal:d1"]},
            effects["new_activities"]: {"kind": "new_activities", "created": []},
            effects["completion"]: {"kind": "activity_completion", "activity_id": "t1", "source_timeline_id": "timeline", "confidence": 1},
            effects["interaction"]: {"kind": "last_interaction", "deal_ids": ["d1"], "contact_ids": ["c1"]},
        }
        for key in ("account", "contact", "deal"):
            details[effects[f"narrative_{key}"]] = {
                "kind": "narrative_update", "target_type": key,
                "target_id": ids[key], "update_id": f"u-{key}"}
        agent = {"id": "e1", "status": "Processed", "error_present": False,
                 "error_sha256": "",
                 "effects": details, "reasoning_cache_sha256": "cache",
                 "processing_started_at": ""}
    return {"schema_version": 1, "record_ids": ids, "fixture": fixture,
            "expected_effects": effects, "records": records, "agent_event": agent}


def test_first_live_state_is_accepted_and_retry_is_stable():
    before, after = state(), state(live=True)
    assert module.validate_preflight(before) == []
    assert module.validate_first(before, after) == []
    assert module.validate_retry(after, state(live=True)) == []


def test_rejects_production_target_and_duplicate_retry_update():
    before, after = state(), state(live=True)
    key = after["expected_effects"]["field_writes"]
    after["agent_event"]["effects"][key]["records_changed"] = ["deal:production"]
    assert any("escaped" in error for error in module.validate_first(before, after))
    first, retry = state(live=True), state(live=True)
    retry["records"]["deal"]["update_ids"].append("duplicate")
    assert any("records" in error for error in module.validate_retry(first, retry))


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
