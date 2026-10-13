"""Monitor monthly GitHub Actions minutes and emit bounded Slack alerts.

The preferred source is GitHub's account billing summary API using a
fine-grained token with only ``Plan: read``.  When that optional credential is
absent, the monitor reconstructs a conservative repository-level estimate from
workflow run durations.  Every workflow in this repository currently has one
job, so rounding each run to the next minute mirrors GitHub's Linux billing
rule closely without making one API request per job.
"""
from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
import json
import math
import urllib.parse
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests


GITHUB_API = "https://api.github.com"
ET = ZoneInfo("America/New_York")


@dataclasses.dataclass(frozen=True)
class UsageSnapshot:
    period: str
    minutes: int
    limit: int
    days_elapsed: int
    days_remaining: int
    days_in_month: int
    source: str

    @property
    def percent(self) -> float:
        return self.minutes / self.limit * 100

    @property
    def minutes_per_day(self) -> float:
        return self.minutes / max(self.days_elapsed, 1)

    @property
    def projected_minutes(self) -> int:
        return math.ceil(self.minutes_per_day * self.days_in_month)

    @property
    def projected_percent(self) -> float:
        return self.projected_minutes / self.limit * 100


@dataclasses.dataclass(frozen=True)
class AlertDecision:
    level: int | None
    should_alert: bool
    reason: str


class JsonAPI:
    """Small JSON HTTP client with explicit bearer-token boundaries."""

    def __init__(self, token: str, *, api_version: str = "2026-03-10"):
        if not token:
            raise RuntimeError("GitHub API token is required")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": api_version,
            "User-Agent": "harbor-github-actions-usage-monitor",
        })

    def get(self, path: str, query: dict[str, Any] | None = None) -> dict:
        url = f"{GITHUB_API}{path}"
        try:
            response = self.session.get(url, params=query, timeout=30)
        except requests.RequestException as error:
            raise RuntimeError(f"GitHub API {path} request failed") from error
        if not response.ok:
            body = response.text[:500]
            raise RuntimeError(
                f"GitHub API {path} returned HTTP {response.status_code}: {body}")
        try:
            return response.json()
        except ValueError as error:
            raise RuntimeError(f"GitHub API {path} returned invalid JSON") from error

    def pages(self, path: str, query: dict[str, Any], key: str) -> Iterable[dict]:
        page = 1
        while True:
            page_query = dict(query, page=page, per_page=100)
            response = self.get(path, page_query)
            items = response.get(key, [])
            if not isinstance(items, list):
                raise RuntimeError(f"GitHub API {path} returned invalid {key}")
            yield from items
            if len(items) < 100:
                return
            page += 1


def _parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def month_context(now: dt.datetime) -> tuple[str, dt.datetime, int, int, int]:
    local = now.astimezone(ET)
    days_in_month = calendar.monthrange(local.year, local.month)[1]
    start = dt.datetime(local.year, local.month, 1, tzinfo=ET)
    return (
        f"{local.year:04d}-{local.month:02d}",
        start,
        local.day,
        days_in_month - local.day,
        days_in_month,
    )


def billing_minutes(payload: dict) -> int:
    total = 0.0
    for item in payload.get("usageItems", []):
        if str(item.get("product", "")).casefold() != "actions":
            continue
        if str(item.get("unitType", "")).casefold() != "minutes":
            continue
        quantity = item.get("grossQuantity", item.get("quantity", 0))
        total += float(quantity or 0)
    return math.ceil(total)


def estimated_run_minutes(runs: Iterable[dict]) -> int:
    """Conservatively estimate rounded Linux minutes for one-job workflows."""
    total = 0
    for run in runs:
        started = run.get("run_started_at") or run.get("created_at")
        completed = run.get("updated_at")
        if not started or not completed:
            continue
        seconds = max(
            0, (_parse_timestamp(completed) - _parse_timestamp(started)).total_seconds())
        attempts = max(int(run.get("run_attempt") or 1), 1)
        if seconds:
            total += math.ceil(seconds / 60) * attempts
    return total


