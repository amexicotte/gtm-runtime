#!/usr/bin/env python3
"""Create committed Phase 2 activation only from complete sanitized evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
CONTACT_ID = "10000000011"


def _read(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("rollout evidence must be a JSON object")
    return payload


def _sha(payload: dict) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode()).hexdigest()


def finalize(*, shadow: dict, preflight: dict, first_state: dict,
             first_report: dict, retry_state: dict, retry_report: dict,
             cleanup: dict, immediate_validation: bool = False,
             now: dt.datetime | None = None) -> dict:
    errors = []
    dates = list(shadow.get("accepted_dates") or [])
    today = (now or dt.datetime.now(ET)).astimezone(ET).date()
    try:
        completed = {value for value in dates
                     if dt.date.fromisoformat(value) < today}
    except (TypeError, ValueError):
        completed = set()
        errors.append("accepted observation dates are invalid")
    if (not immediate_validation and
            (not shadow.get("ready_for_live_verification") or len(completed) < 3)):
        errors.append("three accepted past ET observations are required")
    for label, payload, mode in (
            ("preflight", preflight, "preflight"),
            ("first", first_state, "first"),
            ("retry", retry_state, "retry")):
        verification = payload.get("verification") or {}
        if (verification.get("mode") != mode or
                verification.get("accepted") is not True or
                verification.get("errors")):
            errors.append(f"{label} canary verification was not accepted")
        if str((payload.get("record_ids") or {}).get("contact") or "") != CONTACT_ID:
            errors.append(f"{label} canary escaped the exact Contact allowlist")
    if first_report.get("mode") != "live" or first_report.get("threads_failed") != 0:
        errors.append("first live canary report is incomplete")
    if (retry_report.get("mode") != "live" or
            retry_report.get("threads_failed") != 0 or
            retry_report.get("threads_reasoned") != 0):
        errors.append("retry did not complete with zero reasoning calls")
    if cleanup.get("cleaned_up") is not True or cleanup.get("ready") is not False:
        errors.append("verification timeline fixture was not cleaned up")
    if errors:
        raise RuntimeError("; ".join(dict.fromkeys(errors)))
    activated = now or dt.datetime.now(dt.timezone.utc)
    return {
        "schema_version": 1, "live_enabled": True,
        "activated_at": activated.astimezone(dt.timezone.utc).isoformat(),
        "accepted_dates": sorted(completed),
        "validation_mode": ("bounded_live_canary" if immediate_validation
                            else "three_night_shadow_and_canary"),
        "evidence": {
            "verification_contact_id": CONTACT_ID,
            "shadow_state_sha256": _sha(shadow),
            "preflight_sha256": _sha(preflight),
            "first_state_sha256": _sha(first_state),
            "first_report_sha256": _sha(first_report),
            "retry_state_sha256": _sha(retry_state),
            "retry_report_sha256": _sha(retry_report),
            "cleanup_sha256": _sha(cleanup),
            "retry_reasoning_calls": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("shadow-state", "preflight", "first-state", "first-report",
                 "retry-state", "retry-report", "cleanup", "output"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--immediate-validation", action="store_true")
    args = parser.parse_args()
    result = finalize(
        shadow=_read(args.shadow_state), preflight=_read(args.preflight),
        first_state=_read(args.first_state), first_report=_read(args.first_report),
        retry_state=_read(args.retry_state), retry_report=_read(args.retry_report),
        cleanup=_read(args.cleanup),
        immediate_validation=args.immediate_validation)
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"live_enabled": True,
                      "accepted_dates": result["accepted_dates"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
