#!/usr/bin/env python3
"""Create or inspect the isolated monday records used for Phase 2 live verification."""
from __future__ import annotations

import argparse
import datetime as dt
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
TEST_EMAIL = "business-listening-verification@example.invalid"
MARKER = (
    "Temporary isolated Phase 2 business-listening verification record. "
    "Created by the repository setup script; safe to archive after rollout."
)


def prepare(crm, *, create: bool, reset: bool = False,
            today: dt.date | None = None) -> dict:
    """Return exact test-record IDs, creating and linking missing records."""
    today = today or dt.date.today()
    ids = {key: _exact_id(crm, key, name) for key, name in NAMES.items()}
    missing = [key for key, value in ids.items() if not value]
    if missing and not create:
        return {"ready": False, "missing": missing, "record_ids": ids}

    if not ids["account"]:
        ids["account"] = crm.create_item("account", NAMES["account"], {
            "Type": "Prospect", "Notes": MARKER,
        })
    if not ids["contact"]:
        ids["contact"] = crm.create_item("contact", NAMES["contact"], {
            "Email": TEST_EMAIL, "Accounts": [ids["account"]],
            "Type": "Lead", "Notes": MARKER,
        })
    if not ids["deal"]:
        ids["deal"] = crm.create_item("deal", NAMES["deal"], {
            "Account": [ids["account"]], "Contacts": [ids["contact"]],
            "Stage": "New", "Last interaction": today.isoformat(),
            "Notes": MARKER,
        })
    crm.change_columns("contact", ids["contact"], {
        "Accounts": [ids["account"]], "Deals": [ids["deal"]],
    })
    if not ids["activity"]:
        ids["activity"] = crm.create_item("activity", NAMES["activity"], {
            "Status": "Open", "Activity Type": "Task", "Task Type": "Do it",
            "Due Date": (today + dt.timedelta(days=1)).isoformat(),
            "Related item": [ids["deal"], ids["contact"]], "Notes": MARKER,
        })
    crm.change_columns("activity", ids["activity"], {
        "Related item": [ids["deal"], ids["contact"]],
    })
    if reset:
        # Reset only the four exact disposable records. This is deliberately
        # unavailable through a broad board query or name prefix.
        crm.change_columns("deal", ids["deal"], {
            "Stage": "New",
            "Last interaction": (today - dt.timedelta(days=1)).isoformat(),
            "Last Interaction Source Event": "",
        })
        crm.change_columns("contact", ids["contact"], {
            "Last Interaction": "",
        })
        crm.change_columns("activity", ids["activity"], {
            "Status": "Open", "Completed At": "",
            "Completion Source Type": "", "Completion Source Event Key": "",
            "Completion Source Timeline ID": "", "Completion Evidence": "",
            "Completion Confidence": "", "Awaiting Client Reply": False,
            "Related item": [ids["deal"], ids["contact"]],
        })
    return {
        "ready": True, "missing": [], "record_ids": ids,
        "verification_contact_ids": [ids["contact"]],
        "rollback": "Archive the four exact [TEST - SAFE TO ARCHIVE] items by ID.",
    }


def _exact_id(crm, board_key: str, name: str) -> str:
    matches = crm._items_named(board_key, name)
    if len(matches) > 1:
        raise RuntimeError(f"duplicate reversible {board_key} records")
    return str(matches[0]["id"]) if matches else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create", action="store_true",
        help="create missing records; without this flag the command is read-only")
    parser.add_argument(
        "--reset", action="store_true",
        help="reset only the exact disposable records before first live verification")
    args = parser.parse_args()
    result = prepare(Monday(), create=args.create, reset=args.reset)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
