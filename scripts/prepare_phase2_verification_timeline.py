#!/usr/bin/env python3
"""Create, inspect, or clean up the bounded Phase 2 E&A verification fixture."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from runtime.monday import Monday  # noqa: E402
from runtime.email_events import VERIFICATION_METADATA_PREFIX  # noqa: E402


CONTACT_NAME = "[TEST - SAFE TO ARCHIVE] Business Listening Contact"
CUSTOM_ACTIVITY_NAME = "[TEST] Business Listening Verification Email"
TITLE = "Phase 2 business-listening verification"
METADATA = json.dumps({
    "from": "alex@harboradvisors.example",
    "to": ["business-listening-verification@example.invalid"],
    "cc": [], "bcc": [],
}, separators=(",", ":"))
SUMMARY = "Synthetic bounded email for isolated Phase 2 verification"
CONTENT = VERIFICATION_METADATA_PREFIX + METADATA + "\n" + (
    "The Business Listening Activity is complete.\n"
    "The verification Deal is now in Discovery. This is an isolated "
    "reversible test and must never change a production record."
)


def prepare(crm, *, create: bool, cleanup: bool = False,
            now: dt.datetime | None = None) -> dict:
    contacts = crm._items_named("contact", CONTACT_NAME)
    if len(contacts) != 1:
        raise RuntimeError("expected exactly one reversible verification Contact")
    contact_id = str(contacts[0]["id"])
    activities = crm._post("query{custom_activity{id name type}}")[
        "custom_activity"]
    matching_activities = [row for row in activities
                           if str(row.get("name") or "") == CUSTOM_ACTIVITY_NAME]
    if len(matching_activities) > 1:
        raise RuntimeError("duplicate verification custom activities")
    custom_activity_id = (
        str(matching_activities[0]["id"]) if matching_activities else "")
    timeline_rows = crm.email_timeline_items(contact_id).get("items") or []
    matching_timeline = [row for row in timeline_rows
                         if custom_activity_id and
                         str(row.get("custom_activity_id") or "") == custom_activity_id and
                         str(row.get("title") or "") == TITLE]
    if len(matching_timeline) > 1:
        raise RuntimeError("duplicate verification timeline fixtures")
    timeline_id = str(matching_timeline[0]["id"]) if matching_timeline else ""

    if cleanup:
        if timeline_id:
            crm._post(
                "mutation($id:String!){delete_timeline_item(id:$id){id}}",
                {"id": timeline_id})
        if custom_activity_id:
            crm._post(
                "mutation($id:String!){delete_custom_activity(id:$id){id name}}",
                {"id": custom_activity_id})
        return {
            "ready": False, "cleaned_up": True, "contact_id": contact_id,
            "custom_activity_id": "", "timeline_id": "",
        }

    if not create:
        return {
            "ready": bool(custom_activity_id and timeline_id),
            "cleaned_up": False, "contact_id": contact_id,
            "custom_activity_id": custom_activity_id,
            "timeline_id": timeline_id,
        }
    if not custom_activity_id:
        custom_activity_id = str(crm._post(
            "mutation($name:String!){create_custom_activity("
            "color:SLATE_BLUE,icon_id:PAPERPLANE,name:$name){id}}",
            {"name": CUSTOM_ACTIVITY_NAME},
        )["create_custom_activity"]["id"])
    if not timeline_id:
        occurred = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
            dt.timezone.utc).isoformat()
        timeline_id = str(crm._post(
            "mutation($item:ID!,$activity:String!,$title:String!,"
            "$summary:String!,$content:String!,$timestamp:ISO8601DateTime!){"
            "create_timeline_item(item_id:$item,custom_activity_id:$activity,"
            "title:$title,summary:$summary,content:$content,timestamp:$timestamp){id}}",
            {
                "item": contact_id, "activity": custom_activity_id,
                "title": TITLE, "summary": SUMMARY, "content": CONTENT,
                "timestamp": occurred,
            },
        )["create_timeline_item"]["id"])
    direct = crm._post(
        "query($id:ID!){timeline_item(id:$id){id title type custom_activity_id}}",
        {"id": timeline_id})["timeline_item"]
    if (str(direct.get("custom_activity_id") or "") != custom_activity_id or
            str(direct.get("title") or "") != TITLE):
        raise RuntimeError("verification timeline fixture failed direct readback")
    return {
        "ready": True, "cleaned_up": False, "contact_id": contact_id,
        "custom_activity_id": custom_activity_id, "timeline_id": timeline_id,
        "normal_schedules_ignore_fixture": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    result = prepare(Monday(), create=args.create, cleanup=args.cleanup)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ready") or result.get("cleaned_up") else 2


if __name__ == "__main__":
    raise SystemExit(main())
