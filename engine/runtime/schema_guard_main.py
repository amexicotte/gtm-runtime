"""Zero-token production preflight for checked-in monday schema drift."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .discovery import DiscoveryError, load_env_file, monday_schema
from .schema_contract import (
    DEFAULT_SNAPSHOT, load_snapshot, schema_drift, schema_fingerprint,
    verify_lifecycle_labels,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify live monday schema")
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    try:
        expected = load_snapshot(args.snapshot)
        live = monday_schema()
    except (DiscoveryError, OSError, ValueError) as error:
        print(json.dumps({
            "valid": False, "error": type(error).__name__,
            "message": str(error),
        }, sort_keys=True), file=sys.stderr)
        return 1
    differences = schema_drift(expected, live)
    lifecycle_problems = verify_lifecycle_labels(live)
    result = {
        "valid": not differences and not lifecycle_problems,
        "expected_fingerprint": schema_fingerprint(expected),
        "live_fingerprint": schema_fingerprint(live),
        "drift": differences,
        "lifecycle_problems": lifecycle_problems,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
