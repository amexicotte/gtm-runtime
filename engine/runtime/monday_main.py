"""CLI entry point for one Monday polling cycle."""
from __future__ import annotations

import argparse
import json
import sys

from .config import config
from .monday_runner import run_monday_poll


def main() -> int:
    parser = argparse.ArgumentParser(description="Harbor Monday meeting engine")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-meeting-errors", action="store_true")
    args = parser.parse_args()
    try:
        if not args.dry_run:
            config.require_business_writes()
            config.require_monday()
        stats = run_monday_poll(dry_run=args.dry_run)
    except Exception as error:
        print(f"monday engine error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(stats.to_dict(), sort_keys=True))
    if args.fail_on_meeting_errors and stats.meeting_errors:
        print(
            f"monday engine completed with {stats.meeting_errors} meeting error(s)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
