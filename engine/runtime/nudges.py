"""Deterministic stalled-Deal and unanswered-follow-up Slack nudges."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import datetime as dt
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


@dataclass
class NudgeStats:
    stalled_proposed: int = 0
    followup_proposed: int = 0
    posted: int = 0
    cleared: int = 0
    unchanged: int = 0
    ambiguous: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def sync_nudges(slack, crm, *, deals: dict[str, dict], activities: list[dict],
                channel: str, shadow: bool, stalled_hours: int = 36,
                unanswered_hours: int = 48, now: dt.datetime | None = None,
                slack_user_map: str = "") -> NudgeStats:
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    mapping = _user_mapping(slack_user_map)
    stats = NudgeStats()

    for deal_id, deal in deals.items():
        due = stalled_condition(deal, now=now, hours=stalled_hours)
        if due:
            stats.stalled_proposed += 1
        _sync_one(
            slack, crm, kind="deal", row=deal, target_id=deal_id,
            condition=due, channel=channel, shadow=shadow,
            text=_stalled_message(deal, mapping, stalled_hours), stats=stats)

    deal_ids = set(deals)
    for activity in activities:
        related_deals = sorted(deal_ids.intersection(activity.get("deal_ids") or []))
        if len(related_deals) != 1:
            if activity.get("nudge_ts") or _old_enough(
                    activity.get("completed_at"), now, unanswered_hours):
                stats.ambiguous += 1
            continue
        deal = deals[related_deals[0]]
        due = followup_condition(
            activity, deal=deal, now=now, hours=unanswered_hours)
        if due:
            stats.followup_proposed += 1
        _sync_one(
            slack, crm, kind="activity", row=activity,
            target_id=str(activity["id"]), condition=due,
            channel=channel, shadow=shadow,
            text=_followup_message(activity, deal, mapping, unanswered_hours),
            stats=stats)
    return stats


def stalled_condition(deal: dict, *, now: dt.datetime, hours: int) -> str:
    if not deal.get("active"):
        return ""
    stamp = str(deal.get("last_interaction_at") or "")
    if not stamp or not _old_enough(stamp, now, hours, date_at_end=True):
        return ""
    return _condition_key("stalled", str(deal.get("id") or ""), stamp)


def followup_condition(activity: dict, *, deal: dict,
                       now: dt.datetime, hours: int) -> str:
    completed = str(activity.get("completed_at") or "")
    if not completed or not _old_enough(completed, now, hours):
        return ""
    inbound = str(deal.get("last_inbound_email_at") or "")
    if inbound and _parse(inbound) > _parse(completed):
        return ""
    return _condition_key(
        "unanswered", str(activity.get("id") or ""), completed)


def _sync_one(slack, crm, *, kind: str, row: dict, target_id: str,
              condition: str, channel: str, shadow: bool, text: str,
              stats: NudgeStats) -> None:
    prior_ts = str(row.get("nudge_ts") or "")
    prior_key = str(row.get("nudge_key") or "")
    if shadow:
        return
    if not condition:
        if prior_ts:
            slack.delete(channel, prior_ts)
            if kind == "deal":
                crm.clear_deal_nudge(target_id)
            else:
                crm.clear_followup_nudge(target_id)
            stats.cleared += 1
        return
    if prior_ts and prior_key == condition:
        stats.unchanged += 1
        return
    if prior_ts:
        slack.delete(channel, prior_ts)
    ts = str(slack.post(
        channel, text, client_msg_id=str(uuid.UUID(condition[:32]))))
    try:
        permalink = slack.permalink(channel, ts)
        if kind == "deal":
            crm.save_deal_nudge(
                target_id, condition_key=condition, message_ts=ts,
                permalink=permalink)
        else:
            crm.save_followup_nudge(
                target_id, condition_key=condition, message_ts=ts,
                permalink=permalink)
    except Exception:
        try:
            slack.delete(channel, ts)
        finally:
            raise
    stats.posted += 1


def _stalled_message(deal: dict, mapping: dict[str, str], hours: int) -> str:
    owner = _owner(deal, mapping) or "Deal owner:"
    url = (f"https://harboradvisors.monday.com/boards/10000000003/"
           f"pulses/{deal.get('id')}")
    return (f"{owner} Follow-up nudge: {deal.get('name') or 'Deal'} has no "
            f"recorded activity for {hours}+ hours. {url}")


def _followup_message(activity: dict, deal: dict,
                      mapping: dict[str, str], hours: int) -> str:
    owner = _owner(activity, mapping) or _owner(deal, mapping) or "Deal owner:"
    url = (f"https://harboradvisors.monday.com/boards/10000000005/"
           f"pulses/{activity.get('id')}")
    return (f"{owner} Follow-up nudge: no client response {hours // 24} days "
            f"after “{activity.get('title') or 'completed commitment'}” for "
            f"{deal.get('name') or 'this Deal'}. {url}")


def _owner(row: dict, mapping: dict[str, str]) -> str:
    for owner_id in row.get("owner_ids") or []:
        if str(owner_id) in mapping:
            return f"<@{mapping[str(owner_id)]}>"
    name = str(row.get("owner") or "").strip()
    return f"{name}:" if name else ""


def _user_mapping(value: str) -> dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return {str(key): str(item) for key, item in parsed.items() if item}


def _condition_key(kind: str, target: str, anchor: str) -> str:
    return hashlib.sha256(f"{kind}:{target}:{anchor}".encode()).hexdigest()


def _old_enough(value: str, now: dt.datetime, hours: int,
                date_at_end: bool = False) -> bool:
    try:
        stamp = _parse(str(value), date_at_end=date_at_end)
    except ValueError:
        return False
    return now - stamp >= dt.timedelta(hours=hours)


def _parse(value: str, *, date_at_end: bool = False) -> dt.datetime:
    raw = str(value).strip()
    if len(raw) == 10:
        clock = dt.time.max if date_at_end else dt.time.min
        parsed = dt.datetime.combine(dt.date.fromisoformat(raw), clock, tzinfo=ET)
    else:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(dt.timezone.utc)