def account_usage(api: JsonAPI, owner: str, now: dt.datetime) -> int:
    local = now.astimezone(ET)
    payload = api.get(
        f"/users/{urllib.parse.quote(owner)}/settings/billing/usage/summary",
        {"year": local.year, "month": local.month, "product": "actions"},
    )
    return billing_minutes(payload)


def repository_usage(api: JsonAPI, repository: str, now: dt.datetime) -> int:
    _, start, _, _, _ = month_context(now)
    runs = api.pages(
        f"/repos/{repository}/actions/runs",
        {"created": f">={start.astimezone(dt.timezone.utc).isoformat()}",
         "exclude_pull_requests": "true"},
        "workflow_runs",
    )
    return estimated_run_minutes(runs)


def make_snapshot(*, minutes: int, limit: int, source: str,
                  now: dt.datetime) -> UsageSnapshot:
    if limit <= 0:
        raise RuntimeError("ACTIONS_MONTHLY_MINUTES_LIMIT must be positive")
    period, _, elapsed, remaining, days_in_month = month_context(now)
    return UsageSnapshot(
        period=period,
        minutes=max(minutes, 0),
        limit=limit,
        days_elapsed=elapsed,
        days_remaining=remaining,
        days_in_month=days_in_month,
        source=source,
    )


def decide_alert(snapshot: UsageSnapshot, thresholds: Iterable[int],
                 prior_level: int | None, *, force: bool = False) -> AlertDecision:
    normalized = sorted({int(value) for value in thresholds if int(value) > 0})
    crossed = [value for value in normalized if snapshot.percent >= value]
    level = max(crossed) if crossed else None
    if force:
        return AlertDecision(level=level, should_alert=True, reason="manual test")
    if level is None:
        return AlertDecision(level=None, should_alert=False,
                             reason="below configured thresholds")
    if prior_level is not None and prior_level >= level:
        return AlertDecision(level=level, should_alert=False,
                             reason=f"{level}% alert already sent")
    return AlertDecision(level=level, should_alert=True,
                         reason=f"crossed {level}% threshold")


def alert_artifact_name(period: str, level: int) -> str:
    return f"github-actions-usage-alert-{period}-{level}"


def prior_alert_level(api: JsonAPI, repository: str, period: str,
                      thresholds: Iterable[int]) -> int | None:
    found = []
    for level in thresholds:
        name = alert_artifact_name(period, int(level))
        response = api.get(
            f"/repos/{repository}/actions/artifacts",
            {"name": name, "per_page": 1},
        )
        if any(not artifact.get("expired", False)
               for artifact in response.get("artifacts", [])):
            found.append(int(level))
    return max(found) if found else None


def format_slack_message(snapshot: UsageSnapshot, decision: AlertDecision,
                         *, test: bool = False) -> str:
    prefix = ":test_tube: TEST — " if test else ":warning: "
    level = f"{decision.level}%" if decision.level is not None else "usage"
    month = dt.datetime.strptime(snapshot.period, "%Y-%m").strftime("%B %Y")
    projected_status = (
        "above" if snapshot.projected_minutes > snapshot.limit else "within")
    return (
        f"{prefix}*GitHub Actions {level} alert*\n"
        f"• Used: *{snapshot.minutes:,} / {snapshot.limit:,} minutes* "
        f"({snapshot.percent:.1f}%)\n"
        f"• Time left: *{snapshot.days_remaining} days* in {month} "
        f"({snapshot.days_elapsed} of {snapshot.days_in_month} elapsed)\n"
        f"• Pace: {snapshot.minutes_per_day:.1f} minutes/day; projected "
        f"*{snapshot.projected_minutes:,} minutes* "
        f"({snapshot.projected_percent:.1f}%), {projected_status} the configured limit\n"
        f"• Source: {snapshot.source}\n"
        "Review or upgrade: https://github.com/settings/billing"
    )


def write_marker(path: Path, snapshot: UsageSnapshot,
                 decision: AlertDecision, slack_ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "period": snapshot.period,
        "level": decision.level,
        "minutes": snapshot.minutes,
        "limit": snapshot.limit,
        "percent": round(snapshot.percent, 2),
        "days_remaining": snapshot.days_remaining,
        "source": snapshot.source,
        "slack_ts": slack_ts,
    }, indent=2, sort_keys=True) + "\n")
