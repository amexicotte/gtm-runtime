"""CLI for the approved Account/Deal lifecycle schema migration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .discovery import load_env_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan/apply CRM lifecycle labels")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    try:
        from .config import config
        from .lifecycle_schema import migrate_lifecycle_schema
        from .monday import Monday
        if args.apply:
            config.require_business_writes()
        result = migrate_lifecycle_schema(Monday(), apply=args.apply)
    except Exception as error:
        print(f"monday lifecycle schema error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
