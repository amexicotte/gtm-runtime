"""Deterministic acceptance review for a sanitized scheduled email shadow report."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
WON_VALUES = {"won", "closed won", "closed_won"}
PROHIBITED_RAW_KEYS = {
    "raw_email", "email_body", "subject", "content", "evidence", "excerpt",
    "participants", "recipients", "sender", "from", "to", "cc", "bcc",
}
ZERO_EFFECTS = (
    "field_write_batches", "records_changed", "activities_created",
    "activities_completed", "narrative_updates_posted",
    "narrative_updates_deduplicated", "contacts_created",
)
ET = ZoneInfo("America/New_York")


def review_shadow_report(payload: dict, *, expected_date: str,
                         event_name: str = "schedule",
                         scheduled_for: str = "",
                         schedule_expression: str = "") -> dict:
    """Return a sanitized, machine-verifiable acceptance decision."""
    errors: list[str] = []
    warnings: list[str] = []
    if event_name != "schedule":
        errors.append("run was not triggered by the GitHub schedule")
    try:
        expected = dt.date.fromisoformat(expected_date)
    except ValueError:
        errors.append("expected observation date is invalid")
        expected = None
    try:
        observed = dt.date.fromisoformat(str(payload.get("observation_date") or ""))
    except ValueError:
        errors.append("report observation date is invalid")
        observed = None
    if expected and observed != expected:
        errors.append("report observation date does not match scheduled ET date")
    try:
        scheduled = dt.datetime.fromisoformat(
            str(scheduled_for).replace("Z", "+00:00"))
        if scheduled.tzinfo is None:
            raise ValueError
        scheduled_et = scheduled.astimezone(ET)
    except ValueError:
        errors.append("intended scheduled time is invalid")
        scheduled_et = None
    if scheduled_et and (
            scheduled_et.hour != 2 or scheduled_et.minute != 0 or
            (expected and scheduled_et.date() != expected)):
        errors.append("intended schedule is not 02:00 ET on the observation date")
    if scheduled_et:
        utc_hour = scheduled_et.astimezone(dt.timezone.utc).hour
        required_expression = f"0 {utc_hour} * * *"
        if schedule_expression != required_expression:
            errors.append("GitHub cron expression does not match the intended ET slot")
    elif not schedule_expression:
        errors.append("GitHub cron expression is missing")

    if payload.get("mode") != "shadow":
        errors.append("report mode is not shadow")
    if payload.get("observation_eligible") is not True:
        errors.append("runtime did not mark the observation eligible")
    for key in (
            "threads_failed", "normalization_failures", "page_saturation_gaps",
            "contact_identity_ambiguity_events", "threads_ingest_only"):
        if _integer(payload, key, errors) != 0:
            errors.append(f"{key} must be zero")
    if payload.get("cap_reached") is not False:
        errors.append("a runtime cap was reached or not reported")
    for key in ZERO_EFFECTS:
        if _integer(payload, key, errors) != 0:
            errors.append(f"shadow report contains nonzero effect counter: {key}")

    threads = payload.get("threads")
    if not isinstance(threads, list):
        errors.append("threads must be a list")
        threads = []
    candidates = _integer(payload, "threads_candidates", errors)
    reasoned = _integer(payload, "threads_reasoned", errors)
    relevant = _integer(payload, "threads_relevant", errors)
    irrelevant = _integer(payload, "threads_irrelevant", errors)
    if candidates != len(threads) or reasoned != len(threads):
        errors.append("every candidate thread must have one reasoning report")
    if relevant + irrelevant != len(threads):
        errors.append("every reasoned thread must have an explicit relevance decision")

    totals = {
        "threads": len(threads), "relevant_threads": 0,
        "proposed_fields": 0, "new_activities": 0,
        "activity_completions": 0, "ambiguous_threads": 0,
        "validation_warning_codes": 0,
    }
    for index, thread in enumerate(threads):
        _review_thread(thread, index=index, errors=errors,
                       warnings=warnings, totals=totals)

    nudges = payload.get("nudges")
    if not isinstance(nudges, dict):
        errors.append("nudge summary is missing")
        nudges = {}
    for key in ("posted", "cleared"):
        if _integer(nudges, key, errors, prefix="nudges.") != 0:
            errors.append(f"shadow nudge effect must be zero: {key}")
    if _integer(nudges, "ambiguous", errors, prefix="nudges."):
        errors.append("nudge scan contains unresolved ambiguity")

    serialized = json.dumps(payload, sort_keys=True)
    if EMAIL_PATTERN.search(serialized):
        errors.append("sanitized report contains an email address")
    exposed_keys = sorted(_prohibited_keys(payload))
    if exposed_keys:
        errors.append(
            "sanitized report exposes prohibited raw-email fields: " +
            ", ".join(exposed_keys))

    return {
        "accepted": not errors,
        "observation_date": str(payload.get("observation_date") or ""),
        "scheduled_for": str(scheduled_for or ""),
        "schedule_expression": str(schedule_expression or ""),
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": totals,
    }


def _review_thread(thread, *, index: int, errors: list[str],
                   warnings: list[str], totals: dict[str, int]) -> None:
    label = f"thread[{index}]"
    if not isinstance(thread, dict):
        errors.append(f"{label} is not an object")
        return
    if not str(thread.get("source_id") or "").strip():
        errors.append(f"{label} is missing source_id")
    try:
        occurred = dt.datetime.fromisoformat(
            str(thread.get("occurred_at") or "").replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            raise ValueError
    except ValueError:
        errors.append(f"{label} has invalid occurred_at")
    if thread.get("relevant") not in (True, False):
        errors.append(f"{label} has no explicit relevance decision")
    if thread.get("relevant") is True:
        totals["relevant_threads"] += 1
        timeline = thread.get("timeline_event")
        if not isinstance(timeline, dict):
            errors.append(f"{label} relevant event has no narrative")
        else:
            headline = str(timeline.get("headline") or "").strip()
            body = str(timeline.get("body") or "").strip()
            if not headline or not body:
                errors.append(f"{label} narrative is incomplete")
            words = re.findall(r"\b[\w'-]+\b", f"{headline} {body}")
            if len(words) > 100:
                errors.append(f"{label} narrative exceeds 100 words")
            elif words and len(words) < 30:
                warnings.append(f"{label} narrative is shorter than the 30-word default")

    ambiguities = thread.get("ambiguities") or []
    if not isinstance(ambiguities, list):
        errors.append(f"{label} ambiguities must be a list")
        ambiguities = []
    if ambiguities:
        totals["ambiguous_threads"] += 1

    fields = thread.get("proposed_fields") or []
    if not isinstance(fields, list):
        errors.append(f"{label} proposed_fields must be a list")
        fields = []
    totals["proposed_fields"] += len(fields)
    for field in fields:
        if not isinstance(field, dict):
            errors.append(f"{label} contains an invalid field proposal")
            continue
        target = str(field.get("target") or "")
        name = str(field.get("field") or "")
        confidence = _number(field.get("confidence"))
        if confidence is None or confidence < 0.8:
            errors.append(f"{label} field proposal is below confidence policy")
        if target == "deal" and "stage" in name.casefold() and \
                str(field.get("value") or "").strip().casefold() in WON_VALUES:
            errors.append(f"{label} proposes prohibited Closed Won stage")
        if ambiguities and target == "deal":
            errors.append(f"{label} proposes Deal fields despite entity ambiguity")

    new_activities = thread.get("new_activities") or []
    completions = thread.get("activity_completions") or []
    if not isinstance(new_activities, list) or not isinstance(completions, list):
        errors.append(f"{label} Activity summaries must be lists")
        new_activities = []
        completions = []
    totals["new_activities"] += len(new_activities)
    totals["activity_completions"] += len(completions)
    for activity in new_activities:
        if (not isinstance(activity, dict) or
                _number(activity.get("confidence")) is None or
                float(activity["confidence"]) < 0.8 or
                not str(activity.get("title") or "").strip()):
            errors.append(f"{label} contains an invalid new Activity")
    for completion in completions:
        if (not isinstance(completion, dict) or
                _number(completion.get("confidence")) is None or
                float(completion["confidence"]) < 0.9 or
                not str(completion.get("activity_id") or "").strip()):
            errors.append(f"{label} contains an invalid Activity completion")

    codes = thread.get("validation_warning_codes") or []
    if not isinstance(codes, list):
        errors.append(f"{label} validation_warning_codes must be a list")
    else:
        totals["validation_warning_codes"] += len(codes)


def _integer(container: dict, key: str, errors: list[str], *, prefix: str = "") -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{prefix}{key} is missing or invalid")
        return -1
    return value


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prohibited_keys(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().casefold()
            if normalized in PROHIBITED_RAW_KEYS:
                found.add(normalized)
            found.update(_prohibited_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_prohibited_keys(nested))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--scheduled-for", required=True)
    parser.add_argument("--schedule-expression", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        review = review_shadow_report(
            payload, expected_date=args.expected_date, event_name=args.event_name,
            scheduled_for=args.scheduled_for,
            schedule_expression=args.schedule_expression)
    except Exception as error:
        review = {
            "accepted": False, "observation_date": "",
            "scheduled_for": str(args.scheduled_for or ""),
            "schedule_expression": str(args.schedule_expression or ""),
            "errors": [f"report review failed: {type(error).__name__}"],
            "warnings": [], "summary": {},
        }
    Path(args.output).write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(review, sort_keys=True))
    return 0 if review["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
