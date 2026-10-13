"""CLI entry point for the GitHub Actions usage Slack monitor."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from .github_actions_usage import (
    ET,
    JsonAPI,
    account_usage,
    alert_artifact_name,
    decide_alert,
    format_slack_message,
    make_snapshot,
    prior_alert_level,
    repository_usage,
    write_marker,
)
from .slack import Slack


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def _append(path_value: str, lines: list[str]) -> None:
    if not path_value:
        return
    with Path(path_value).open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-alert", action="store_true")
    parser.add_argument(
        "--marker", default=".artifacts/github-actions-usage/alert.json")
    args = parser.parse_args()

    now = dt.datetime.now(tz=ET)
    repository = _required("GITHUB_REPOSITORY")
    owner = os.environ.get("GITHUB_ACCOUNT", repository.split("/", 1)[0]).strip()
    repository_api = JsonAPI(_required("GITHUB_REPOSITORY_TOKEN"))
    billing_token = os.environ.get("GITHUB_BILLING_READ_TOKEN", "").strip()
    limit = int(os.environ.get("ACTIONS_MONTHLY_MINUTES_LIMIT", "2000"))
    thresholds = tuple(int(value.strip()) for value in os.environ.get(
        "ACTIONS_USAGE_ALERT_THRESHOLDS", "75,90,100").split(",")
        if value.strip())

    source = "repository run-duration estimate (one job per workflow)"
    if billing_token:
        try:
            minutes = account_usage(JsonAPI(billing_token), owner, now)
            source = "GitHub account billing API"
        except RuntimeError as error:
            print(f"Billing API unavailable; using repository estimate: {error}")
            minutes = repository_usage(repository_api, repository, now)
    else:
        minutes = repository_usage(repository_api, repository, now)

    snapshot = make_snapshot(
        minutes=minutes, limit=limit, source=source, now=now)
    prior = prior_alert_level(
        repository_api, repository, snapshot.period, thresholds)
    decision = decide_alert(
        snapshot, thresholds, prior, force=args.force_alert)

    alert_sent = False
    marker_created = False
    artifact_name = ""
    if decision.should_alert:
        user_id = os.environ.get("SLACK_USAGE_ALERT_USER_ID", "").strip()
        destination = user_id or os.environ.get(
            "SLACK_USAGE_ALERT_CHANNEL", "deal-alerts").strip()
        message = format_slack_message(
            snapshot, decision, test=args.force_alert)
        slack = Slack(token=_required("SLACK_BOT_TOKEN"))
        slack_ts = (
            slack.dm(user_id, message) if user_id else
            slack.post(destination, message)
        )
        alert_sent = True
        if not args.force_alert and decision.level is not None:
            artifact_name = alert_artifact_name(
                snapshot.period, decision.level)
            write_marker(Path(args.marker), snapshot, decision, slack_ts)
            marker_created = True

    print(
        f"GitHub Actions usage: {snapshot.minutes}/{snapshot.limit} minutes "
        f"({snapshot.percent:.1f}%); {snapshot.days_remaining} days remain; "
        f"{decision.reason}; source={snapshot.source}")
    _append(os.environ.get("GITHUB_OUTPUT", ""), [
        f"usage_minutes={snapshot.minutes}",
        f"usage_percent={snapshot.percent:.2f}",
        f"days_remaining={snapshot.days_remaining}",
        f"alert_sent={str(alert_sent).lower()}",
        f"marker_created={str(marker_created).lower()}",
        f"artifact_name={artifact_name}",
    ])
    _append(os.environ.get("GITHUB_STEP_SUMMARY", ""), [
        "### GitHub Actions usage",
        "",
        f"- Used: **{snapshot.minutes:,} / {snapshot.limit:,} minutes** "
        f"({snapshot.percent:.1f}%)",
        f"- Time left: **{snapshot.days_remaining} days**",
        f"- Projected month-end: **{snapshot.projected_minutes:,} minutes** "
        f"({snapshot.projected_percent:.1f}%)",
        f"- Source: {snapshot.source}",
        f"- Alert: {decision.reason}",
    ])


if __name__ == "__main__":
    main()
