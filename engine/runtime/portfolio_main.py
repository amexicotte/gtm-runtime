"""CLI for the weekly advisory Closed Won/Lost pattern brief."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .email_main import _load_env_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    from .config import config
    from .monday import Monday
    from .portfolio_patterns import PortfolioBriefError, run_portfolio_patterns
    try:
        if args.live:
            config.require_portfolio_live()
        else:
            config.require_portfolio_reasoning()
        report = run_portfolio_patterns(crm=Monday(), live=args.live)
    except Exception as error:
        detail = f": {error}" if isinstance(error, PortfolioBriefError) else ""
        print(
            f"portfolio pattern error: {type(error).__name__}{detail}",
            file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        Path(args.report).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
