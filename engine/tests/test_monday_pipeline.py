"""Verify the reasoning bridge makes one call and hands the result to writer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.monday_pipeline import reason_and_write, resume_reasoning_write  # noqa: E402


class FakeMonday:
    def __init__(self):
        self.changes = []

    def change_columns(self, board, item, values):
        self.changes.append((board, item, values))


def test_exactly_one_call_then_final_write():
    calls, writes = [], []

    def extractor(transcript, title, date, ctx=None):
        calls.append((transcript, title, date, ctx))
        return {"relevant": True, "meeting": {"summary": "Clear next steps"}}

    def applier(result, ctx):
        writes.append((result, ctx))

    crm = FakeMonday()
    ctx = {"meeting_id": "7", "prospect_account_id": "8",
           "partner_account_id": None, "deal_id": None, "contact_ids": []}
    result = reason_and_write(
        crm, transcript="A real transcript", meeting_title="Acme intro",
        meeting_date="2026-07-31", ctx=ctx, notes_status="Received",
        extractor=extractor, applier=applier,
    )
    assert len(calls) == 1
    assert writes == [(result, ctx)]
    assert crm.changes == [
        ("meeting", "7", {"Processing Status": "Processing"}),
        ("meeting", "7", {"Processing Status": "Processed"}),
    ]


def test_refuses_to_reason_without_resolved_prospect():
    try:
        reason_and_write(
            FakeMonday(), transcript="text", meeting_title="Internal",
            meeting_date="2026-07-31", ctx={"meeting_id": "7"},
            notes_status="Received",
        )
    except ValueError as error:
        assert "resolved prospect" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_refuses_to_reason_while_waiting_for_notes():
    try:
        reason_and_write(
            FakeMonday(), transcript="text", meeting_title="Acme",
            meeting_date="2026-07-31",
            ctx={"meeting_id": "7", "prospect_account_id": "8"},
            notes_status="Awaiting",
        )
    except ValueError as error:
        assert "waits for manual notes" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_persisted_result_resumes_writes_without_another_model_call():
    class DurableMonday(FakeMonday):
        def __init__(self):
            super().__init__()
            self.saved = []

        def begin_meeting_processing(self, meeting_id):
            self.changes.append(("meeting", meeting_id, {
                "Processing Status": "Processing"}))

        def save_reasoning_result(self, meeting_id, result):
            self.saved.append((meeting_id, result))

    crm = DurableMonday()
    ctx = {"meeting_id": "7", "prospect_account_id": "8",
           "partner_account_id": None, "deal_id": None, "contact_ids": []}
    result = {"relevant": True, "meeting": {"summary": "Durable result"}}

    def failed_write(_result, _ctx):
        raise RuntimeError("transient monday write failure")

    try:
        reason_and_write(
            crm, transcript="A real transcript", meeting_title="Acme intro",
            meeting_date="2026-07-31", ctx=ctx, notes_status="Received",
            extractor=lambda *args, **kwargs: result, applier=failed_write,
        )
    except RuntimeError as error:
        assert "transient" in str(error)
    else:
        raise AssertionError("expected write failure")
    assert crm.saved == [("7", result)]
    assert crm.changes[-1][2]["Processing Status"] == "Processing"

    writes = []
    assert resume_reasoning_write(
        crm, result=result, meeting_title="Acme intro", ctx=ctx,
        applier=lambda saved, saved_ctx: writes.append((saved, saved_ctx)),
    ) == result
    assert writes == [(result, ctx)]
    assert crm.changes[-1] == (
        "meeting", "7", {"Processing Status": "Processed"})


def test_irrelevant_meeting_still_routes_through_event_publisher():
    crm, published = FakeMonday(), []
    result = {"relevant": False, "_event": {"source": "meeting"}}
    resume_reasoning_write(
        crm, result=result, meeting_title="No material change",
        ctx={"meeting_id": "7", "prospect_account_id": "8"},
        applier=lambda *_args: (_ for _ in ()).throw(
            AssertionError("irrelevant fields must not write")),
        publisher=lambda *args, **kwargs: published.append((args, kwargs)),
    )
    assert len(published) == 1
    assert crm.changes[-1][2]["Processing Status"] == "Processed"


def test_identity_validation_drops_internal_contact_and_records_full_input_audit():
    class DurableMonday(FakeMonday):
        def __init__(self): super().__init__(); self.saved = []
        def save_reasoning_result(self, meeting, result): self.saved.append(result)

    crm = DurableMonday()
    ctx = {
        "meeting_id": "7", "prospect_account_id": "8", "deal_id": "won1",
        "contact_ids": ["c1"],
        "contact_email_ids": {"albert@quillr.example": "c1"},
        "crm_snapshot": {"account": {"Type": "Active Client"},
                         "contacts": [{"id": "c1", "name": "Avery Lin"}]},
    }
    model = {
        "relevant": True,
        "contacts": [
            {"email": "albert@quillr.example", "role_title": "CEO"},
            {"email": "sam@harboradvisors.example", "role_title": None},
        ],
        "deal": {"champion_name": "Albert Mexicotte"},
        "meeting": {"call_type": "Intro", "summary": "Context"},
    }
    result = reason_and_write(
        crm, transcript="full transcript", manual_notes="full human notes",
        meeting_title="Quillr", meeting_date="2026-08-04", ctx=ctx,
        notes_status="Received", extractor=lambda *args, **kwargs: model,
        applier=lambda *args: None,
    )
    assert [row["email"] for row in result["contacts"]] == [
        "albert@quillr.example"]
    assert result["deal"]["champion_name"] == "c1"
    audit = result["_audit"]
    assert audit["input"]["manual_notes_chars"] == len("full human notes")
    assert audit["crm_snapshot"]["account"]["Type"] == "Active Client"
    assert len(audit["validation_warnings"]) == 3


def test_business_event_is_cached_before_narrative_effects_and_uses_no_second_call():
    class DurableMonday(FakeMonday):
        def __init__(self):
            super().__init__(); self.saved = []
        def save_reasoning_result(self, meeting, result):
            self.saved.append((meeting, result))

    crm = DurableMonday()
    ctx = {
        "meeting_id": "m1", "prospect_account_id": "a1", "deal_id": "d1",
        "contact_ids": ["c1"], "attendee_emails": ["jane@acme.com"],
    }
    model_calls, order = [], []
    model = {
        "relevant": True,
        "timeline_event": {
            "headline": "Scope confirmed",
            "body": "Jane confirmed the discovery scope and asked Harbor to send the preparation document before Friday's planning session.",
            "labels": ["Decision", "Next step"],
        },
        "deal": {"deal_value": {
            "status": "ANSWERED", "value": 10000,
            "evidence": {"excerpt": "ten thousand", "confidence": 0.95},
        }},
    }

    def applier(result, saved_ctx):
        assert crm.saved and result.get("_event")
        order.append("fields")

    def publisher(saved_crm, result, activation_at=""):
        assert saved_crm is crm and crm.saved and result.get("_event")
        assert result["deal"]["deal_value"]["evidence"]["source_id"]
        order.append("narrative")

    result = reason_and_write(
        crm, transcript="The fee is ten thousand for the scope.", meeting_title="Acme scope",
        meeting_date="2026-08-17", ctx=ctx, notes_status="Fallback",
        extractor=lambda *args, **kwargs: model_calls.append(args) or model,
        applier=applier, publisher=publisher,
    )
    assert len(model_calls) == 1
    assert order == ["fields", "narrative"]
    assert crm.saved[0][1] is result
    assert crm.changes[-1] == (
        "meeting", "m1", {"Processing Status": "Processed"})


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
