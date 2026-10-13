"""Repository CLI for bounded Phase 3 workspace-read operations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .email_main import _load_env_file


def _write(payload: dict | list, path: str = "") -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path:
        Path(path).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    website = sub.add_parser("website-refresh")
    website.add_argument("--output", default="")
    brief = sub.add_parser("operating-brief")
    brief.add_argument("--audit-output", default="")
    docs = sub.add_parser("search-docs")
    docs.add_argument("query")
    docs.add_argument("--account-id", default="")
    docs.add_argument("--deal-id", default="")
    docs.add_argument("--audit-output", default="")
    crm = sub.add_parser("crm-context")
    crm.add_argument("entity_ids", nargs="+")
    crm.add_argument("--audit-output", default="")
    updates = sub.add_parser("recent-updates")
    updates.add_argument("entity_ids", nargs="+")
    updates.add_argument("--limit", type=int, default=5)
    updates.add_argument("--audit-output", default="")
    activities = sub.add_parser("open-activities")
    activities.add_argument("entity_ids", nargs="+")
    activities.add_argument("--audit-output", default="")
    patterns = sub.add_parser("deal-pattern-brief")
    patterns.add_argument("--audit-output", default="")
    audit = sub.add_parser("audit")
    audit.add_argument("--output", default="")
    args = parser.parse_args()

    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    # Import configuration-backed clients only after the ignored local .env is
    # loaded. GitHub Actions injects the same values before process startup.
    from .business_context import (
        ContextError, ContextTools, WebsiteContext, WorkspaceDrive,
        WorkspaceNotion,
    )
    from .monday import Monday
    try:
        if args.command == "website-refresh":
            _write(WebsiteContext().refresh(), args.output)
            return 0
        notion = WorkspaceNotion() if args.command in {
            "operating-brief", "search-docs", "audit"} else None
        drive = WorkspaceDrive() if args.command in {"search-docs", "audit"} else None
        tools = ContextTools(
            crm=Monday() if args.command in {
                "crm-context", "recent-updates", "open-activities",
                "deal-pattern-brief"} else None,
            notion=notion, drive=drive,
        )
        if args.command == "operating-brief":
            result = tools.get_operating_brief()
        elif args.command == "search-docs":
            result = tools.search_business_docs(
                args.query, account_id=args.account_id, deal_id=args.deal_id)
        elif args.command == "crm-context":
            result = tools.get_crm_context(args.entity_ids)
        elif args.command == "recent-updates":
            result = tools.get_recent_updates(args.entity_ids, args.limit)
        elif args.command == "open-activities":
            result = tools.get_open_activities(args.entity_ids)
        elif args.command == "deal-pattern-brief":
            result = tools.get_deal_pattern_brief()
        else:
            notion_status = notion.connectivity()
            drive_status = drive.connectivity()
            brief_result = tools.get_operating_brief()
            drive_result = tools.search_business_docs("Harbor")
            result = {
                "status": "ready", "operating_brief_sha256":
                    brief_result["data"]["content_sha256"],
                "notion": notion_status, "drive": drive_status,
                "business_doc_result_count": len(drive_result["data"]),
                "audit": tools.audit,
            }
        audit_output = getattr(args, "audit_output", "")
        if audit_output:
            _write({"calls": tools.audit}, audit_output)
        _write(result, getattr(args, "output", ""))
        return 0
    except (ContextError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"business context error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
