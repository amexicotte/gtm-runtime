#!/usr/bin/env python3
"""Collect and verify scheduled Phase 2 shadow artifacts through the GitHub CLI."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import tempfile
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
WORKFLOW = "email-listening-nightly.yml"
ROLLOUT_NOT_BEFORE = dt.datetime(
    2026, 8, 19, 0, 16, 2, tzinfo=ET).astimezone(dt.timezone.utc)


def assess_run(run: dict, artifact_dir: Path) -> dict:
    """Assess one downloaded artifact without reading any raw email source."""
    run_id = int(run.get("databaseId") or 0)
    errors = []
    try:
        created = dt.datetime.fromisoformat(
            str(run.get("createdAt") or "").replace("Z", "+00:00")).astimezone(ET)
    except ValueError:
        created = None
        errors.append("invalid GitHub run timestamp")
    if run.get("event") != "schedule":
        errors.append("run was not scheduled")
    if run.get("status") != "completed":
        errors.append("run is not complete")

    review_path = artifact_dir / "email-shadow-review.json"
    report_path = artifact_dir / "email-shadow-report.json"
    gate_path = artifact_dir / "schedule-gate.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        review = {}
        errors.append("sanitized acceptance review is missing or invalid")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        report = {}
        errors.append("sanitized shadow report is missing or invalid")
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        gate = {}
        errors.append("schedule gate evidence is missing or invalid")

    if gate.get("eligible") is not True:
        errors.append("schedule gate did not accept the intended ET slot")
    scheduled_for = str(review.get("scheduled_for") or "")
    if scheduled_for != str(gate.get("scheduled_for") or ""):
        errors.append("review and schedule gate intended times differ")
    if str(review.get("schedule_expression") or "") != str(
            gate.get("schedule_expression") or ""):
        errors.append("review and schedule gate cron expressions differ")
    try:
        scheduled = dt.datetime.fromisoformat(
            scheduled_for.replace("Z", "+00:00"))
        if scheduled.tzinfo is None:
            raise ValueError
        scheduled = scheduled.astimezone(ET)
    except ValueError:
        scheduled = None
        errors.append("intended schedule timestamp is invalid")
    if scheduled and (scheduled.hour != 2 or scheduled.minute != 0):
        errors.append("intended schedule was not 02:00 ET")
    if created and scheduled:
        delay = created.astimezone(dt.timezone.utc) - scheduled.astimezone(
            dt.timezone.utc)
        if delay < dt.timedelta(0) or delay > dt.timedelta(hours=6):
            errors.append("GitHub run start is outside the bounded schedule delay")

    if review.get("accepted") is not True or review.get("errors"):
        errors.append("deterministic artifact review did not accept the run")
    if report.get("observation_eligible") is not True:
        errors.append("runtime did not mark the observation eligible")
    observation_date = str(review.get("observation_date") or "")
    if observation_date != str(report.get("observation_date") or ""):
        errors.append("report and review observation dates differ")
    if scheduled and observation_date != scheduled.date().isoformat():
        errors.append("artifact date does not match intended scheduled ET date")
    if run.get("conclusion") != "success":
        errors.append("GitHub workflow conclusion is not success")

    return {
        "accepted": not errors,
        "date": observation_date,
        "run_id": run_id,
        "head_sha": str(run.get("headSha") or ""),
        "url": str(run.get("url") or ""),
        "scheduled_for": scheduled_for,
        "errors": list(dict.fromkeys(errors)),
        "summary": review.get("summary") if isinstance(
            review.get("summary"), dict) else {},
    }


def collect(*, limit: int = 30, runner=None,
            now: dt.datetime | None = None) -> dict:
    runner = runner or _run
    runs = json.loads(runner([
        "gh", "run", "list", "--workflow", WORKFLOW, "--limit", str(limit),
        "--json", "databaseId,event,status,conclusion,createdAt,updatedAt,headSha,url",
    ]))
    observations = []
    skipped = []
    skipped_nonmatching = []
    for run in runs:
        if run.get("event") != "schedule" or run.get("status") != "completed":
            continue
        try:
            created = dt.datetime.fromisoformat(
                str(run.get("createdAt") or "").replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            created = None
        if (not created or
                created.astimezone(dt.timezone.utc) < ROLLOUT_NOT_BEFORE):
            continue
        run_id = int(run["databaseId"])
        with tempfile.TemporaryDirectory(prefix="harbor-shadow-") as directory:
            target = Path(directory)
            try:
                runner([
                    "gh", "run", "download", str(run_id), "--name",
                    f"email-listening-{run_id}", "--dir", str(target),
                ])
            except RuntimeError as error:
                skipped.append({
                    "run_id": run_id,
                    "errors": [f"artifact download failed: {type(error).__name__}"],
                })
                continue
            try:
                gate = json.loads(
                    (target / "schedule-gate.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                gate = None
            if isinstance(gate, dict) and gate.get("eligible") is False:
                skipped_nonmatching.append({
                    "run_id": run_id,
                    "schedule_expression": str(
                        gate.get("schedule_expression") or ""),
                })
                continue
            observations.append(assess_run(run, target))

    accepted_by_date = {}
    for row in sorted(observations, key=lambda value: value["run_id"]):
        if row["accepted"] and row["date"]:
            accepted_by_date[row["date"]] = row
    accepted = [accepted_by_date[key] for key in sorted(accepted_by_date)]
    rejected = [row for row in observations if not row["accepted"]]
    today = (now or dt.datetime.now(ET)).astimezone(ET).date()
    completed_dates = [
        row["date"] for row in accepted
        if dt.date.fromisoformat(row["date"]) < today
    ]
    return {
        "accepted_count": len(accepted),
        "accepted_dates": [row["date"] for row in accepted],
        "accepted_observations": accepted,
        "rejected_runs": rejected,
        "artifact_download_failures": skipped,
        "skipped_nonmatching_schedules": skipped_nonmatching,
        "ready_for_live_verification": len(set(completed_dates)) >= 3,
    }


def apply_post_shadow_gates(state: dict, *, runner=None,
                            today: dt.date | None = None) -> None:
    runner = runner or _run
    dates = list(state.get("accepted_dates") or [])
    today = today or dt.datetime.now(ET).date()
    try:
        completed_dates = {
            value for value in dates if dt.date.fromisoformat(value) < today
        }
    except (TypeError, ValueError):
        raise RuntimeError("accepted scheduled ET dates are invalid") from None
    if (len(completed_dates) < 3 or
            not state.get("ready_for_live_verification")):
        raise RuntimeError(
            "three accepted completed scheduled ET dates are required")
    runner(["gh", "variable", "set", "EMAIL_SHADOW_OBSERVATION_DATES",
            "--body", ",".join(sorted(completed_dates))])
    runner(["gh", "variable", "set", "EMAIL_SHADOW_REVIEW_APPROVED",
            "--body", "true"])
    runner(["gh", "variable", "set", "EMAIL_AUTOMATIC_WRITES_APPROVED",
            "--body", "true"])


def _run(command: list[str]) -> str:
    process = subprocess.run(
        command, check=False, text=True, capture_output=True)
    if process.returncode:
        raise RuntimeError(
            f"command failed ({command[0]} {command[1]}): "
            f"{process.stderr.strip()[:300]}")
    return process.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", default="")
    parser.add_argument("--apply-post-shadow-gates", action="store_true")
    args = parser.parse_args()
    state = collect(limit=args.limit)
    if args.apply_post_shadow_gates:
        apply_post_shadow_gates(state)
    rendered = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if state["rejected_runs"] or state["artifact_download_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
