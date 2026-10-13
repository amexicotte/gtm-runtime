"""CLI for previewing or installing the native monday commander dashboard."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .commander_dashboard import CommanderDashboardInstaller
from .discovery import load_env_file
from .monday import Monday


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="perform additive monday mutations; default is read-only")
    parser.add_argument("--annual-goal", type=float, default=1_500_000)
    parser.add_argument("--year", type=int, default=dt.datetime.now(
        ZoneInfo("America/New_York")).year)
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--state-file", type=Path,
                        default=root / "docs" / "commander-dashboard-state.json")
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    try:
        kill_switch = os.environ.get(
            "BUSINESS_LISTENING_WRITE_KILL_SWITCH", "false").strip().casefold()
        if kill_switch not in {"", "0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise RuntimeError("BUSINESS_LISTENING_WRITE_KILL_SWITCH must be true or false")
        if args.apply and kill_switch in {"1", "true", "yes", "on"}:
            raise RuntimeError("business-listening write kill switch is active")
        installer = CommanderDashboardInstaller(
            Monday(), year=args.year, annual_goal=args.annual_goal,
            state_file=args.state_file)
        result = installer.apply() if args.apply else installer.preview()
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
