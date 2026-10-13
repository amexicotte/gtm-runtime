"""Slack intake and one-pass processing for calls without a recording."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import re
from zoneinfo import ZoneInfo

from matcher import classify_domain

from .agent_events import publish_cached_event
from .business_events import attach_business_event
from .config import config
from .deals import ensure_deal_from_result
from .evidence import block_nonverbatim_evidence, enrich_reasoning_evidence
from .reasoning import extract_manual


_TRIGGER = re.compile(
    r"^\s*(?:manual(?:\s+(?:call|notes))?|unrecorded(?:\s+call)?)\s*:\s*",
    re.I,
)


class ManualIdentityError(RuntimeError):
    """The notes do not identify one safe Account mapping."""


@dataclass
class ManualNotesSyncResult:
    scanned: int = 0
    candidates: int = 0
    created: int = 0
    ignored: int = 0
    meeting_ids: list[str] = field(default_factory=list)


@dataclass
class ManualWriteResult:
    result: dict
    ctx: dict


def manual_note_body(text: str) -> str | None:
    """Return an explicitly triggered manual note, otherwise ``None``."""
    match = _TRIGGER.match(text or "")
    if not match:
        return None
    body = (text or "")[match.end():].strip()
    return body or None


def manual_meeting_title(body: str) -> str:
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    first = re.sub(r"\s+", " ", first)
    if len(first) > 72:
        first = first[:71].rstrip() + "…"
    return f"Manual call — {first or 'untitled notes'}"


def sync_manual_note_posts(slack, crm, *, channel: str = "call-notes",
                           lookback_days: int = 14,
                           now: dt.datetime | None = None,
                           dry_run: bool = True) -> ManualNotesSyncResult:
    """Create one monday Meeting for every new ``manual:`` Slack root post."""
    now = now or dt.datetime.now(dt.timezone.utc)
    oldest = str((now - dt.timedelta(days=lookback_days)).timestamp())
    messages = slack.history(channel, oldest_ts=oldest)
    existing = set(crm.existing_slack_intake_ids())
    result = ManualNotesSyncResult(scanned=len(messages))

    for message in sorted(messages, key=lambda row: float(row.get("ts") or 0)):
        ts = str(message.get("ts") or "")
        thread_ts = str(message.get("thread_ts") or "")
        body = manual_note_body(message.get("text") or "")
        if (not ts or message.get("bot_id") or message.get("subtype") or
                (thread_ts and thread_ts != ts) or body is None):
            result.ignored += 1
            continue
        result.candidates += 1
        if ts in existing:
            result.ignored += 1
            continue
        if dry_run:
            continue

        timestamp = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc)
        meeting_id = str(crm.create_manual_meeting(
            title=manual_meeting_title(body),
            date=timestamp.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
            notes=body,
            slack_ts=ts,
            slack_user_id=str(message.get("user") or ""),
            requested_at=timestamp.isoformat(),
        ))
        existing.add(ts)
        result.created += 1
        result.meeting_ids.append(meeting_id)
        try:
            slack.post(
                channel,
                "Queued as a manual CRM call. I’ll match or create the records, "
                "write the final updates to monday, and create the action items "
                "here. You can review the listening summary and give feedback "
                "in this Slack thread.",
                thread_ts=ts,
            )
        except RuntimeError:
            # The durable monday row is the intake acknowledgement. A transient
            # Slack write failure must not strand it before reasoning.
            pass
    return result


def manual_reason_and_write(crm, *, notes: str, meeting_title: str,
                            meeting_date: str, meeting: dict,
                            extractor=extract_manual, applier=None,
                            publisher=None
                            ) -> ManualWriteResult:
    """Make the single model call, cache it, then resolve and write final values."""
    if not notes or not notes.strip():
        raise ValueError("manual reasoning requires non-empty notes")
    meeting_id = str(meeting["id"])
    if hasattr(crm, "begin_meeting_processing"):
        crm.begin_meeting_processing(meeting_id)
    else:
        crm.change_columns("meeting", meeting_id, {
            "Processing Status": "Processing",
        })
    try:
        result = extractor(notes, meeting_title, meeting_date)
    except Exception:
        crm.change_columns("meeting", meeting_id, {
            "Processing Status": "Ready",
        })
        raise
    if not isinstance(result, dict):
        crm.change_columns("meeting", meeting_id, {
            "Processing Status": "Ready",
        })
        raise TypeError("manual reasoning result must be a JSON object")
    block_nonverbatim_evidence(result, [notes])
    if hasattr(crm, "save_reasoning_result"):
        try:
            crm.save_reasoning_result(meeting_id, result)
        except Exception:
            crm.change_columns("meeting", meeting_id, {
                "Processing Status": "Ready",
            })
            raise
    try:
        if not _stored_context(meeting).get("prospect_account_id"):
            _validate_manual_identity_source(result, notes)
    except ManualIdentityError:
        crm.change_columns("meeting", meeting_id, {
            "Processing Status": "Ready",
        })
        raise
    event_meeting = dict(meeting)
    event_meeting.setdefault("date", meeting_date)
    event_meeting.setdefault("manual_notes", notes)
    return resume_manual_write(
        crm, result=result, meeting_title=meeting_title, meeting=event_meeting,
        applier=applier, publisher=publisher,
    )


def resume_manual_write(crm, *, result: dict, meeting_title: str,
                        meeting: dict, applier=None,
                        publisher=None) -> ManualWriteResult:
    """Resolve cached manual-note identity and apply it without another call."""
    if not isinstance(result, dict):
        raise TypeError("manual reasoning result must be a JSON object")
    ctx = _stored_context(meeting)
    source_notes = str(meeting.get("manual_notes") or "")
    block_nonverbatim_evidence(result, [source_notes])
    if not ctx.get("prospect_account_id"):
        _validate_manual_identity_source(result, source_notes)
    if result.get("relevant") is not True:
        event = attach_business_event(
            result, source="manual_note", meeting_id=str(meeting["id"]),
            occurred_at=str(meeting.get("date") or ""), title=meeting_title,
            content=str(meeting.get("manual_notes") or ""), ctx=ctx,
        )
        enrich_reasoning_evidence(result, event)
        if hasattr(crm, "save_reasoning_result"):
            crm.save_reasoning_result(str(meeting["id"]), result)
        publisher = publisher or publish_cached_event
        publisher(
            crm, result, activation_at=config.BUSINESS_EVENTS_ACTIVATED_AT)
        crm.change_columns("meeting", str(meeting["id"]), {
            "Processing Status": "Processed",
        })
        return ManualWriteResult(result=result, ctx=ctx)
    if not ctx.get("prospect_account_id"):
        ctx = resolve_manual_identity(crm, str(meeting["id"]), result)
    ensure_deal_from_result(crm, ctx, result, meeting_title=meeting_title)
    event = attach_business_event(
        result, source="manual_note", meeting_id=str(meeting["id"]),
        occurred_at=str(meeting.get("date") or ""), title=meeting_title,
        content=str(meeting.get("manual_notes") or ""), ctx=ctx,
    )
    enrich_reasoning_evidence(result, event)
    # Identity is deterministic and can resolve after the first cache save.
    # Persist the completed envelope before any field/narrative effects.
    if hasattr(crm, "save_reasoning_result"):
        crm.save_reasoning_result(str(meeting["id"]), result)
    if applier is None:
        from reasoning.writer import apply
        applier = apply
    applier(result, ctx)
    publisher = publisher or publish_cached_event
    publisher(
        crm, result, activation_at=config.BUSINESS_EVENTS_ACTIVATED_AT)
    crm.change_columns("meeting", str(meeting["id"]), {
        "Processing Status": "Processed",
    })
    return ManualWriteResult(result=result, ctx=ctx)


def resolve_manual_identity(crm, meeting_id: str, result: dict) -> dict:
    """Resolve model-extracted identity using exact CRM keys."""
    identity = result.get("identity") or {}
    if not isinstance(identity, dict):
        raise ManualIdentityError("DeepSeek did not return a usable identity")
    domain = _domain(identity.get("account_domain"))
    name = _text(identity.get("account_name"))

    account = crm.find_account_by_domain(domain) if domain else None
    if account:
        account_id = str(account["id"])
    elif domain:
        account_id = str(crm.create_account_from_manual(name or "", domain=domain))
    elif name:
        matches = crm.find_accounts_by_name(name)
        if len(matches) > 1:
            raise ManualIdentityError(
                f"more than one Account is named {name!r}; a domain is required")
        account_id = (str(matches[0]["id"]) if matches else
                      str(crm.create_account_from_manual(name)))
    else:
        raise ManualIdentityError(
            "the notes did not identify a company name or business domain")

    contact_ids: list[str] = []
    contact_email_ids: dict[str, str] = {}
    contacts = identity.get("contacts") or []
    for contact in contacts if isinstance(contacts, list) else []:
        if not isinstance(contact, dict):
            continue
        email = _email(contact.get("email"))
        contact_name = _text(contact.get("name"))
        if email:
            existing = crm.find_contact_by_email(email)
            contact_id = (str(existing["id"]) if existing else
                          str(crm.create_contact_from_manual(
                              contact_name or "", account_id, email=email)))
            contact_email_ids[email] = contact_id
        elif contact_name:
            matches = crm.find_contacts_by_name(contact_name, account_id)
            if len(matches) > 1:
                continue
            contact_id = (str(matches[0]["id"]) if matches else
                          str(crm.create_contact_from_manual(contact_name, account_id)))
        else:
            continue
        if contact_id not in contact_ids:
            contact_ids.append(contact_id)

    active = (crm.active_deals_for_account(account_id)
              if hasattr(crm, "active_deals_for_account") else [])
    if len(active) > 1:
        raise ManualIdentityError(
            "the matched Account has multiple active Deals; close the duplicate "
            "before processing")
    try:
        deal_id = (crm.preferred_deal_for_account(account_id)
                   if hasattr(crm, "preferred_deal_for_account") else
                   (str(active[0]["id"]) if active else
                    crm.find_deal_for_account(account_id)))
    except RuntimeError as error:
        raise ManualIdentityError(str(error)) from error
    ctx = {
        "meeting_id": str(meeting_id),
        "prospect_account_id": account_id,
        "partner_account_id": None,
        "deal_id": str(deal_id) if deal_id else None,
        "contact_ids": contact_ids,
        "contact_email_ids": contact_email_ids,
        "attendee_emails": list(contact_email_ids),
    }
    crm.save_manual_meeting_context(
        meeting_id, account_id=account_id, contact_ids=contact_ids,
        contact_email_ids=contact_email_ids, deal_id=ctx["deal_id"],
    )
    return ctx


def manual_identity_prompt(error: Exception) -> str:
    return (
        "I extracted the CRM updates and tasks, but I can’t safely match the "
        f"company yet ({error}). Reply in this thread with:\n"
        "```Company domain: example.com\n"
        "Contact: Jane Smith <jane@example.com>```\n"
        "I’ll reuse the cached DeepSeek result after the mapping, so this will "
        "not trigger another model call."
    )


def _stored_context(meeting: dict) -> dict:
    accounts = [str(value) for value in meeting.get("account_ids") or []]
    prospect = str(meeting.get("prospect_account_id") or "")
    if not prospect and len(accounts) == 1:
        prospect = accounts[0]
    deals = [str(value) for value in meeting.get("deal_ids") or []]
    return {
        "meeting_id": str(meeting["id"]),
        "prospect_account_id": prospect or None,
        "partner_account_id": None,
        "deal_id": deals[0] if deals else None,
        "contact_ids": [str(value) for value in meeting.get("contact_ids") or []],
        "contact_email_ids": {
            str(key).lower(): str(value)
            for key, value in (meeting.get("contact_email_ids") or {}).items()
        },
        "attendee_emails": [
            value.strip() for value in str(
                meeting.get("attendee_emails") or "").splitlines() if value.strip()
        ],
    }


def _validate_manual_identity_source(result: dict, notes: str) -> None:
    identity = result.get("identity") or {}
    if not isinstance(identity, dict):
        raise ManualIdentityError("manual identity must be an object")
    source = re.sub(r"\s+", " ", str(notes).casefold()).strip()
    checks: list[tuple[str, str]] = []
    name = _text(identity.get("account_name"))
    raw_domain = _text(identity.get("account_domain"))
    domain = _domain(raw_domain)
    if name:
        checks.append(("account name", name))
    if raw_domain:
        if not domain:
            raise ManualIdentityError("manual identity contains an invalid business domain")
        checks.append(("account domain", domain))
    for contact in identity.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        contact_name = _text(contact.get("name"))
        raw_email = _text(contact.get("email"))
        email = _email(raw_email)
        if contact_name:
            checks.append(("contact name", contact_name))
        if raw_email:
            if not email:
                raise ManualIdentityError("manual identity contains an invalid Contact email")
            checks.append(("Contact email", email))
    if not checks:
        raise ManualIdentityError("manual note contains no source-proven identity")
    for label, value in checks:
        normalized = re.sub(r"\s+", " ", value.casefold()).strip()
        if normalized not in source:
            raise ManualIdentityError(
                f"{label} is not present in the manual-note source")


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _domain(value) -> str:
    raw = _text(value).lower()
    match = re.search(
        r"(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,})",
        raw,
    )
    domain = match.group(1).rstrip(".") if match else ""
    return domain if domain and classify_domain(domain) == "external" else ""


def _email(value) -> str:
    match = re.search(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", _text(value), re.I)
    return match.group(0).lower().rstrip(".") if match else ""
