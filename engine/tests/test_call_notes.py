"""Exact Slack thread matching, cursor, and prompt composition tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.call_notes import (  # noqa: E402
    account_type_reply,
    ensure_call_note_prompts,
    manual_note_text,
    reasoning_content,
    release_transcript_fallbacks,
    request_call_notes,
    sync_call_note_threads,
)


class FakeSlack:
    def __init__(self, messages=None):
        self.messages = messages or {}
        self.posts = []
        self.calls = []

    def replies(self, channel, root_ts, oldest_ts=None):
        self.calls.append((channel, root_ts, oldest_ts))
        return self.messages.get(root_ts, [])

    def post(self, channel, text):
        self.posts.append((channel, text))
        return "200.0"


class FakeMonday:
    def __init__(self, meetings):
        self.meetings = meetings
        self.saved = []
        self.prompts = []
        self.cursors = []
        self.account_types = []

    def meetings_for_call_notes(self):
        return self.meetings

    def save_call_notes(self, meeting_id, notes, note_ids, *, last_ts=None):
        self.saved.append((meeting_id, notes, note_ids, last_ts))

    def save_call_notes_prompt(self, meeting_id, prompt_ts, requested_at):
        self.prompts.append((meeting_id, prompt_ts, requested_at))

    def save_call_notes_cursor(self, meeting_id, last_ts):
        self.cursors.append((meeting_id, last_ts))

    def mark_notes_fallback(self, meeting_id):
        self.cursors.append((meeting_id, "fallback"))

    def set_account_type(self, account_id, account_type):
        self.account_types.append((account_id, account_type))


def target(**updates):
    row = {
        "id": "m1", "title": "Attio Set Up Alignment", "date": "2026-07-31",
        "status": "Ready",
        "attendee_emails": "alex@harboradvisors.example\nuser@lumenhq.example",
        "account_names": ["Lumen"], "manual_notes": "", "slack_note_ids": "",
        "fireflies_summary": "Fireflies says this was a CRM alignment call.",
        "call_notes_prompt_ts": "100.0", "call_notes_last_ts": "100.0",
    }
    row.update(updates)
    return row


def test_prompt_is_posted_once_and_timestamp_is_persisted():
    slack, monday = FakeSlack(), FakeMonday([])
    meeting = target(call_notes_prompt_ts="")
    ts = request_call_notes(
        slack, monday, meeting, dry_run=False,
        requested_at=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert ts == "200.0"
    assert "[meeting:m1]" in slack.posts[0][1]
    assert "Reply in this thread" in slack.posts[0][1]
    assert "Fireflies says this was a CRM alignment call" in slack.posts[0][1]
    assert monday.prompts == [("m1", "200.0", "2026-07-31T18:00:00+00:00")]

    already = target(call_notes_prompt_ts="99.0")
    assert request_call_notes(slack, monday, already, dry_run=False) == "99.0"
    assert len(slack.posts) == 1


def test_prompt_sweep_only_targets_unprompted_meetings():
    slack = FakeSlack()
    monday = FakeMonday([target(), target(id="m2", call_notes_prompt_ts="")])
    result = ensure_call_note_prompts(
        slack, monday, dry_run=False,
        requested_at=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
    )
    assert result.prompted == 1
    assert monday.prompts[0][0] == "m2"


def test_solo_background_recording_gets_identity_prompt_but_internal_team_call_does_not():
    slack = FakeSlack()
    monday = FakeMonday([
        target(
            id="background", status="Gated", call_notes_prompt_ts="",
            attendee_emails="alex@harboradvisors.example", account_names=[],
        ),
        target(
            id="internal", status="Gated", call_notes_prompt_ts="",
            attendee_emails=("alex@harboradvisors.example\n"
                             "sam@harboradvisors.example"), account_names=[],
        ),
    ])
    result = ensure_call_note_prompts(slack, monday, dry_run=False)
    assert result.prompted == 1
    assert monday.prompts[0][0] == "background"
    prompt = slack.posts[0][1]
    assert "Background recording needs details" in prompt
    assert "Company domain: example.com" in prompt
    assert "`scrap`" in prompt


def test_only_new_human_replies_in_exact_thread_are_attached():
    slack = FakeSlack({"100.0": [
        {"ts": "100.0", "text": "bot prompt", "bot_id": "B1"},
        {"ts": "101.0", "text": "Budget is approved.", "user": "U1"},
        {"ts": "102.0", "text": "automated", "bot_id": "B2"},
    ]})
    monday = FakeMonday([target()])
    result = sync_call_note_threads(slack, monday, dry_run=False)
    assert result.matched == 1 and result.attached == 1
    assert slack.calls == [("call-notes", "100.0", "100.0")]
    assert "Budget is approved" in monday.saved[0][1]
    assert monday.saved[0][2] == ["101.0"]
    assert monday.saved[0][3] == "102.0"


def test_all_human_replies_from_multiple_teammates_are_combined_in_order():
    slack = FakeSlack({"100.0": [
        {"ts": "100.0", "text": "bot prompt", "bot_id": "B1"},
        {"ts": "101.0", "text": "Sam's notes", "user": "SAM"},
        {"ts": "102.0", "text": "Alex's notes", "user": "ALEX"},
    ]})
    monday = FakeMonday([target()])
    result = sync_call_note_threads(slack, monday, dry_run=False)
    assert result.matched == 2 and result.attached == 2
    combined = monday.saved[0][1]
    assert combined.index("Sam's notes") < combined.index("Alex's notes")
    assert monday.saved[0][2] == ["101.0", "102.0"]
    assert monday.saved[0][3] == "102.0"


def test_stored_cursor_and_note_ids_prevent_rereads():
    slack = FakeSlack({"100.0": [
        {"ts": "101.0", "text": "Already stored", "user": "U1"},
        {"ts": "103.0", "text": "New detail", "user": "U1"},
    ]})
    monday = FakeMonday([target(
        call_notes_last_ts="101.0", slack_note_ids="101.0",
        manual_notes="Existing notes",
    )])
    result = sync_call_note_threads(slack, monday, dry_run=False)
    assert slack.calls[0][2] == "101.0"
    assert result.matched == 1
    assert monday.saved[0][2] == ["101.0", "103.0"]
    assert monday.saved[0][3] == "103.0"


def test_mapping_only_reply_advances_cursor_without_releasing_notes():
    slack = FakeSlack({"100.0": [{
        "ts": "101.0",
        "text": "Company domain: acme.com\nContact: Jane <jane@acme.com>",
        "user": "U1",
    }]})
    monday = FakeMonday([target(
        status="Gated", attendee_emails="alex@harboradvisors.example",
    )])
    result = sync_call_note_threads(slack, monday, dry_run=False)
    assert result.attached == 0 and monday.saved == []
    assert monday.cursors == [("m1", "101.0")]


def test_combined_mapping_reply_stores_only_text_after_notes_label():
    text = (
        "Company domain: acme.com\n"
        "Contact: Jane Smith <jane@acme.com>\n"
        "Notes:\nBudget is approved."
    )
    assert manual_note_text(text) == "Budget is approved."
    slack = FakeSlack({"100.0": [{"ts": "101.0", "text": text, "user": "U1"}]})
    monday = FakeMonday([target(
        status="Ready", attendee_emails="alex@harboradvisors.example\njane@acme.com",
    )])
    result = sync_call_note_threads(slack, monday, dry_run=False)
    assert result.attached == 1
    assert "Budget is approved" in monday.saved[0][1]
    assert "Company domain" not in monday.saved[0][1]


def test_ai_investment_type_reply_updates_only_the_prospect_account():
    text = "Type: AI Investment\nBudget is approved."
    slack = FakeSlack({"100.0": [{"ts": "101.0", "text": text, "user": "U1"}]})
    monday = FakeMonday([target(
        prospect_account_id="a1", account_ids=["a1", "partner-1"],
    )])

    result = sync_call_note_threads(slack, monday, dry_run=False)

    assert account_type_reply(text) == "AI Investment"
    assert result.account_types_updated == 1
    assert monday.account_types == [("a1", "AI Investment")]
    assert "Budget is approved" in monday.saved[0][1]
    assert "Type: AI Investment" not in monday.saved[0][1]

    assert manual_note_text(
        "Notes:\nBudget is approved.\nType: AI Investment"
    ) == "Budget is approved."


def test_type_only_reply_updates_account_and_advances_cursor_without_notes():
    slack = FakeSlack({"100.0": [{
        "ts": "101.0", "text": "Account Type: ai investment", "user": "U1",
    }]})
    monday = FakeMonday([target(prospect_account_id="a1", account_ids=["a1"])])

    result = sync_call_note_threads(slack, monday, dry_run=False)

    assert result.attached == 0 and result.account_types_updated == 1
    assert monday.account_types == [("a1", "AI Investment")]
    assert monday.saved == []
    assert monday.cursors == [("m1", "101.0")]


def test_manual_notes_are_appended_to_transcript_for_one_call():
    content = reasoning_content("Jane: Transcript", "Lumen notes")
    assert content == "Jane: Transcript\n\n# Human call notes\nLumen notes"


def test_fallback_releases_only_after_twelve_hours():
    monday = FakeMonday([
        target(id="old", notes_requested_at="2026-07-31T05:00:00+00:00",
               notes_status="Awaiting"),
        target(id="new", notes_requested_at="2026-07-31T12:00:00+00:00",
               notes_status="Awaiting"),
        target(id="done", notes_requested_at="2026-07-30T00:00:00+00:00",
               notes_status="Received"),
    ])
    released = release_transcript_fallbacks(
        monday, now=dt.datetime(2026, 7, 31, 18, tzinfo=dt.timezone.utc),
        dry_run=False,
    )
    assert released == ["old"]
    assert monday.cursors == [("old", "fallback")]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
