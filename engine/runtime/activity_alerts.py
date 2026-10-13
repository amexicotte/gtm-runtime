"""Public Slack accountability loop for monday Activities.

Each open Activity gets one concise line. A human reacts with a check mark; the
next poll marks the already-final Activity Done in monday, then deletes the bot's
Slack post. This keeps the runtime polling-only and requires no public webhook.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import re
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


@dataclass
class ActivityAlertStats:
    notified: int = 0
    deferred: int = 0
    scanned: int = 0
    completed: int = 0
    deleted: int = 0
    updated: int = 0


def sync_activity_alerts(slack, crm, *, channel: str = "deal-alerts",
                         dry_run: bool = False,
                         now: dt.datetime | None = None) -> ActivityAlertStats:
    stats = ActivityAlertStats()
    today = _et_date(now)
    rows = crm.activities_for_alerts()
    for activity in rows:
        root_ts = str(activity.get("slack_notification_ts") or "")
        if str(activity.get("status") or "").casefold() == "done":
            if root_ts and not dry_run:
                try:
                    slack.delete(channel, root_ts)
                    crm.clear_activity_alert(str(activity["id"]))
                    stats.deleted += 1
                except RuntimeError:
                    # Keep the saved timestamp so the next poll retries cleanup.
                    pass
            continue
        if not _due_for_alert(activity.get("due_date") or "", today=today):
            stats.deferred += 1
            # Before due-date gating existed, future tasks were announced as
            # soon as they were created. Retract that premature post and clear
            # its cursor so the due-date poll can create a fresh notification.
            if root_ts and not dry_run:
                try:
                    slack.delete(channel, root_ts)
                    crm.clear_activity_alert(str(activity["id"]))
                    stats.deleted += 1
                except RuntimeError:
                    # Retain the timestamp and retry cleanup on the next poll.
                    pass
            continue
        if not dry_run:
            activity["owner_ids"] = crm.ensure_activity_owner(
                str(activity["id"]), activity.get("owner_ids") or [])
        if not root_ts:
            if dry_run:
                continue
            text = _message(activity)
            root_ts = str(slack.post(channel, text))
            permalink = slack.permalink(channel, root_ts)
            crm.save_activity_alert(str(activity["id"]), root_ts, permalink)
            stats.notified += 1
            continue

        last_ts = str(activity.get("slack_last_ts") or root_ts)
        messages = slack.replies(channel, root_ts, oldest_ts=last_ts)
        stats.scanned += len(messages)
        completed = any(_checked(message, root_ts) for message in messages)
        highest = last_ts
        for message in messages:
            ts = str(message.get("ts") or "")
            if ts and _newer(ts, highest):
                highest = ts
        if completed:
            if not dry_run:
                crm.complete_activity_from_slack(str(activity["id"]), highest)
                try:
                    slack.delete(channel, root_ts)
                    crm.clear_activity_alert(str(activity["id"]))
                    stats.deleted += 1
                except RuntimeError:
                    # monday is already authoritative. Its retained Slack
                    # timestamp makes deletion retry on the next poll.
                    pass
            stats.completed += 1
        elif not dry_run:
            root = next((message for message in messages
                         if str(message.get("ts") or "") == root_ts), None)
            desired = _message(activity)
            if root and not _same_message(str(root.get("text") or ""), desired):
                slack.update(channel, root_ts, desired)
                stats.updated += 1
            if _newer(highest, last_ts):
                crm.save_activity_alert_cursor(str(activity["id"]), highest)
    return stats


def _message(activity: dict) -> str:
    company = (activity.get("company_name") or
               (activity.get("related_names") or ["CRM"])[0])
    return f"{company}: {activity['title']} ({_due(activity.get('due_date') or '')})"


def _due(value: str) -> str:
    if not value:
        return "No due date"
    try:
        date = dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    suffix = f", {date.year}" if date.year != dt.date.today().year else ""
    return f"{date.strftime('%b')} {date.day}{suffix}"


def _et_date(now: dt.datetime | None) -> dt.date:
    stamp = now or dt.datetime.now(dt.timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(ET).date()


def _due_for_alert(value: str, *, today: dt.date) -> bool:
    """Alert undated/invalid legacy tasks immediately; gate valid future dates."""
    raw = str(value or "").strip()
    if not raw:
        return True
    try:
        due = dt.date.fromisoformat(raw[:10])
    except ValueError:
        return True
    return due <= today


def _same_message(actual: str, desired: str) -> bool:
    """Ignore Slack's automatic URL-to-mrkdwn rendering when comparing text."""
    rendered = re.sub(r"<[^|>]+\|([^>]+)>", r"\1", actual)
    return rendered == desired


def _checked(message: dict, root_ts: str) -> bool:
    reactions = message.get("reactions") or []
    if any(reaction.get("name") in ("white_check_mark", "heavy_check_mark") and
           int(reaction.get("count") or 0) > 0 for reaction in reactions):
        return True
    if str(message.get("ts") or "") == root_ts:
        return False
    if message.get("bot_id") or message.get("subtype"):
        return False
    text = (message.get("text") or "").strip().casefold()
    return bool(re.match(r"^(?:done|complete|completed|mark done|checked|check|✅)\b", text))


def _newer(left: str, right: str) -> bool:
    try:
        return float(left) > float(right)
    except (TypeError, ValueError):
        return left > right
