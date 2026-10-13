"""Idempotently install monday columns owned by the runtime."""
from __future__ import annotations

import json
import sys

from .monday import Monday


def main() -> int:
    try:
        result = Monday().prepare_runtime_schema()
    except Exception as error:
        print(f"monday schema setup error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
