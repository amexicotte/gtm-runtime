"""Fireflies parsing and idempotent sync tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.fireflies import (  # noqa: E402
    attendee_details, attendee_emails, fireflies_summary, transcript_text)
from runtime.fireflies_sync import sync_recent  # noqa: E402


def row(item_id, title, timestamp, participants):
    return {"id": item_id, "title": title, "date": timestamp,
            "transcript_url": f"https://fireflies.ai/{item_id}",
            "participants": participants, "meeting_attendees": []}


class FakeFireflies:
    def __init__(self, rows):
        self.rows = rows

    def list_transcripts(self, **kwargs):
        return self.rows


class FakeMonday:
    def __init__(self, existing=None, existing_rows=None):
        self.existing = set(existing or [])
        self.existing_rows = existing_rows
        self.created = []

    def existing_fireflies_ids(self):
        return self.existing

    def existing_fireflies_rows(self):
        return self.existing_rows or []

    def create_meeting_from_fireflies(self, transcript, **kwargs):
        self.created.append((transcript, kwargs))
        return str(len(self.created))


def test_attendees_merge_and_dedupe_api_fields():
    transcript = {
        "participants": ["JANE@ACME.COM"], "organizer_email": "alex@harboradvisors.example",
        "meeting_attendees": [{"email": "jane@acme.com"}, {"email": "joe@acme.com"}],
    }
    assert attendee_emails(transcript) == [
        "jane@acme.com", "joe@acme.com", "alex@harboradvisors.example",
    ]


def test_attendee_display_names_are_keyed_by_exact_email():
    assert attendee_details({"meeting_attendees": [
        {"displayName": "Jane Smith", "email": "JANE@ACME.COM"},
    ]}) == {"jane@acme.com": "Jane Smith"}


def test_transcript_text_has_speaker_labels():
    assert transcript_text({"sentences": [
        {"speaker_name": "Jane", "text": "Hello"},
        {"speaker_name": "Alex", "text": "Hi there"},
    ]}) == "Jane: Hello\nAlex: Hi there"


def test_impossible_speaker_label_rebinds_to_only_external_attendee():
    audit = {}
    rendered = transcript_text({
        "meeting_attendees": [
            {"displayName": "Avery Lin", "email": "albert@client.com"},
            {"displayName": "Sam Reyes", "email": "sam@harboradvisors.example"},
        ],
        "sentences": [
            {"speaker_name": "Alex Mexicotte", "text": "Client context"},
            {"speaker_name": "Sam Reyes", "text": "Harbor response"},
        ],
    }, external_attendee_names=["Avery Lin"], identity_audit=audit)
    assert rendered == "Avery Lin: Client context\nSam Reyes: Harbor response"
    assert audit["speaker_label_corrections"]["Alex Mexicotte"]["utterances"] == 1


def test_generic_speaker_labels_remain_neutral():
    audit = {}
    rendered = transcript_text({
        "meeting_attendees": [
            {"displayName": "Justin", "email": "justin@client.com"},
            {"displayName": None, "email": "alex@harboradvisors.example"},
        ],
        "sentences": [
            {"speaker_name": "Speaker 1", "text": "One side"},
            {"speaker_name": "Speaker 2", "text": "The other side"},
        ],
    }, external_attendee_names=["Justin"], identity_audit=audit)
    assert rendered == "Speaker 1: One side\nSpeaker 2: The other side"
    assert audit["speaker_label_corrections"] == {}


def test_native_summary_prefers_overview():
    assert fireflies_summary({"summary": {
        "overview": "Full overview", "short_summary": "Short",
    }}) == "Full overview"


def test_sync_dedupes_team_copies_and_does_not_write_in_dry_run():
    timestamp = 1785502800000  # 2026-07-31
    rows = [
        row("ff1", "Acme Intro", timestamp, ["jane@acme.com"]),
        row("ff2", "Acme Intro", timestamp, ["jane@acme.com"]),
    ]
    monday = FakeMonday()
    result = sync_recent(
        FakeFireflies(rows), monday, days=2, dry_run=True,
        now=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert result.fetched == 2 and result.canonical == 1 and result.duplicates == 1
    assert len(result.candidates) == 1 and monday.created == []


def test_sync_skips_existing_id_and_creates_only_new():
    timestamp = 1785502800000
    rows = [
        row("old", "Old", timestamp, ["old@one.com"]),
        row("new", "New", timestamp, ["new@two.com"]),
    ]
    monday = FakeMonday(existing={"old"})
    result = sync_recent(
        FakeFireflies(rows), monday, days=2, dry_run=False,
        now=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert result.existing == 1 and result.created == 1
    assert monday.created[0][0]["id"] == "new"


def test_latest_only_dedupes_team_copies_before_selecting_one():
    older = 1785416400000
    latest = 1785502800000
    rows = [
        row("lumen-alex", "Lumen", latest, ["alex@harboradvisors.example", "x@lumen.example"]),
        row("lumen-sam", "Lumen", latest, ["sam@harboradvisors.example", "x@lumen.example"]),
        row("older", "Older meeting", older, ["old@one.com"]),
    ]
    # Team copies can have different internal attendees, but matching title/date
    # still collapses them before the one-meeting limit is applied.
    monday = FakeMonday()
    result = sync_recent(
        FakeFireflies(rows), monday, days=2, dry_run=False, max_meetings=1,
        now=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert result.duplicates == 1 and result.created == 1
    assert monday.created[0][0]["title"] == "Lumen"


def test_later_team_copy_is_deduped_against_existing_monday_meeting():
    timestamp = 1785502800000
    monday = FakeMonday(existing_rows=[{
        "url": "alex-copy", "title": "Lumen weekly", "date": "2026-07-31",
        "invited": ["alex@harboradvisors.example", "x@lumen.example"],
    }])
    result = sync_recent(
        FakeFireflies([row(
            "sam-copy", "Lumen weekly", timestamp,
            ["sam@harboradvisors.example", "x@lumen.example"],
        )]),
        monday, days=2, dry_run=False,
        now=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert result.existing == 1 and result.created == 0
    assert monday.created == []


def test_single_attendee_background_recording_is_not_cross_run_duplicate():
    timestamp = 1785954300000
    monday = FakeMonday(existing_rows=[{
        "url": "earlier", "title": "Beacon recruiting", "date": "2026-08-05",
        "invited": ["alex@harboradvisors.example"],
    }])
    result = sync_recent(
        FakeFireflies([row(
            "background", "Partner recruitment", timestamp,
            ["alex@harboradvisors.example"],
        )]),
        monday, days=2, dry_run=False,
        now=dt.datetime(2026, 8, 5, 22, tzinfo=dt.timezone.utc),
    )
    assert result.existing == 0 and result.created == 1
    assert monday.created[0][0]["id"] == "background"


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
