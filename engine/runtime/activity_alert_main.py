"""Narrow CLI for the scheduled monday Activity -> Slack accountability pass."""
from __future__ import annotations

import json
import sys

from .activity_alerts import sync_activity_alerts
from .config import config
from .monday import Monday
from .slack import Slack


def main() -> int:
    try:
        config.require_business_writes()
        if not config.MONDAY_API_TOKEN:
            raise RuntimeError("MONDAY_API_TOKEN is required")
        if not config.SLACK_BOT_TOKEN:
            raise RuntimeError("SLACK_BOT_TOKEN is required")
        stats = sync_activity_alerts(
            Slack(), Monday(), channel=config.DEAL_ALERTS_CHANNEL,
            dry_run=False)
    except Exception as error:
        print(f"Activity alert error: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(stats.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
