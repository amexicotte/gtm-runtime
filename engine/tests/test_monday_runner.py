"""End-to-end polling orchestration tests with no network calls."""
import os
import sys
import datetime as dt
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.monday_runner import (  # noqa: E402
    _manual_notes_integrity_error,
    _processing_claim_is_stale,
    run_monday_poll,
)


def test_manual_notes_integrity_blocks_truncated_cache():
    assert _manual_notes_integrity_error({
        "manual_notes": "abc",
        "manual_notes_chars": "4",
        "manual_notes_sha256": "",
    }) == "the manual-note cache contains 3 of the expected 4 characters"
    assert _manual_notes_integrity_error({
        "manual_notes": "abc", "manual_notes_chars": "",
        "manual_notes_sha256": (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    }) == ""


class FakeCRM:
    def __init__(self, meeting):
        self.meetings = meeting if isinstance(meeting, list) else [meeting]
        self.prompts = []
        self.changes = []

    def existing_fireflies_ids(self): return {"ff1"}
    def existing_fireflies_rows(self): return []
    def meetings_for_call_notes(self): return self.meetings
    def save_call_notes_prompt(self, meeting_id, prompt_ts, requested_at):
        row = next(item for item in self.meetings if item["id"] == meeting_id)
        row["call_notes_prompt_ts"] = prompt_ts
        row["call_notes_last_ts"] = prompt_ts
        row["notes_requested_at"] = requested_at
        row["notes_status"] = "Awaiting"
    def save_call_notes_cursor(self, meeting_id, last_ts): pass
    def mark_notes_fallback(self, meeting_id): pass
    def change_columns(self, board, item, values):
        self.changes.append((board, item, values))


class FakeSlack:
    def __init__(self): self.posts = []
    def post(self, channel, text, thread_ts=None):
        self.posts.append((channel, text, thread_ts))
        return "100.0"
    def replies(self, channel, root, oldest_ts=None): return []


class FakeFireflies:
    def list_transcripts(self, **kwargs): return []
    def get_transcript(self, transcript_id):
        return {"sentences": [{"speaker_name": "Jane", "text": "Transcript"}]}


def meeting(notes_status="Awaiting"):
    return {
        "id": "m1", "title": "Lumen", "date": "2026-07-31",
        "status": "Ready", "attendee_emails": "x@lumen.example",
        "account_ids": ["a1"], "account_names": ["Lumen"],
        "manual_notes": "Budget approved" if notes_status == "Received" else "",
        "slack_note_ids": "", "call_notes_prompt_ts": "100.0",
        "call_notes_last_ts": "100.0", "notes_requested_at": "",
        "notes_status": notes_status, "fireflies_id": "ff1",
        "prospect_account_id": "", "partner_account_ids": [],
        "contact_ids": ["c1"], "deal_ids": [],
    }


def test_waiting_notes_never_calls_reasoner():
    calls = []
    stats = run_monday_poll(
        crm=FakeCRM(meeting()), slack=FakeSlack(), fireflies=FakeFireflies(),
        reasoner=lambda **kwargs: calls.append(kwargs),
    )
    assert calls == []
    assert stats.meetings_waiting == 1 and stats.meetings_reasoned == 0


def test_received_notes_release_exactly_one_combined_reasoning_call():
    calls = []
    stats = run_monday_poll(
        crm=FakeCRM(meeting("Received")), slack=FakeSlack(),
        fireflies=FakeFireflies(), reasoner=lambda *args, **kwargs: calls.append(kwargs),
    )
    assert len(calls) == 1
    assert calls[0]["manual_notes"] == "Budget approved"
    assert calls[0]["notes_status"] == "Received"
    assert calls[0]["ctx"]["prospect_account_id"] == "a1"
    assert stats.meetings_reasoned == 1


def test_processing_meeting_resumes_cached_result_without_fetching_transcript():
    row = meeting("Received")
    row.update({
        "status": "Processing",
        "reasoning_result": {"meeting": {"summary": "Cached"}},
    })
    reasoner_calls, resume_calls = [], []

    class NoDetailFireflies(FakeFireflies):
        def get_transcript(self, transcript_id):
            raise AssertionError("cached recovery must not fetch the transcript")

    stats = run_monday_poll(
        crm=FakeCRM(row), slack=FakeSlack(), fireflies=NoDetailFireflies(),
        reasoner=lambda *args, **kwargs: reasoner_calls.append(kwargs),
        resumer=lambda *args, **kwargs: resume_calls.append(kwargs) or kwargs["result"],
    )
    assert reasoner_calls == []
    assert len(resume_calls) == 1
    assert resume_calls[0]["result"]["meeting"]["summary"] == "Cached"
    assert stats.meetings_reasoned == 0 and stats.meetings_resumed == 1


def test_activity_failure_leaves_cached_meeting_resumable_without_model_recall():
    row = meeting("Received")
    evidence = {"excerpt": "Send recap", "confidence": .99,
                "source_id": "m1", "occurred_at": "2026-08-17T00:00:00+00:00"}
    result = {"relevant": True, "meeting": {"action_items": {
        "status": "ANSWERED", "value": ["Send recap"], "evidence": evidence}}}

    class ActivityCRM(FakeCRM):
        def __init__(self, row):
            super().__init__(row); self.fail = True; self.created = []
        def existing_activity_titles(self, meeting): return set()
        def item_names(self, ids): return []
        def owner_ids_for_context(self, **kwargs): return ["owner"]
        def create_activity(self, **kwargs):
            if self.fail:
                raise RuntimeError("response lost")
            self.created.append(kwargs)
            return "t1"

    crm = ActivityCRM(row)
    model_calls = []
    first = run_monday_poll(
        crm=crm, slack=FakeSlack(), fireflies=FakeFireflies(),
        reasoner=lambda *args, **kwargs: model_calls.append(1) or result)
    assert first.meeting_errors == 1 and model_calls == [1]
    assert crm.changes[-1] == (
        "meeting", "m1", {"Processing Status": "Processing"})

    row["status"] = "Processing"
    row["reasoning_result"] = result
    crm.fail = False
    resume_calls = []
    second = run_monday_poll(
        crm=crm, slack=FakeSlack(), fireflies=FakeFireflies(),
        reasoner=lambda *args, **kwargs: model_calls.append(1),
        resumer=lambda *args, **kwargs: resume_calls.append(1) or result)
    assert second.meetings_resumed == 1 and resume_calls == [1]
    assert model_calls == [1] and len(crm.created) == 1


def test_empty_transcript_is_deferred_without_blocking_later_meetings():
    empty = meeting("Received")
    empty.update({
        "id": "m0", "title": "Empty", "fireflies_id": "ff0",
        "notes_requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    valid = meeting("Received")
    calls = []

    class OneEmptyTranscript(FakeFireflies):
        def get_transcript(self, transcript_id):
            if transcript_id == "ff0":
                return {"sentences": []}
            return super().get_transcript(transcript_id)

    stats = run_monday_poll(
        crm=FakeCRM([empty, valid]), slack=FakeSlack(),
        fireflies=OneEmptyTranscript(),
        reasoner=lambda *args, **kwargs: calls.append(kwargs),
    )
    assert len(calls) == 1
    assert calls[0]["meeting_title"] == "Lumen"
    assert stats.meetings_skipped_no_transcript == 1
    assert stats.meetings_reasoned == 1 and stats.meeting_errors == 0


def test_old_empty_transcript_is_flagged_once_instead_of_retried_forever():
    row = meeting("Fallback")
    row.update({
        "id": "m0", "title": "Empty", "fireflies_id": "ff0",
        "notes_requested_at": "2026-07-31T12:00:00+00:00",
    })

    class EmptyTranscript(FakeFireflies):
        def get_transcript(self, transcript_id): return {"sentences": []}

    crm, slack = FakeCRM(row), FakeSlack()
    stats = run_monday_poll(
        crm=crm, slack=slack, fireflies=EmptyTranscript(),
        reasoner=lambda *args, **kwargs: None,
    )
    assert stats.meetings_flagged_no_transcript == 1
    assert ("meeting", "m0", {"Processing Status": "Processed"}) in crm.changes
    assert slack.posts[-1][2] == "100.0"


def test_one_reasoning_error_does_not_abort_later_meetings():
    failed = meeting("Received")
    failed.update({"id": "m0", "title": "Broken", "fireflies_id": "ff0"})
    valid = meeting("Received")
    calls = []

    def reasoner(*args, **kwargs):
        calls.append(kwargs["meeting_title"])
        if kwargs["meeting_title"] == "Broken":
            raise RuntimeError("one bad meeting")

    stats = run_monday_poll(
        crm=FakeCRM([failed, valid]), slack=FakeSlack(),
        fireflies=FakeFireflies(), reasoner=reasoner,
    )
    assert calls == ["Broken", "Lumen"]
    assert stats.meeting_errors == 1 and stats.meetings_reasoned == 1


def test_stale_processing_claim_without_cache_is_released_and_retried():
    row = meeting("Received")
    row.update({
        "status": "Processing",
        "reasoning_result": {},
        "processing_started_at": "2026-07-31T12:00:00+00:00",
    })
    crm = FakeCRM(row)
    calls = []
    stats = run_monday_poll(
        crm=crm, slack=FakeSlack(), fireflies=FakeFireflies(),
        reasoner=lambda *args, **kwargs: calls.append(kwargs),
    )
    assert len(calls) == 1 and stats.meetings_reasoned == 1
    assert crm.changes[0] == (
        "meeting", "m1", {
            "Processing Status": "Ready", "Processing Started At": ""})


def test_fresh_processing_claim_is_not_retried():
    now = dt.datetime(2026, 8, 6, 20, 0, tzinfo=dt.timezone.utc)
    assert not _processing_claim_is_stale(
        "2026-08-06T19:45:01+00:00", 30, now=now)
    assert _processing_claim_is_stale(
        "2026-08-06T19:29:59+00:00", 30, now=now)


def test_manual_slack_meeting_uses_manual_reasoner_without_fireflies():
    row = meeting("Received")
    row.update({
        "title": "Manual call — Acme", "fireflies_id": "",
        "slack_intake_ts": "90.0", "manual_notes": "Met Jane at Acme",
    })

    class ManualCRM(FakeCRM):
        def existing_slack_intake_ids(self): return {"90.0"}

    class ManualSlack(FakeSlack):
        def history(self, channel, oldest_ts=None): return []

    standard_calls, manual_calls = [], []
    stats = run_monday_poll(
        crm=ManualCRM(row), slack=ManualSlack(), fireflies=FakeFireflies(),
        reasoner=lambda *args, **kwargs: standard_calls.append(kwargs),
        manual_reasoner=lambda *args, **kwargs: (
            manual_calls.append(kwargs) or SimpleNamespace(
                result={"meeting": {"action_items": []}},
                ctx={"prospect_account_id": "a1", "contact_ids": [],
                     "deal_id": None},
            )
        ),
    )
    assert standard_calls == [] and len(manual_calls) == 1
    assert manual_calls[0]["notes"] == "Met Jane at Acme"
    assert stats.manual_meetings_reasoned == 1 and stats.meetings_reasoned == 1


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
