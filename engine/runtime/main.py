"""Entry point for one scheduled run.

  python -m runtime.main            # real run (needs secrets)
  python -m runtime.main --dry-run  # no writes, no LLM/Slack calls that mutate

Run from the repo root so the `matcher` package is importable.
"""
from __future__ import annotations

import argparse
import sys

from .config import config
from .pipeline import run


def main() -> int:
    ap = argparse.ArgumentParser(description="Harbor GTM Engine — one run")
    ap.add_argument("--dry-run", action="store_true",
                    help="log intended actions; perform no writes / LLM calls")
    args = ap.parse_args()

    if not args.dry_run:
        try:
            config.require_all()
        except RuntimeError as e:
            print(f"config error: {e}", file=sys.stderr)
            return 2

    stats = run(dry_run=args.dry_run)
    # non-zero exit if nothing could be loaded is NOT an error; only hard failures raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
