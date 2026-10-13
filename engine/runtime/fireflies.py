"""Read-only Fireflies GraphQL client for meeting ingestion.

Fireflies is the source of transcript text and attendee emails. The client does
not write to Fireflies and does not perform any LLM work.
"""
from __future__ import annotations

import os
import re
import time

import requests


API = "https://api.fireflies.ai/graphql"
_TEAM_NAMES_BY_EMAIL = {
    "alex@harboradvisors.example": "Alex Mexicotte",
    "sam@harboradvisors.example": "Sam Reyes",
}

_LIST_QUERY = """
query Transcripts($from: DateTime, $to: DateTime, $limit: Int, $skip: Int) {
  transcripts(fromDate: $from, toDate: $to, limit: $limit, skip: $skip) {
    id title date transcript_url participants organizer_email
    meeting_attendees { displayName email }
    summary { overview short_summary bullet_gist action_items }
  }
}
"""

_DETAIL_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id title date transcript_url participants organizer_email
    meeting_attendees { displayName email }
    summary { overview short_summary bullet_gist action_items }
    sentences { speaker_name text start_time end_time }
  }
}
"""


class Fireflies:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("FIREFLIES_API_KEY", "")
        if not self.token:
            raise RuntimeError("missing required env var: FIREFLIES_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    def _post(self, query: str, variables: dict) -> dict:
        last_error = None
        for attempt in range(4):
            try:
                response = self.session.post(
                    API, json={"query": query, "variables": variables}, timeout=90)
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(float(response.headers.get("Retry-After", 1 + attempt)))
                    continue
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise RuntimeError(f"Fireflies GraphQL: {payload['errors']}")
                return payload["data"]
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(1 + attempt)
        raise RuntimeError(f"Fireflies request failed: {last_error}")

    def list_transcripts(self, *, from_date: str, to_date: str | None = None,
                         page_size: int = 50) -> list[dict]:
        """Fetch all transcript metadata in a time range, following pagination."""
        rows, skip = [], 0
        while True:
            page = self._post(_LIST_QUERY, {
                "from": from_date, "to": to_date,
                "limit": page_size, "skip": skip,
            }).get("transcripts") or []
            rows.extend(page)
            if len(page) < page_size:
                return rows
            skip += len(page)

    def get_transcript(self, transcript_id: str) -> dict:
        row = self._post(_DETAIL_QUERY, {"id": transcript_id}).get("transcript")
        if not row:
            raise RuntimeError(f"Fireflies transcript not found: {transcript_id}")
        return row


def transcript_text(transcript: dict, *, external_attendee_names: list[str] | None = None,
                    identity_audit: dict | None = None) -> str:
    """Render sentences while correcting impossible Fireflies speaker labels.

    Fireflies sometimes assigns an absent Harbor employee's name to the only
    external speaker. Exact attendee emails are authoritative; diarization is
    not. When there is exactly one resolved external attendee, any label that
    does not belong to an actual attendee is deterministically rebound to that
    external person. Ambiguous multi-contact calls keep the raw label.
    """
    external = [str(value).strip() for value in external_attendee_names or []
                if str(value).strip()]
    attendee_names = [
        str(row.get("displayName") or "").strip()
        for row in transcript.get("meeting_attendees") or []
        if str(row.get("displayName") or "").strip()
    ]
    attendee_names.extend(
        _TEAM_NAMES_BY_EMAIL[email] for email in attendee_emails(transcript)
        if email in _TEAM_NAMES_BY_EMAIL and
        _TEAM_NAMES_BY_EMAIL[email] not in attendee_names)
    attendee_names.extend(
        value for value in external if value not in attendee_names)
    known = {_person_key(value) for value in attendee_names}
    sole_external = external[0] if len({_person_key(value) for value in external}) == 1 else ""
    corrections: dict[str, dict[str, object]] = {}
    lines = []
    for sentence in transcript.get("sentences") or []:
        text = (sentence.get("text") or "").strip()
        if not text:
            continue
        speaker = (sentence.get("speaker_name") or "Speaker").strip()
        if (sole_external and not _is_generic_speaker(speaker) and
                _person_key(speaker) not in known):
            correction = corrections.setdefault(speaker, {
                "corrected_to": sole_external, "utterances": 0,
                "reason": "Fireflies label was not an attendee; exactly one external attendee resolved by email",
            })
            correction["utterances"] = int(correction["utterances"]) + 1
            speaker = sole_external
        lines.append(f"{speaker}: {text}")
    if identity_audit is not None:
        identity_audit.update({
            "attendee_names": attendee_names,
            "external_attendee_names": external,
            "speaker_label_corrections": corrections,
        })
    return "\n".join(lines)


def _person_key(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("-", " ").split())


def _is_generic_speaker(value: str) -> bool:
    key = _person_key(value)
    return bool(re.fullmatch(r"(?:speaker|unknown)(?:\s+\d+)?", key))


def attendee_emails(transcript: dict) -> list[str]:
    """Return normalized, deduplicated attendee emails from both API fields."""
    values = list(transcript.get("participants") or [])
    values.extend(attendee.get("email") for attendee in
                  (transcript.get("meeting_attendees") or []))
    organizer = transcript.get("organizer_email")
    if organizer:
        values.append(organizer)
    out = []
    for value in values:
        email = (value or "").strip().lower()
        if "@" in email and email not in out:
            out.append(email)
    return out


def attendee_details(transcript: dict) -> dict[str, str]:
    """Return Fireflies display names keyed by normalized attendee email."""
    details = {}
    for attendee in transcript.get("meeting_attendees") or []:
        email = str(attendee.get("email") or "").strip().lower()
        name = str(attendee.get("displayName") or "").strip()
        if "@" in email and name:
            details[email] = name
    return details


def fireflies_summary(transcript: dict) -> str:
    """Choose the best available native Fireflies meeting summary."""
    summary = transcript.get("summary") or {}
    for key in ("overview", "short_summary", "bullet_gist"):
        value = (summary.get(key) or "").strip()
        if value:
            return value
    return ""
