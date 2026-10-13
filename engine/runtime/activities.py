"""Create idempotent monday Activities from extracted meeting commitments."""
from __future__ import annotations

import ast
import datetime as dt
import re

from .evidence import evidence_problem


def create_activities_from_result(crm, meeting: dict, ctx: dict, result: dict,
                                  *, slack_link: str = "") -> list[str]:
    wrapped = (result.get("meeting") or {}).get("action_items")
    answered = (isinstance(wrapped, dict) and
                str(wrapped.get("status") or "").strip().upper() == "ANSWERED")
    items = action_items(wrapped) if answered and not evidence_problem(wrapped) else []
    return create_activities(
        crm, meeting, ctx, items, slack_link=slack_link)


def create_activities(crm, meeting: dict, ctx: dict, items: list[str], *,
                      slack_link: str = "") -> list[str]:
    """Create missing tasks, deduped by normalized title + Source Meeting."""
    if not items:
        return []
    existing = crm.existing_activity_titles(str(meeting["id"]))
    contact_names = crm.item_names(ctx.get("contact_ids") or [])
    owner_ids = (
        crm.owner_ids_for_context(
            account_id=ctx.get("prospect_account_id"),
            deal_id=ctx.get("deal_id"),
            meeting_owner_ids=meeting.get("owner_ids") or [],
        )
        if hasattr(crm, "owner_ids_for_context")
        else [str(value) for value in meeting.get("owner_ids") or []]
    )
    created = []
    for title in items:
        normalized = _norm(title)
        if not normalized or normalized in existing:
            continue
        task_type = _task_type(title, contact_names)
        due_date = _due_date(meeting.get("date") or "", title)
        related = [ctx.get("prospect_account_id"), *(ctx.get("contact_ids") or [])]
        if ctx.get("deal_id"):
            related.append(ctx["deal_id"])
        activity_id = crm.create_activity(
            title=title,
            task_type=task_type,
            due_date=due_date,
            owner_ids=[str(value) for value in owner_ids],
            related_ids=[str(value) for value in related if value],
            source_meeting_id=str(meeting["id"]),
            slack_link=slack_link,
            notes=f"Created from {meeting.get('title') or 'meeting'} by the meeting engine.",
        )
        created.append(str(activity_id))
        existing.add(normalized)
    return created


def create_email_activities(crm, *, event, ctx: dict,
                            items: list[dict]) -> list[dict]:
    """Create exact, evidenced email commitments keyed by BusinessEvent."""
    if not items:
        return []
    related = [*event.account_ids, *event.contact_ids, *event.deal_ids]
    related_ids = {str(value) for value in related if value}
    existing = crm.existing_activity_titles_for_event(
        event.event_key, related_ids)
    contact_names = crm.item_names(list(event.contact_ids))
    owner_ids = crm.owner_ids_for_context(
        account_id=event.account_ids[0] if event.account_ids else None,
        deal_id=event.deal_ids[0] if event.deal_ids else None,
        meeting_owner_ids=[],
    )
    created = []
    for item in items:
        title = str(item.get("title") or "").strip()
        normalized = _norm(title)
        if not normalized or normalized in existing:
            continue
        explicit_due = str(item.get("due_date") or "").strip()
        try:
            due_date = dt.date.fromisoformat(explicit_due[:10]).isoformat()
        except ValueError:
            due_date = _due_date(event.occurred_at, title)
        activity_id = crm.create_activity(
            title=title,
            task_type=_task_type(title, contact_names),
            due_date=due_date,
            owner_ids=[str(value) for value in owner_ids],
            related_ids=sorted(related_ids),
            source_meeting_id="",
            source_event_key=event.event_key,
            slack_link=event.source_url,
            notes=(f"Created from {event.title or 'email'} by the email listening "
                   f"engine. Source event: {event.event_key}."),
        )
        created.append({"id": str(activity_id), "title": title})
        existing.add(normalized)
    return created


def action_items(value) -> list[str]:
    """Normalize model wrappers, lists, legacy list reprs, or bullet text."""
    if isinstance(value, dict):
        if str(value.get("status") or "").upper() == "NOT_DISCUSSED":
            return []
        value = value.get("value")
    if isinstance(value, list):
        return _unique(_action_title(item) for item in value)
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return _unique(str(item).strip() for item in parsed)
        except (SyntaxError, ValueError):
            pass
    lines = [re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", line).strip()
             for line in raw.splitlines()]
    return _unique(lines)


def _task_type(title: str, contact_names: list[str]) -> str:
    lowered = title.casefold().strip()
    external_names = {part.casefold() for name in contact_names
                      for part in (name, name.split(" ", 1)[0]) if part}
    if any(lowered.startswith(f"{name} ") for name in external_names):
        return "Chase"
    if lowered.startswith(("client ", "customer ", "prospect ")):
        return "Chase"
    return "Do it"


def _due_date(meeting_date: str, title: str) -> str:
    try:
        base = dt.date.fromisoformat(meeting_date[:10])
    except ValueError:
        base = dt.date.today()
    lowered = title.casefold()

    iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", title)
    if iso:
        try:
            return dt.date(*map(int, iso.groups())).isoformat()
        except ValueError:
            pass

    slash = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", title)
    if slash:
        month, day = int(slash.group(1)), int(slash.group(2))
        year_text = slash.group(3)
        year = int(year_text) if year_text else base.year
        if year < 100:
            year += 2000
        try:
            candidate = dt.date(year, month, day)
            if not year_text and candidate < base:
                candidate = dt.date(year + 1, month, day)
            return candidate.isoformat()
        except ValueError:
            pass

    months = {name.casefold(): index for index, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"), 1)}
    month_pattern = "|".join([*months, *(name[:3] for name in months)])
    named = re.search(
        rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        title, re.I)
    if named:
        key = named.group(1).casefold()
        month = months.get(key) or months[next(name for name in months if name.startswith(key))]
        year = int(named.group(3)) if named.group(3) else base.year
        try:
            candidate = dt.date(year, month, int(named.group(2)))
            if not named.group(3) and candidate < base:
                candidate = dt.date(year + 1, month, int(named.group(2)))
            return candidate.isoformat()
        except ValueError:
            pass

    if "day after tomorrow" in lowered:
        return (base + dt.timedelta(days=2)).isoformat()
    if re.search(r"\btomorrow\b", lowered):
        return (base + dt.timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", lowered):
        return base.isoformat()
    relative = re.search(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", lowered)
    if relative:
        count = int(relative.group(1))
        if relative.group(2).startswith("week"):
            count *= 7
        return (base + dt.timedelta(days=count)).isoformat()
    if "next week" in lowered:
        return (base + dt.timedelta(days=7)).isoformat()

    weekdays = {name.casefold(): index for index, name in enumerate((
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))}
    match = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
                      title, re.I)
    if match:
        target = weekdays[match.group(1).casefold()]
        days = (target - base.weekday()) % 7 or 7
        return (base + dt.timedelta(days=days)).isoformat()
    return (base + dt.timedelta(days=7)).isoformat()


def _action_title(item) -> str:
    if not isinstance(item, dict):
        return str(item).strip()
    for key in ("title", "task", "action", "value"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""


def _unique(values) -> list[str]:
    out = []
    for value in values:
        if value and _norm(value) not in {_norm(item) for item in out}:
            out.append(value)
    return out


def _norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))
