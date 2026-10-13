"""Evidence-gated email Activity reconciliation and retry tests."""
import datetime as dt
from dataclasses import replace
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.business_events import BusinessEvent  # noqa: E402
from runtime.email_reconciliation import (  # noqa: E402
    apply_claimed_email_event, prepare_email_result, validate_completion,
)


def event(*, attachments=True, candidates=None):
    content = ("[mail-1 | 2026-08-17T12:00:00+00:00 | outbound | "
               "from alex@harboradvisors.example]\nAttached is the onsite prep document.")
    return BusinessEvent(
        source="email", source_id="thread:abc",
        occurred_at="2026-08-17T12:00:00+00:00",
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        title="Onsite prep", account_ids=("a1",), contact_ids=("c1",),
        deal_ids=("d1",), content=content,
        attachments=({"type": "link", "url": "https://docs/prep"},) if attachments else (),
        open_activity_candidates=tuple(candidates or [{
            "id": "t1", "title": "Send onsite prep document",
            "unique_match": True, "requires_delivery_evidence": True,
            "related_ids": ["c1", "d1"],
        }]),
        captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def test_completion_requires_unique_outbound_verbatim_high_confidence_and_link():
    row = {"activity_id": "t1", "decision": "completed",
           "expects_client_response": True,
           "evidence": {"excerpt": "Attached is the onsite prep document.",
                        "confidence": 0.96}}
    valid, problem = validate_completion(
        row, candidates={"t1": event().open_activity_candidates[0]}, event=event(),
        confidence_threshold=0.9,
        outbound_sources=[("mail-1", "Attached is the onsite prep document.",
                           "2026-08-17T12:00:00+00:00", ("https://docs/prep",),
                           ("c1",), (("https://docs/prep",
                                      "Attached is the onsite prep document."),))])
    assert not problem and valid["activity_id"] == "t1"
    assert validate_completion(
        row, candidates={"t1": event(attachments=False).open_activity_candidates[0]},
        event=event(attachments=False), confidence_threshold=0.9,
        outbound_sources=[("mail-1", "Attached is the onsite prep document.",
                           "2026-08-17T12:00:00+00:00", (), ("c1",))])[0] is None


def test_delivery_link_must_be_on_the_same_outbound_message_as_completion():
    row = {"activity_id": "t1", "decision": "completed",
           "evidence": {"excerpt": "Attached is the onsite prep document.",
                        "confidence": 0.96}}
    valid, problem = validate_completion(
        row, candidates={"t1": event().open_activity_candidates[0]}, event=event(),
        confidence_threshold=0.9,
        outbound_sources=[
            ("mail-1", "Attached is the onsite prep document.",
             "2026-08-17T12:00:00+00:00", (), ("c1",)),
            ("mail-2", "Unrelated signature", "2026-08-17T13:00:00+00:00",
             ("https://linkedin.example/profile",), ("c1",)),
        ])
    assert valid is None and "requires a delivered attachment or link" in problem


def test_completion_recipient_must_match_related_contact():
    row = {"activity_id": "t1", "decision": "completed",
           "evidence": {"excerpt": "Attached is the onsite prep document.",
                        "confidence": 0.96}}
    valid, problem = validate_completion(
        row, candidates={"t1": event().open_activity_candidates[0]}, event=event(),
        confidence_threshold=0.9,
        outbound_sources=[(
            "mail-1", "Attached is the onsite prep document.",
            "2026-08-17T12:00:00+00:00", ("https://docs/prep",), ("c2",))])
    assert valid is None and "exact related Contact" in problem
    competing = dict(event().open_activity_candidates[0], unique_match=False)
    assert validate_completion(
        row, candidates={"t1": competing}, event=event(),
        confidence_threshold=0.9,
        outbound_sources=[("mail-1", "Attached is the onsite prep document.",
                           "2026-08-17T12:00:00+00:00", (), ("c1",))])[0] is None


def test_generic_website_link_cannot_prove_delivery():
    row = {"activity_id": "t1", "decision": "completed",
           "evidence": {"excerpt": "I sent it yesterday.", "confidence": .99}}
    delivery_event = replace(
        event(), content="I sent it yesterday.\nSee my website link: Harbor")
    valid, problem = validate_completion(
        row, candidates={"t1": delivery_event.open_activity_candidates[0]},
        event=delivery_event, confidence_threshold=.9,
        outbound_sources=[(
            "mail-1", delivery_event.content, "2026-08-17T12:00:00+00:00",
            ("https://harboradvisors.example",), ("c1",),
            (("https://harboradvisors.example", "See my website link: Harbor"),))])
    assert valid is None and "requires a delivered attachment or link" in problem


def test_prepare_blocks_nonverbatim_fields_and_ambiguous_deal():
    result = {
        "relevant": True,
        "timeline_event": {"headline": "Prep sent", "body": "Harbor sent the requested onsite preparation document to Jane.", "labels": ["Task completed"]},
        "deal": {"stage": {"status": "ANSWERED", "value": "Proposal",
                            "evidence": {"excerpt": "invented stage", "confidence": .99}}},
        "contacts": [{"email": "wrong@example.com", "role_title": {
            "status": "ANSWERED", "value": "VP",
            "evidence": {"excerpt": "Attached", "confidence": .9}}}],
        "activity_completions": [], "new_activities": [],
    }
    prepare_email_result(
        result, event=event(), contact_email_ids={"jane@acme.com": "c1"},
        ambiguous_deal=True, completion_confidence=.9,
        message_sources=[("mail-1", "Attached is the onsite prep document.",
                          "2026-08-17T12:00:00+00:00")],
        outbound_sources=[("mail-1", "Attached is the onsite prep document.",
                           "2026-08-17T12:00:00+00:00", ("https://docs/prep",),
                           ("c1",), (("https://docs/prep",
                                      "Attached is the onsite prep document."),))],
        inbound_sources=[])
    assert result["deal"] == {} and result["contacts"] == []
    assert result["_event"]["timeline_event"]["headline"] == "Prep sent"


def test_prepare_records_exact_latest_inbound_time():
    result = {"relevant": False, "timeline_event": None, "account": {},
              "deal": {}, "contacts": [], "activity_completions": [],
              "new_activities": []}
    prepare_email_result(
        result, event=event(), contact_email_ids={}, ambiguous_deal=False,
        completion_confidence=.9, message_sources=[], outbound_sources=[],
        inbound_sources=[
            ("mail-in-1", "First", "2026-08-17T10:00:00+00:00"),
            ("mail-in-2", "Second", "2026-08-17T11:00:00+00:00"),
        ])
    assert result["_reconciliation"]["last_inbound_at"] == \
        "2026-08-17T11:00:00+00:00"


class CRM:
    def __init__(self):
        self.effects, self.completed, self.created, self.updates = {}, [], [], []
        self.status = "Processing"
    def existing_activity_titles_for_event(self, key, related): return set()
    def item_names(self, ids): return ["Jane"]
    def owner_ids_for_context(self, **kwargs): return ["owner"]
    def create_activity(self, **kwargs): self.created.append(kwargs); return "new1"
    def complete_activity_from_event(self, item, **kwargs): self.completed.append((item, kwargs))
    def stamp_last_interaction(self, **kwargs): self.interaction = kwargs
    def active_client_project_for_deal(self, account, deal): return None
    def update_has_event_marker(self, item, effect): return False
    def post_narrative_update(self, item, body): self.updates.append((item, body)); return str(len(self.updates))
    def record_agent_event_effect(self, event_id, key, details): self.effects[key] = details
    def complete_agent_event(self, event_id): self.status = "Processed"
    def fail_agent_event(self, event_id, error): self.status = "Failed"


def test_cached_effects_make_email_apply_retry_safe():
    result = {
        "relevant": True,
        "timeline_event": {"headline": "Prep sent", "body": "Harbor sent the requested onsite preparation document to Jane.", "labels": ["Task completed"]},
        "account": {}, "deal": {}, "contacts": [],
        "activity_completions": [{"activity_id": "t1", "decision": "completed",
            "expects_client_response": True,
            "evidence": {"excerpt": "Attached is the onsite prep document.", "confidence": .96}}],
        "new_activities": [{"title": "Confirm Jane received prep", "due_date": "2026-08-19",
            "evidence": {"excerpt": "Attached is the onsite prep document.", "confidence": .9}}],
    }
    ev = prepare_email_result(
        result, event=event(), contact_email_ids={"jane@acme.com": "c1"},
        ambiguous_deal=False, completion_confidence=.9,
        message_sources=[("mail-1", "Attached is the onsite prep document.",
                          "2026-08-17T12:00:00+00:00")],
        outbound_sources=[("mail-1", "Attached is the onsite prep document.",
                           "2026-08-17T12:00:00+00:00", ("https://docs/prep",),
                           ("c1",), (("https://docs/prep",
                                      "Attached is the onsite prep document."),))],
        inbound_sources=[])
    crm = CRM()
    writes = []
    apply_claimed_email_event(
        crm, result=result, event=ev, agent_event_id="e1", effects={},
        ctx={"prospect_account_id": "a1", "contact_ids": ["c1"], "deal_id": "d1"},
        applier=lambda result, ctx: writes.append(1))
    first_effects = dict(crm.effects)
    apply_claimed_email_event(
        crm, result=result, event=ev, agent_event_id="e1", effects=first_effects,
        ctx={"prospect_account_id": "a1", "contact_ids": ["c1"], "deal_id": "d1"},
        applier=lambda result, ctx: writes.append(1))
    assert writes == [1]
    assert len(crm.created) == 1 and len(crm.completed) == 1
    assert crm.completed[0][1]["source_timeline_id"] == "mail-1"
    assert len(crm.updates) == 3


def test_missing_relevance_suppresses_all_model_derived_effects():
    result = {
        "timeline_event": {"headline": "Prep sent", "body": (
            "Harbor sent the requested onsite preparation document to Jane."),
            "labels": ["Task completed"]},
        "account": {}, "deal": {}, "contacts": [],
        "_validated_activity_completions": [{
            "activity_id": "t1", "occurred_at": event().occurred_at,
            "evidence": "sent", "confidence": .99,
            "source_timeline_id": "mail-1"}],
        "_validated_new_activities": [{"title": "Call Jane"}],
        "_reconciliation": {"last_inbound_at": ""},
    }
    crm, writes = CRM(), []
    apply_claimed_email_event(
        crm, result=result, event=event(), agent_event_id="e1", effects={},
        ctx={"prospect_account_id": "a1", "contact_ids": ["c1"],
             "deal_id": "d1"},
        applier=lambda *_args: writes.append(1))
    assert writes == [] and crm.created == [] and crm.completed == []
    assert crm.updates == []


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
