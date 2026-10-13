"""CLI for the nightly monday E&A listening and nudge pass."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ.setdefault(name, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Harbor monday E&A listening engine")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", action="store_true",
                      help="read and reason, but perform no monday or Slack mutations")
    mode.add_argument("--live", action="store_true",
                      help="apply approval-gated CRM/Activity/nudge effects")
    parser.add_argument("--since", default="",
                        help="timezone-aware lower bound; required if activation is unset")
    parser.add_argument("--nightly-observation", action="store_true",
                        help="mark a complete scheduled shadow pass as observation-eligible")
    parser.add_argument("--report", default="",
                        help="write the sanitized JSON report to this path")
    parser.add_argument("--max-threads", type=int, default=None)
    parser.add_argument("--max-reasoning", type=int, default=None)
    parser.add_argument("--ingest-only", action="store_true",
                        help="read/normalize/match only; never call the reasoning provider")
    parser.add_argument("--verification-contact-ids", default="",
                        help="comma-separated exact monday Contact IDs for a reversible live check")
    args = parser.parse_args()

    # Local development uses the ignored engine/.env. Production injects the
    # same names from GitHub Secrets/Variables. Variable values are never logged.
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    from .config import config
    from .email_runner import run_email_listening

    shadow = not args.live
    try:
        if shadow and args.ingest_only:
            config.require_email_ingest()
        elif shadow:
            config.require_email_shadow()
        else:
            config.require_email_live()
            if (args.verification_contact_ids and
                    not config.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID):
                raise RuntimeError(
                    "EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID is required for the "
                    "bounded live verification fixture")
        business_context = None
        if config.BUSINESS_CONTEXT_ENABLED and not args.ingest_only:
            from .business_context import (
                BusinessContextPrefetcher, ContextTools, WebsiteContext,
                WorkspaceDrive, WorkspaceNotion,
            )
            business_context = BusinessContextPrefetcher(
                ContextTools(notion=WorkspaceNotion(), drive=WorkspaceDrive()),
                website=WebsiteContext())
        stats = run_email_listening(
            shadow=shadow, since=args.since,
            nightly_observation=args.nightly_observation,
            max_threads=args.max_threads, max_reasoning=args.max_reasoning,
            ingest_only=args.ingest_only,
            verification_contact_ids=_contact_ids(
                args.verification_contact_ids) if args.verification_contact_ids else None,
            business_context=business_context)
    except Exception as error:
        failure = _failure_report(shadow=shadow, error=error)
        if args.report:
            Path(args.report).write_text(
                json.dumps(failure, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
        print(f"email engine error: {type(error).__name__}", file=sys.stderr)
        return 1
    payload = stats.to_dict()
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    return 2 if stats.threads_failed else 0


def _contact_ids(value: str) -> set[str]:
    values = {item.strip() for item in str(value).split(",") if item.strip()}
    if not values or any(not item.isdigit() for item in values):
        raise ValueError("verification Contact IDs must be comma-separated numeric IDs")
    return values


def _failure_report(*, shadow: bool, error: Exception,
                    now: dt.datetime | None = None) -> dict:
    """Return a source-content-free fatal report for private workflow artifacts."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ET)
    return {
        "mode": "shadow" if shadow else "live",
        "observation_date": now.date().isoformat(),
        "observation_eligible": False,
        "fatal_error_code": type(error).__name__,
        "threads_failed": 1,
        "normalization_failures": 0,
        "page_saturation_gaps": 0,
        "contact_identity_ambiguity_events": 0,
        "threads_ingest_only": 0,
        "cap_reached": False,
        "field_write_batches": 0,
        "records_changed": 0,
        "activities_created": 0,
        "activities_completed": 0,
        "narrative_updates_posted": 0,
        "narrative_updates_deduplicated": 0,
        "threads_candidates": 0,
        "threads_reasoned": 0,
        "threads_relevant": 0,
        "threads_irrelevant": 0,
        "threads": [],
        "nudges": {"posted": 0, "cleared": 0, "ambiguous": 0},
        "warnings": ["fatal runtime failure; inspect error code and server logs"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
