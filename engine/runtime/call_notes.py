"""Exact, cursor-based #call-notes orchestration for monday Meetings.

Each meeting gets one Slack prompt. Human notes are replies to that prompt, so
the prompt timestamp is both an exact meeting key and the start of the cursor.
Only replies newer than ``Call Notes Last TS`` are requested on later runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import re

from matcher import classify_domain, domain_of, parse_domains

from .gate import is_scrap_command


@dataclass
class NoteMatch:
    message_ts: str
    meeting_id: str
    meeting_title: str
    score: int = 100


@dataclass
class CallNotesResult:
    scanned: int = 0
    matched: int = 0
    attached: int = 0
    ignored: int = 0
    prompted: int = 0
    account_types_updated: int = 0
    matches: list[NoteMatch] = field(default_factory=list)


def ensure_call_note_prompts(slack, monday, *, channel: str = "call-notes",
                             dry_run: bool = True,
                             requested_at: dt.datetime | None = None
                             ) -> CallNotesResult:
    """Prompt every active meeting that does not already have a prompt."""
    result = CallNotesResult()
    for meeting in monday.meetings_for_call_notes():
        if meeting.get("status") not in ("Awaiting", "Ready", "Gated"):
            continue
        if (meeting.get("status") == "Gated" and
                not requires_manual_identity(meeting)):
            continue
        if meeting.get("call_notes_prompt_ts"):
            continue
        request_call_notes(
            slack, monday, meeting, channel=channel, dry_run=dry_run,
            requested_at=requested_at,
        )
        result.prompted += 1
    return result


def request_call_notes(slack, monday, meeting: dict, *,
                       channel: str = "call-notes", dry_run: bool = True,
                       requested_at: dt.datetime | None = None) -> str | None:
    """Post one notes request and persist its root thread timestamp."""
    if meeting.get("call_notes_prompt_ts"):
        return str(meeting["call_notes_prompt_ts"])
    account = ", ".join(meeting.get("account_names") or []) or "account pending"
    date = meeting.get("date") or "date unavailable"
    if requires_manual_identity(meeting):
        text = (
            f"[meeting:{meeting['id']}] Background recording needs details\n"
            f"*{meeting['title']}* — {date} — account pending\n\n"
            f"*Fireflies summary*\n{_summary(meeting)}\n\n"
            "Fireflies captured a transcript but no identifying external "
            "attendee. Reply in this thread using:\n"
            "```Company domain: example.com\n"
            "Contact: Jane Smith <jane@example.com>\n"
            "Notes:\n"
            "Your manual notes here```\n"
            "You can send the mapping first and your notes in a later reply. "
            "Or reply `scrap` to ignore the recording and stop processing it."
        )
    else:
        text = (
            f"[meeting:{meeting['id']}] Manual notes requested\n"
            f"*{meeting['title']}* — {date} — {account}\n\n"
            f"*Fireflies summary*\n{_summary(meeting)}\n\n"
            "Reply in this thread with your manual call notes. I’ll combine them "
            "with the Fireflies transcript and process the meeting once. If no "
            "notes arrive, the transcript-only fallback runs after 12 hours. "
            "For an AI investment account, include `Type: AI Investment` on "
            "its own line."
        )
    if dry_run:
        return None
    if hasattr(monday, "prepare_call_notes_columns"):
        monday.prepare_call_notes_columns()
    prompt_ts = slack.post(channel, text)
    requested_at = requested_at or dt.datetime.now(dt.timezone.utc)
    monday.save_call_notes_prompt(
        str(meeting["id"]), str(prompt_ts), requested_at.isoformat())
    return str(prompt_ts)


def sync_call_note_threads(slack, monday, *, channel: str = "call-notes",
                           dry_run: bool = True) -> CallNotesResult:
    """Attach only new human replies from each meeting's exact Slack thread.

    The cursor advances only after the corresponding monday write succeeds.
    ``Slack Note IDs`` remains a second idempotency guard for retries.
    """
    meetings = monday.meetings_for_call_notes()
    result = CallNotesResult()
    for meeting in meetings:
        if meeting.get("status") not in ("Awaiting", "Ready", "Gated"):
            continue
        root_ts = str(meeting.get("call_notes_prompt_ts") or "")
        if not root_ts:
            continue
        last_ts = str(meeting.get("call_notes_last_ts") or root_ts)
        messages = slack.replies(channel, root_ts, oldest_ts=last_ts)
        result.scanned += len(messages)
        consumed = {
            value for value in (meeting.get("slack_note_ids") or "").splitlines()
            if value
        }
        human = []
        account_types = []
        highest_ts = last_ts
        for message in messages:
            ts = str(message.get("ts") or "")
            if ts and _newer(ts, highest_ts):
                highest_ts = ts
            text = (message.get("text") or "").strip()
            if (not ts or ts == root_ts or ts in consumed or not text or
                    message.get("bot_id") or message.get("subtype")):
                result.ignored += 1
                continue
            account_type = account_type_reply(text)
            note_text = manual_note_text(text)
            if not note_text and not account_type:
                result.ignored += 1
                continue
            if account_type:
                account_types.append(account_type)
            if note_text:
                prepared = dict(message)
                prepared["text"] = note_text
                human.append(prepared)
                result.matched += 1
                result.matches.append(NoteMatch(
                    ts, str(meeting["id"]), meeting.get("title") or ""))

        if dry_run:
            continue
        if account_types:
            requested_types = set(account_types)
            if len(requested_types) != 1:
                raise RuntimeError(
                    f"conflicting Account Type replies for meeting {meeting['id']}")
            account_id = str(meeting.get("prospect_account_id") or "")
            if not account_id:
                account_ids = [str(value) for value in
                               (meeting.get("account_ids") or [])]
                if len(account_ids) == 1:
                    account_id = account_ids[0]
            if not account_id:
                raise RuntimeError(
                    f"cannot apply Account Type before meeting {meeting['id']} "
                    "has one resolved prospect Account")
            monday.set_account_type(account_id, next(iter(requested_types)))
            result.account_types_updated += 1
        if human:
            existing_notes = (meeting.get("manual_notes") or "").strip()
            note_ids = list(consumed)
            blocks = []
            for message in sorted(human, key=lambda item: float(item["ts"])):
                timestamp = dt.datetime.fromtimestamp(
                    float(message["ts"]), dt.timezone.utc)
                blocks.append(
                    f"## Slack note — {timestamp.isoformat()}\n"
                    f"{(message.get('text') or '').strip()}"
                )
                note_ids.append(str(message["ts"]))
            combined = "\n\n".join(
                value for value in [existing_notes, *blocks] if value)
            monday.save_call_notes(
                str(meeting["id"]), combined, sorted(set(note_ids), key=float),
                last_ts=highest_ts,
            )
            result.attached += len(human)
        elif _newer(highest_ts, last_ts):
            monday.save_call_notes_cursor(str(meeting["id"]), highest_ts)
    return result


def release_transcript_fallbacks(monday, *, hours: int = 12,
                                 now: dt.datetime | None = None,
                                 dry_run: bool = True) -> list[str]:
    """Release meetings whose manual-note wait has expired."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    released = []
    for meeting in monday.meetings_for_call_notes():
        if meeting.get("status") not in ("Awaiting", "Ready", "Gated"):
            continue
        if meeting.get("notes_status") in ("Received", "Fallback"):
            continue
        requested = _parse_datetime(meeting.get("notes_requested_at") or "")
        if not requested or now - requested < dt.timedelta(hours=hours):
            continue
        released.append(str(meeting["id"]))
        if not dry_run:
            monday.mark_notes_fallback(str(meeting["id"]))
    return released


