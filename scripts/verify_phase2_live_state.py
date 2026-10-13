#!/usr/bin/env python3
"""Snapshot and verify the bounded Phase 2 monday live-write canary."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from runtime.monday import Monday  # noqa: E402


NAMES = {
    "account": "[TEST - SAFE TO ARCHIVE] Business Listening Account",
    "contact": "[TEST - SAFE TO ARCHIVE] Business Listening Contact",
    "deal": "[TEST - SAFE TO ARCHIVE] Business Listening Deal",
    "activity": "[TEST - SAFE TO ARCHIVE] Business Listening Activity",
}
CUSTOM_ACTIVITY_NAME = "[TEST] Business Listening Verification Email"
TIMELINE_TITLE = "Phase 2 business-listening verification"
COLUMNS = {
    "account": ["Review Status"],
    "contact": ["Review Status", "Last Interaction"],
    "deal": [
        "Stage", "Review Status", "Last interaction",
        "Last Interaction Source Event",
    ],
    "activity": [
        "Status", "Review Status", "Completed At", "Completion Source Type",
        "Completion Source Event Key", "Completion Source Timeline ID",
        "Completion Evidence", "Completion Confidence",
        "Awaiting Client Reply", "Related item",
    ],
}


def _sha(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _effect(event_key: str, kind: str, target_type: str, target_id: str) -> str:
    return _sha(f"{event_key}:{kind}:{target_type}:{target_id}")


def _exact_ids(crm) -> dict[str, str]:
    ids = {}
    for board_key, name in NAMES.items():
        rows = crm._items_named(board_key, name)
        if len(rows) != 1:
            raise RuntimeError(f"expected exactly one reversible {board_key} record")
        ids[board_key] = str(rows[0]["id"])
    return ids


def _fixture(crm, contact_id: str) -> dict[str, str]:
    activities = crm._post("query{custom_activity{id name type}}")[
        "custom_activity"]
    matches = [row for row in activities
               if str(row.get("name") or "") == CUSTOM_ACTIVITY_NAME]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one verification custom activity")
    custom_id = str(matches[0]["id"])
    rows = crm.email_timeline_items(contact_id).get("items") or []
    matches = [row for row in rows
               if str(row.get("custom_activity_id") or "") == custom_id
               and str(row.get("title") or "") == TIMELINE_TITLE]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one verification timeline item")
    row = matches[0]
    occurred_at = dt.datetime.fromisoformat(
        str(row["created_at"]).replace("Z", "+00:00"))
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=dt.timezone.utc)
    timeline_id = str(row["id"])
    source_id = "thread:" + _sha(timeline_id)[:40]
    return {
        "custom_activity_id": custom_id,
        "timeline_id": timeline_id,
        "occurred_at": occurred_at.astimezone(dt.timezone.utc).isoformat(),
        "event_key": f"email:{source_id}",
    }


def _record(crm, board_key: str, item_id: str, marker: str) -> dict:
    columns = {title: crm.column(board_key, title)["id"]
               for title in COLUMNS[board_key]}
    query = (
        "query($ids:[ID!],$columns:[String!]){items(ids:$ids){id name "
        "updates(limit:100){id body created_at} column_values(ids:$columns){"
        "id text value ... on BoardRelationValue{linked_item_ids}}}}")
    rows = crm._post(query, {
        "ids": [item_id], "columns": list(columns.values()),
    }).get("items") or []
    if len(rows) != 1 or str(rows[0].get("name") or "") != NAMES[board_key]:
        raise RuntimeError(f"reversible {board_key} identity changed")
    by_id = {row["id"]: row for row in rows[0].get("column_values") or []}
    values = {}
    for title, column_id in columns.items():
        row = by_id.get(column_id, {})
        if title == "Completion Evidence":
            values[title] = {"sha256": _sha(row.get("text") or "")}
        elif title == "Related item":
            values[title] = {
                "linked_item_ids": sorted(
                    str(value) for value in row.get("linked_item_ids") or [])}
        else:
            values[title] = {"text": str(row.get("text") or "")}
    updates = [{
        "id": str(row.get("id") or ""),
        "body_sha256": _sha(row.get("body") or ""),
        "is_expected_marker": marker in str(row.get("body") or ""),
    } for row in rows[0].get("updates") or []]
    return {
        "id": item_id, "values": values,
        "update_ids": sorted(row["id"] for row in updates),
        "marker_update_ids": sorted(
            row["id"] for row in updates if row["is_expected_marker"]),
        "updates": sorted(updates, key=lambda row: row["id"]),
    }


def snapshot(crm) -> dict:
    ids = _exact_ids(crm)
    fixture = _fixture(crm, ids["contact"])
    event_key = fixture["event_key"]
    markers = {
        key: _effect(event_key, "narrative", key, ids[key])
        for key in ("account", "contact", "deal")}
    records = {
        key: _record(crm, key, item_id, markers.get(key, "no-marker"))
        for key, item_id in ids.items()}
    event_rows = crm._items_named("agent_event", event_key)
    if len(event_rows) > 1:
        raise RuntimeError("duplicate verification Agent Events")
    agent_event = None
    if event_rows:
        item_id = str(event_rows[0]["id"])
        state = crm._agent_event_state(item_id)
        cache = state.pop("reasoning_cache", {})
        error = str(state.pop("error", "") or "")
        agent_event = {
            "id": item_id, **state,
            "error_present": bool(error),
            "error_sha256": _sha(error) if error else "",
            "reasoning_cache_sha256": _sha(json.dumps(
                cache, sort_keys=True, separators=(",", ":"))),
        }
    expected_effects = {
        "field_writes": _effect(event_key, "field_writes", "crm", "fields"),
        "new_activities": _effect(
            event_key, "new_activities", "activity", "create"),
        "completion": _effect(
            event_key, "completion", "activity", ids["activity"]),
        "interaction": _effect(
            event_key, "stamp", "interaction", ids["deal"]),
        **{f"narrative_{key}": value for key, value in markers.items()},
    }
    return {
        "schema_version": 1,
        "record_ids": ids,
        "fixture": fixture,
        "expected_effects": expected_effects,
        "records": records,
        "agent_event": agent_event,
    }


def _text(state: dict, board: str, title: str) -> str:
    return state["records"][board]["values"][title]["text"]


def validate_preflight(state: dict) -> list[str]:
    errors = []
    if state.get("agent_event") is not None:
        errors.append("verification Agent Event already exists")
    if _text(state, "deal", "Stage").casefold() != "new":
        errors.append("verification Deal is not in New")
    if _text(state, "activity", "Status").casefold() != "open":
        errors.append("verification Activity is not Open")
    try:
        occurred = dt.datetime.fromisoformat(state["fixture"]["occurred_at"])
        deal_date = dt.date.fromisoformat(
            _text(state, "deal", "Last interaction")[:10])
        interaction_is_old = deal_date < occurred.date()
    except (TypeError, ValueError):
        interaction_is_old = False
    if not interaction_is_old:
        errors.append("verification Deal interaction date is not older than fixture")
    for key in ("account", "contact", "deal"):
        if state["records"][key]["marker_update_ids"]:
            errors.append(f"verification {key} already has event narrative marker")
    return errors


def validate_first(before: dict, after: dict) -> list[str]:
    errors = validate_preflight(before)
    if before["record_ids"] != after["record_ids"] or before["fixture"] != after["fixture"]:
        errors.append("verification boundary changed during live run")
    agent = after.get("agent_event") or {}
    if str(agent.get("status") or "").casefold() != "processed":
        errors.append("Agent Event is not Processed")
    if agent.get("error_present"):
        errors.append("Agent Event contains an error or ambiguity")
    effects = agent.get("effects") if isinstance(agent.get("effects"), dict) else {}
    expected = after["expected_effects"]
    if set(effects) != set(expected.values()):
        errors.append("Agent Event effect set is incomplete or unexpected")
    ids = after["record_ids"]
    field = effects.get(expected["field_writes"], {})
    if field.get("kind") != "field_writes" or field.get("records_changed") != [
            f"deal:{ids['deal']}"]:
        errors.append("field-write effect escaped the isolated Deal")
    created = effects.get(expected["new_activities"], {})
    if created.get("kind") != "new_activities" or created.get("created") != []:
        errors.append("live canary unexpectedly created an Activity")
    completion = effects.get(expected["completion"], {})
    try:
        completion_confidence = float(completion.get("confidence") or 0)
    except (TypeError, ValueError):
        completion_confidence = 0
    if (completion.get("kind") != "activity_completion" or
            str(completion.get("activity_id") or "") != ids["activity"] or
            str(completion.get("source_timeline_id") or "") !=
            after["fixture"]["timeline_id"] or
            completion_confidence < 0.9):
        errors.append("Activity completion effect is incomplete or mis-targeted")
    interaction = effects.get(expected["interaction"], {})
    if (interaction.get("kind") != "last_interaction" or
            interaction.get("deal_ids") != [ids["deal"]] or
            interaction.get("contact_ids") != [ids["contact"]]):
        errors.append("interaction effect escaped the isolated records")
    for key in ("account", "contact", "deal"):
        detail = effects.get(expected[f"narrative_{key}"], {})
        if (detail.get("kind") != "narrative_update" or
                detail.get("target_type") != key or
                str(detail.get("target_id") or "") != ids[key]):
            errors.append(f"{key} narrative effect is missing or mis-targeted")
        before_markers = before["records"][key]["marker_update_ids"]
        after_markers = after["records"][key]["marker_update_ids"]
        if before_markers or len(after_markers) != 1:
            errors.append(f"{key} did not receive exactly one narrative Update")
    event_key = after["fixture"]["event_key"]
    timeline_id = after["fixture"]["timeline_id"]
    occurred_at = after["fixture"]["occurred_at"]
    if _text(after, "deal", "Stage").casefold() != "discovery":
        errors.append("verification Deal Stage is not Discovery")
    if _text(after, "deal", "Last Interaction Source Event") != event_key:
        errors.append("Deal interaction provenance does not match the event")
    if _text(after, "activity", "Status").casefold() != "done":
        errors.append("verification Activity is not Done")
    if _text(after, "activity", "Completion Source Type").casefold() != "email":
        errors.append("Activity completion source is not email")
    if _text(after, "activity", "Completion Source Event Key") != event_key:
        errors.append("Activity completion event key is incorrect")
    if _text(after, "activity", "Completion Source Timeline ID") != timeline_id:
        errors.append("Activity completion timeline ID is incorrect")
    if _text(after, "activity", "Completed At") != occurred_at:
        errors.append("Activity completion time is not the source event time")
    try:
        confidence = float(_text(after, "activity", "Completion Confidence"))
    except ValueError:
        confidence = 0
    if confidence < 0.9:
        errors.append("Activity completion confidence is below 0.90")
    if _text(after, "activity", "Awaiting Client Reply").casefold() not in {
            "", "no", "false"}:
        errors.append("Activity incorrectly awaits a client reply")
    if after["records"]["activity"]["values"]["Related item"][
            "linked_item_ids"] != sorted([ids["deal"], ids["contact"]]):
        errors.append("Activity relationship boundary changed")
    return list(dict.fromkeys(errors))


def validate_retry(first: dict, retry: dict) -> list[str]:
    errors = []
    for key in ("record_ids", "fixture", "expected_effects", "records", "agent_event"):
        if first.get(key) != retry.get(key):
            errors.append(f"retry changed {key}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("snapshot", "preflight", "first", "retry"))
    parser.add_argument("--before", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    current = snapshot(Monday())
    errors = []
    if args.mode == "preflight":
        errors = validate_preflight(current)
    elif args.mode in {"first", "retry"}:
        if not args.before:
            raise SystemExit("--before is required for first/retry verification")
        prior = json.loads(Path(args.before).read_text(encoding="utf-8"))
        errors = (validate_first(prior, current) if args.mode == "first"
                  else validate_retry(prior, current))
    current["verification"] = {"mode": args.mode, "accepted": not errors,
                               "errors": errors}
    Path(args.output).write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(current["verification"], indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
