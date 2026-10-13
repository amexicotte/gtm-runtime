"""End-to-end shadow runner tests with injected read-only adapters."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.email_events import ContactDiscovery  # noqa: E402
from runtime.email_runner import (  # noqa: E402
    EmailRunStats, _materialize_contact_discoveries, run_email_listening,
)
from runtime.config import config  # noqa: E402


class CRM:
    def __init__(self, *, automated=False):
        self.automated = automated
        self.mutations = []
    def email_contact_index(self):
        return {
            "jane@acme.com": {"id": "c1", "name": "Jane", "email": "jane@acme.com",
                              "account_ids": ["a1"], "deal_ids": ["d1"]},
            "bob@acme.com": {"id": "c2", "name": "Bob", "email": "bob@acme.com",
                             "account_ids": ["a1"], "deal_ids": ["d1"]},
        }
    def email_deal_index(self):
        return {"d1": {"id": "d1", "name": "Acme", "active": True,
                        "account_ids": ["a1"], "contact_ids": ["c1", "c2"],
                        "last_interaction_at": "2026-08-17T10:00:00+00:00",
                        "last_inbound_email_at": "", "nudge_ts": "", "nudge_key": ""}}
    def email_timeline_items(self, contact_id):
        sender = "no-reply@vendor.com" if self.automated else "jane@acme.com"
        return {"cursor": "", "items": [{
            "id": "mail-1", "type": "email", "title": "Proposal follow-up",
            "created_at": "2026-08-17T12:00:00Z", "content": "Can we meet Friday?",
            "metadata": {"from": sender,
                         "to": ["alex@harboradvisors.example", "bob@acme.com"],
                         "cc": [], "bcc": []},
        }]}
    def open_activity_candidates_for_entities(self, ids): return []
    def email_reasoning_context(self, **kwargs): return {"account": {"id": "a1"}}
    def followup_nudge_candidates(self): return []
    def claim_agent_event(self, *args, **kwargs): self.mutations.append("claim")


def test_shadow_dedupes_cross_contact_timeline_item_and_never_mutates():
    crm, calls = CRM(), []
    def reasoner(content, title, occurred, ctx=None):
        calls.append((content, title, ctx))
        return {"relevant": True,
                "timeline_event": {"headline": "Meeting requested",
                                   "body": "Jane asked Harbor to meet on Friday to continue the proposal discussion.",
                                   "labels": ["Next step"]},
                "account": {}, "deal": {}, "contacts": [],
                "new_activities": [], "activity_completions": []}
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm, reasoner=reasoner,
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contacts_scanned == 2
    assert stats.timeline_items_deduplicated == 1
    assert stats.threads_reasoned == 1 and len(calls) == 1
    assert stats.observation_eligible
    assert crm.mutations == []


def test_shadow_proposes_new_same_domain_participant_without_creating_contact():
    class DiscoveryCRM(CRM):
        def email_contact_index(self):
            return {
                "jane@acme.com": {"id": "c1", "name": "Jane",
                                   "email": "jane@acme.com",
                                   "account_ids": ["a1"], "deal_ids": ["d1"]},
            }
        def email_account_index(self):
            return {"acme.com": {"id": "a1", "name": "Acme"}}

    crm = DiscoveryCRM()
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00", crm=crm,
        reasoner=lambda *_args, **_kwargs: {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": []},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contact_discoveries_proposed == 1
    assert stats.contacts_created == 0
    assert crm.mutations == []


def test_live_contact_discovery_is_created_once_and_updates_run_indexes():
    class DiscoveryCRM:
        def __init__(self):
            self.created = []
        def find_contact_by_email(self, email):
            return None
        def create_contact(self, email, account_id, name=None, deal_ids=None):
            self.created.append((email, account_id, name, deal_ids))
            return "c-new"

    crm = DiscoveryCRM()
    contacts = {}
    deals = {"d1": {"id": "d1", "contact_ids": ["c1"]}}
    stats = EmailRunStats(mode="live")
    proposed = set()
    discovery = ContactDiscovery(
        email="bob@acme.com", name="Bob Buyer", account_id="a1",
        deal_ids=("d1",))
    _materialize_contact_discoveries(
        crm, [discovery], contacts=contacts, deals=deals, stats=stats,
        shadow=False, proposed_emails=proposed)
    _materialize_contact_discoveries(
        crm, [discovery], contacts=contacts, deals=deals, stats=stats,
        shadow=False, proposed_emails=proposed)
    assert crm.created == [("bob@acme.com", "a1", "Bob Buyer", ["d1"])]
    assert contacts["bob@acme.com"]["account_ids"] == ["a1"]
    assert deals["d1"]["contact_ids"] == ["c1", "c-new"]
    assert stats.contact_discoveries_proposed == 1
    assert stats.contacts_created == 1


def test_live_contact_discovery_conflict_fails_closed_without_duplicate():
    class ConflictCRM:
        def find_contact_by_email(self, email):
            return {"id": "ambiguous-existing"}
        def create_contact(self, *args, **kwargs):
            raise AssertionError("must not create a duplicate exact-email Contact")

    stats = EmailRunStats(mode="live")
    _materialize_contact_discoveries(
        ConflictCRM(), [ContactDiscovery(
            email="bob@acme.com", name="Bob", account_id="a1")],
        contacts={}, deals={}, stats=stats, shadow=False,
        proposed_emails=set())
    assert stats.contact_creation_conflicts == 1
    assert stats.contacts_created == 0


def test_shadow_prefetches_bounded_business_context_for_reasoning():
    crm, captured = CRM(), []
    context = type("Context", (), {
        "for_email": lambda self, snapshot: {
            "operating_brief": {"content": "Harbor operating context"},
            "context_audit": [{"tool": "get_operating_brief"}],
        },
    })()
    def reasoner(*_args, ctx=None, **_kwargs):
        captured.append(ctx["business_context"])
        return {"relevant": False, "timeline_event": {}, "account": {},
                "deal": {}, "contacts": [], "new_activities": [],
                "activity_completions": []}
    run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00", crm=crm,
        reasoner=reasoner, business_context=context,
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert captured[0]["operating_brief"]["content"] == "Harbor operating context"


def test_automated_only_shadow_is_a_noop_without_reasoning():
    crm, calls = CRM(automated=True), []
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00", crm=crm,
        reasoner=lambda *args, **kwargs: calls.append(1),
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.timeline_items_excluded == 2
    assert stats.threads_candidates == 0 and calls == []
    assert crm.mutations == []


def test_shadow_report_exposes_intended_effects_without_raw_email_data():
    crm = CRM()
    def reasoner(*_args, **_kwargs):
        return {
            "relevant": True,
            "timeline_event": {"headline": "Meeting requested",
                               "body": "The client requested a Friday meeting.",
                               "labels": ["Next step"]},
            "account": {"account_notes": {
                "status": "ANSWERED", "value": "Requested Friday meeting",
                "evidence": {"excerpt": "Can we meet Friday?", "confidence": .95}}},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": [],
        }
    report = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00", crm=crm,
        reasoner=reasoner,
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc)).to_dict()
    serialized = __import__("json").dumps(report)
    assert "Proposal follow-up" not in serialized
    assert "jane@acme.com" not in serialized
    assert "Can we meet Friday?" not in serialized
    assert report["threads"][0]["proposed_fields"][0]["value"] == \
        "Requested Friday meeting"


def test_normalization_failure_disqualifies_night():
    crm = CRM()
    original = crm.email_timeline_items
    def bad_page(contact_id):
        page = original(contact_id)
        page["items"][0]["created_at"] = "not-a-time"
        return page
    crm.email_timeline_items = bad_page
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm,
        reasoner=lambda *_args, **_kwargs: {},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.normalization_failures == 2
    assert not stats.observation_eligible


def test_duplicate_contact_identity_is_excluded_and_disqualifies_night():
    crm = CRM()
    crm.email_contact_ambiguity_count = 2
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm,
        reasoner=lambda *_args, **_kwargs: {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": []},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contact_identity_ambiguities == 2
    assert not stats.observation_eligible
    assert all("@" not in warning for warning in stats.warnings)


def test_dormant_duplicate_contact_identity_does_not_disqualify_night():
    crm = CRM()
    crm.email_contact_ambiguity_count = 1
    crm.email_contact_ambiguities = [[
        {"id": "duplicate-1"}, {"id": "duplicate-2"},
    ]]
    original = crm.email_timeline_items
    def timeline(contact_id):
        if contact_id.startswith("duplicate-"):
            return {"cursor": "", "items": [{
                "id": f"old-{contact_id}", "type": "email", "title": "Old",
                "created_at": "2026-08-10T12:00:00Z", "content": "Old message",
                "metadata": {"from": "client@example.com",
                             "to": ["alex@harboradvisors.example"], "cc": [], "bcc": []},
            }]}
        return original(contact_id)
    crm.email_timeline_items = timeline
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm,
        reasoner=lambda *_args, **_kwargs: {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": []},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contact_identity_ambiguities == 1
    assert stats.contact_identity_ambiguity_events == 0
    assert stats.observation_eligible


def test_resolved_contact_identity_scans_alias_timelines_as_canonical():
    class ResolvedCRM(CRM):
        email_contact_identity_resolutions = [{"canonical_contact_id": "c1"}]
        def email_contact_index(self):
            return {"jane@acme.com": {
                "id": "c1", "name": "Jane", "email": "jane@acme.com",
                "account_ids": ["a1"], "deal_ids": ["d1"],
                "timeline_contact_ids": ["c1", "c-alias"],
            }}
    crm = ResolvedCRM()
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00", crm=crm,
        reasoner=lambda *_args, **_kwargs: {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": []},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contact_identity_resolutions == 1
    assert stats.contacts_scanned == 2
    assert stats.timeline_items_deduplicated == 1


def test_new_email_on_duplicate_contact_identity_disqualifies_night():
    crm = CRM()
    crm.email_contact_ambiguity_count = 1
    crm.email_contact_ambiguities = [[{"id": "duplicate-1"}]]
    original = crm.email_timeline_items
    def timeline(contact_id):
        if contact_id == "duplicate-1":
            return {"cursor": "", "items": [{
                "id": "ambiguous-mail", "type": "email", "title": "Current",
                "created_at": "2026-08-17T14:00:00Z", "content": "Current message",
                "metadata": {"from": "client@example.com",
                             "to": ["alex@harboradvisors.example"], "cc": [], "bcc": []},
            }]}
        return original(contact_id)
    crm.email_timeline_items = timeline
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm,
        reasoner=lambda *_args, **_kwargs: {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": []},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.contact_identity_ambiguity_events == 1
    assert not stats.observation_eligible
    assert all("@" not in warning for warning in stats.warnings)


def test_verification_activity_is_processed_only_with_exact_contact_allowlist():
    class FixtureCRM(CRM):
        def email_contact_index(self):
            return {"fixture@example.invalid": {
                "id": "999", "name": "Fixture", "email": "fixture@example.invalid",
                "account_ids": ["a1"], "deal_ids": ["d1"],
            }}
        def email_timeline_items(self, contact_id):
            return {"cursor": "", "items": [{
                "id": "fixture-1", "type": "custom",
                "custom_activity_id": "verify-activity",
                "title": "Verification", "created_at": "2026-08-17T12:00:00Z",
                "content": ('HARBOR_VERIFY_EMAIL:{"from":"alex@harboradvisors.example",'
                            '"to":["fixture@example.invalid"]}\n'
                            'The Business Listening Activity is complete.'),
                "metadata": {},
            }]}
    calls = []
    reasoner = lambda *_args, **_kwargs: (
        calls.append(1) or {
            "relevant": False, "timeline_event": {}, "account": {},
            "deal": {}, "contacts": [], "new_activities": [],
            "activity_completions": [],
        })
    original = config.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID
    try:
        config.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID = "verify-activity"
        scheduled = run_email_listening(
            shadow=True, since="2026-08-17T00:00:00-04:00",
            crm=FixtureCRM(), reasoner=reasoner,
            now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
        verification = run_email_listening(
            shadow=True, since="2026-08-17T00:00:00-04:00",
            crm=FixtureCRM(), reasoner=reasoner,
            verification_contact_ids={"999"},
            now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    finally:
        config.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID = original
    assert scheduled.threads_candidates == 0
    assert verification.threads_candidates == 1
    assert len(calls) == 1


def test_full_nonemail_page_with_cursor_disqualifies_night():
    crm = CRM()
    crm.email_timeline_items = lambda contact_id: {
        "cursor": "hidden-page", "items": [{
            "id": f"update-{index}", "type": "update", "title": "Update",
            "created_at": "2026-08-17T12:00:00Z", "content": "not email",
            "metadata": {},
        } for index in range(25)]}
    stats = run_email_listening(
        shadow=True, since="2026-08-17T00:00:00-04:00",
        nightly_observation=True, crm=crm,
        reasoner=lambda *_args, **_kwargs: {},
        now=dt.datetime(2026, 8, 18, 6, tzinfo=dt.timezone.utc))
    assert stats.page_saturation_gaps == 2
    assert not stats.observation_eligible


def test_verification_allowlist_isolates_nudges_and_global_state():
    class LiveCRM(CRM):
        def __init__(self):
            super().__init__(); self.saved_nudges, self.saved_state = [], []
        def email_contact_index(self):
            return {
                "jane@acme.com": {"id": "c1", "account_ids": ["a1"],
                                   "deal_ids": ["d1"]},
                "bob@other.com": {"id": "c2", "account_ids": ["a2"],
                                   "deal_ids": ["d2"]},
            }
        def email_account_index(self):
            return {"acme.com": {"id": "a1"}, "other.com": {"id": "a2"}}
        def email_deal_index(self):
            return {
                "d1": {"id": "d1", "name": "Acme", "active": True,
                       "contact_ids": ["c1"], "account_ids": ["a1"],
                       "last_interaction_at": "2026-08-14", "nudge_ts": "",
                       "nudge_key": "", "last_inbound_email_at": ""},
                "d2": {"id": "d2", "name": "Other", "active": True,
                       "contact_ids": ["c2"], "account_ids": ["a2"],
                       "last_interaction_at": "2026-08-14", "nudge_ts": "",
                       "nudge_key": "", "last_inbound_email_at": ""},
            }
        def email_timeline_items(self, contact_id): return {"cursor": "", "items": []}
        def verify_phase2_schema(self): pass
        def email_reconciliation_state(self):
            raise AssertionError("verification must not read global state")
        def save_email_reconciliation_state(self, state): self.saved_state.append(state)
        def save_deal_nudge(self, item, **kwargs):
            self.saved_nudges.append((item, kwargs))
        def clear_deal_nudge(self, item): pass

    class LiveSlack:
        def __init__(self): self.posts = []
        def post(self, channel, text, client_msg_id=None):
            self.posts.append((channel, text)); return "1.0"
        def permalink(self, channel, ts): return "https://slack/nudge"
        def delete(self, channel, ts): pass

    crm, slack = LiveCRM(), LiveSlack()
    original = config.require_email_live
    try:
        config.require_email_live = lambda: None
        stats = run_email_listening(
            shadow=False, since="2026-08-17T00:00:00-04:00",
            crm=crm, slack=slack, verification_contact_ids={"c1"},
            now=dt.datetime(2026, 8, 18, 16, tzinfo=dt.timezone.utc))
    finally:
        config.require_email_live = original
    assert stats.contacts_scanned == 1
    assert [item for item, _details in crm.saved_nudges] == ["d1"]
    assert crm.saved_state == []


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