def attach_call_notes(*args, **kwargs):
    """Compatibility alias for callers of the old static history scanner."""
    kwargs.pop("days", None)
    kwargs.pop("now", None)
    return sync_call_note_threads(*args, **kwargs)


def reasoning_content(transcript: str, manual_notes: str = "") -> str:
    transcript = (transcript or "").strip()
    notes = (manual_notes or "").strip()
    if not notes:
        return transcript
    return f"{transcript}\n\n# Human call notes\n{notes}".strip()


def _newer(left: str, right: str) -> bool:
    try:
        return float(left) > float(right)
    except (TypeError, ValueError):
        return left > right


def _parse_datetime(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _email_lines(value: str) -> list[str]:
    return [part.strip() for part in str(value).replace(",", "\n").splitlines()
            if part.strip()]


def _summary(meeting: dict) -> str:
    summary = (meeting.get("fireflies_summary") or "").strip()
    return summary[:2500] if summary else "Summary not available yet."


def requires_manual_identity(meeting: dict) -> bool:
    """True for an unresolved solo/background or free-mail-only recording."""
    emails = _email_lines(meeting.get("attendee_emails") or "")
    buckets = parse_domains(emails)
    internal_people = sum(
        1 for email in emails
        if classify_domain(domain_of(email) or "") == "harbor"
    )
    return (not buckets["external"] and
            (bool(buckets["freemail"]) or internal_people <= 1))


def manual_note_text(text: str) -> str:
    """Return actual note content, excluding mapping and scrap controls."""
    value = (text or "").strip()
    if not value or is_scrap_command(value):
        return ""
    notes = re.search(r"(?is)(?:^|\n)\s*notes\s*:\s*(.*)$", value)
    if notes:
        value = notes.group(1).strip()
    value = re.sub(
        r"(?im)^\s*(?:account\s+)?type\s*:\s*ai\s+investment\s*$\n?",
        "", value,
    ).strip()
    if re.search(
            r"(?im)^\s*(?:company(?:\s+domain)?|domain|contact(?:\s+\d+)?)\s*:",
            value):
        return ""
    return value


def account_type_reply(text: str) -> str | None:
    """Return the deterministic Account Type explicitly supplied in a reply."""
    if re.search(
            r"(?im)^\s*(?:account\s+)?type\s*:\s*ai\s+investment\s*$",
            text or ""):
        return "AI Investment"
    return None
