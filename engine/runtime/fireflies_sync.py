"""Idempotent Fireflies-to-monday Meeting ingestion."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from zoneinfo import ZoneInfo

from matcher import dedupe_meeting_rows, parse_domains, title_core

from .fireflies import attendee_emails


@dataclass
class SyncResult:
    fetched: int = 0
    canonical: int = 0
    duplicates: int = 0
    existing: int = 0
    created: int = 0
    candidates: list[dict] = field(default_factory=list)


def sync_recent(fireflies, monday, *, days: int = 2, dry_run: bool = True,
                now: dt.datetime | None = None,
                timezone: str = "America/New_York",
                max_meetings: int | None = None,
                not_before: str | None = None) -> SyncResult:
    """Pull a rolling window and create only unseen canonical Meeting rows."""
    zone = ZoneInfo(timezone)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    start = now.astimezone(dt.timezone.utc) - dt.timedelta(days=days)
    baseline = _parse_datetime(not_before)
    if baseline and baseline > start:
        start = baseline
    transcripts = fireflies.list_transcripts(
        from_date=start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        to_date=now.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"),
    )
    result = SyncResult(fetched=len(transcripts))

    by_id = {str(row["id"]): row for row in transcripts}
    rows = []
    for transcript in transcripts:
        meeting_date = _date_of(transcript, zone)
        rows.append({
            "url": str(transcript["id"]),
            "title": transcript.get("title") or "",
            "date": meeting_date,
            "invited": attendee_emails(transcript),
            "transcript": transcript.get("transcript_url"),
            "created": str(transcript.get("date") or ""),
        })
    canonical_rows, duplicate_groups = dedupe_meeting_rows(rows)
    canonical_rows.sort(
        key=lambda row: float(row.get("created") or 0), reverse=True)
    result.canonical = len(canonical_rows)
    result.duplicates = sum(len(group) - 1 for group in duplicate_groups)

    existing_ids = monday.existing_fireflies_ids()
    existing_rows = (monday.existing_fireflies_rows()
                     if hasattr(monday, "existing_fireflies_rows") else [])
    for row in canonical_rows:
        if row["url"] in existing_ids or _matches_existing(row, existing_rows):
            result.existing += 1
            continue
        if max_meetings is not None and len(result.candidates) >= max_meetings:
            break
        transcript = by_id[row["url"]]
        candidate = {
            "id": row["url"], "title": transcript.get("title") or "",
            "date": row["date"], "attendee_count": len(row["invited"]),
            "has_transcript_url": bool(transcript.get("transcript_url")),
        }
        result.candidates.append(candidate)
        if dry_run:
            continue
        meeting_id = monday.create_meeting_from_fireflies(
            transcript, date=row["date"], attendee_emails=row["invited"])
        candidate["monday_id"] = str(meeting_id)
        existing_rows.append(row)
        result.created += 1
    return result


def _date_of(transcript: dict, zone: ZoneInfo) -> str:
    milliseconds = transcript.get("date")
    if not milliseconds:
        return dt.datetime.now(zone).date().isoformat()
    return dt.datetime.fromtimestamp(float(milliseconds) / 1000, dt.timezone.utc
                                     ).astimezone(zone).date().isoformat()


def _matches_existing(candidate: dict, existing_rows: list[dict]) -> bool:
    """Apply the same cross-run duplicate rule used inside one Fireflies page."""
    candidate_invites = {
        value.lower() for value in (candidate.get("invited") or []) if value
    }
    candidate_title = title_core(candidate.get("title") or "")
    for row in existing_rows:
        if candidate.get("date") != row.get("date"):
            continue
        same_title = candidate_title and candidate_title == title_core(
            row.get("title") or "")
        invites = {value.lower() for value in (row.get("invited") or []) if value}
        # A background recording commonly contains only its recorder. Matching
        # that one address would suppress every distinct recording they made on
        # the same day, so invite-only matching requires at least two people.
        buckets = parse_domains(list(candidate_invites))
        same_invites = (len(candidate_invites) >= 2 and
                        bool(buckets["external"] or buckets["freemail"]) and
                        candidate_invites == invites)
        if same_title or same_invites:
            return True
    return False


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else parsed.replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        raise ValueError("FIREFLIES_NOT_BEFORE must be an ISO-8601 timestamp")
