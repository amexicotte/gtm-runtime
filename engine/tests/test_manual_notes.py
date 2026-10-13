"""Manual Slack-note intake, identity resolution, and cached-write tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.manual_notes import (  # noqa: E402
    ManualIdentityError,
    manual_note_body,
    manual_reason_and_write,
    resume_manual_write,
    resolve_manual_identity,
    sync_manual_note_posts,
)


class FakeSlack:
    def __init__(self, messages):
        self.messages = messages
        self.posts = []
        self.oldest = None

    def history(self, channel, oldest_ts=None):
        self.oldest = oldest_ts
        return self.messages

    def post(self, channel, text, thread_ts=None):
        self.posts.append((channel, text, thread_ts))
        return "bot.1"


class IntakeCRM:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []

    def existing_slack_intake_ids(self):
        return self.existing

    def create_manual_meeting(self, **kwargs):
        self.created.append(kwargs)
        return f"m{len(self.created)}"


class IdentityCRM:
    def __init__(self):
        self.contexts = []
        self.created_accounts = []
        self.created_contacts = []

    def find_account_by_domain(self, domain): return None
    def find_accounts_by_name(self, name):
        return [{"id": "a1", "name": "Acme"}] if name == "Acme" else []
    def create_account_from_manual(self, name, domain=""):
        self.created_accounts.append((name, domain)); return "a-new"
    def find_contact_by_email(self, email): return None
    def find_contacts_by_name(self, name, account):
        return [{"id": "c1", "name": "Jane Smith"}]
    def create_contact_from_manual(self, name, account, email=""):
        self.created_contacts.append((name, account, email)); return "c-new"
    def active_deals_for_account(self, account): return []
    def find_deal_for_account(self, account): return None
    def save_manual_meeting_context(self, meeting, **kwargs):
        self.contexts.append((meeting, kwargs))


def test_trigger_keeps_notes_unstructured_but_requires_explicit_prefix():
    assert manual_note_body("manual: Met Jane at Acme. Budget is approved.") == (
        "Met Jane at Acme. Budget is approved.")
    assert manual_note_body("Manual call:\nrough bullets\n- send scope") == (
        "rough bullets\n- send scope")
    assert manual_note_body("ordinary channel message") is None


def test_sync_creates_only_new_human_root_posts_and_acknowledges_in_thread():
    slack = FakeSlack([
        {"ts": "10.0", "text": "manual: already imported", "user": "U1"},
        {"ts": "11.0", "text": "regular chatter", "user": "U1"},
        {"ts": "12.0", "text": "manual: bot text", "bot_id": "B1"},
        {"ts": "13.0", "thread_ts": "9.0", "text": "manual: reply", "user": "U1"},
        {"ts": "14.0", "text": "manual: Acme intro\nSend a scope Friday", "user": "U2"},
    ])
    crm = IntakeCRM(existing={"10.0"})
    result = sync_manual_note_posts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc),
    )
    assert result.candidates == 2 and result.created == 1
    assert crm.created[0]["slack_ts"] == "14.0"
    assert crm.created[0]["title"] == "Manual call — Acme intro"
    assert crm.created[0]["notes"].endswith("Send a scope Friday")
    assert slack.posts[0][2] == "14.0"


def test_identity_exact_matches_account_and_named_contact_without_inventing_email():
    crm = IdentityCRM()
    result = {"identity": {
        "account_name": "Acme", "account_domain": None,
        "contacts": [{"name": "Jane Smith", "email": None}],
    }}
    ctx = resolve_manual_identity(crm, "m1", result)
    assert ctx["prospect_account_id"] == "a1"
    assert ctx["contact_ids"] == ["c1"]
    assert crm.created_accounts == [] and crm.created_contacts == []
    assert crm.contexts[0][1]["account_id"] == "a1"


def test_identity_creates_final_records_from_domain_and_email():
    crm = IdentityCRM()
    result = {"identity": {
        "account_name": "New Co", "account_domain": "https://newco.com",
        "contacts": [{"name": "Jo Doe", "email": "JO@NEWCO.COM"}],
    }}
    ctx = resolve_manual_identity(crm, "m2", result)
    assert crm.created_accounts == [("New Co", "newco.com")]
    assert crm.created_contacts == [("Jo Doe", "a-new", "jo@newco.com")]
    assert ctx["contact_email_ids"] == {"jo@newco.com": "c-new"}


def test_missing_company_is_gated_instead_of_creating_a_guess():
    try:
        resolve_manual_identity(IdentityCRM(), "m3", {
            "identity": {"account_name": None, "account_domain": None},
        })
    except ManualIdentityError as error:
        assert "company name" in str(error)
    else:
        raise AssertionError("expected ManualIdentityError")


def test_one_model_call_is_cached_before_manual_writes():
    class DurableCRM(IdentityCRM):
        def __init__(self):
            super().__init__(); self.changes = []; self.saved = []
        def begin_meeting_processing(self, meeting):
            self.changes.append(("meeting", meeting, {"Processing Status": "Processing"}))
        def save_reasoning_result(self, meeting, result):
            self.saved.append((meeting, result))
        def change_columns(self, board, item, values):
            self.changes.append((board, item, values))

    crm = DurableCRM()
    model_calls, writes = [], []
    result = {
        "relevant": True,
        "identity": {"account_name": "Acme", "contacts": []},
        "meeting": {"summary": "Useful call", "call_type": "Referral"},
    }
    outcome = manual_reason_and_write(
        crm, notes="Met Acme", meeting_title="Manual call — Acme",
        meeting_date="2026-08-10",
        meeting={"id": "m4", "account_ids": [], "contact_ids": [], "deal_ids": []},
        extractor=lambda *args: model_calls.append(args) or result,
        applier=lambda saved, ctx: writes.append((saved, ctx)),
    )
    assert len(model_calls) == 1
    assert len(crm.saved) == 2
    assert crm.saved[0][0] == crm.saved[1][0] == "m4"
    assert crm.saved[-1][1]["_event"]["source"] == "manual_note"
    assert crm.saved[-1][1]["_event"]["account_ids"] == ("a1",)
    assert writes[0][0] is result and outcome.ctx["prospect_account_id"] == "a1"
    assert crm.changes[-1] == (
        "meeting", "m4", {"Processing Status": "Processed"})


def test_irrelevant_mapped_manual_note_still_routes_interaction_event():
    class CRM:
        def __init__(self): self.saved, self.changes = [], []
        def save_reasoning_result(self, meeting, result):
            self.saved.append((meeting, result))
        def change_columns(self, board, item, values):
            self.changes.append((board, item, values))
    crm, published = CRM(), []
    outcome = resume_manual_write(
        crm, result={"relevant": False}, meeting_title="Manual call — Acme",
        meeting={"id": "m5", "date": "2026-08-17", "manual_notes": "Hello",
                 "account_ids": ["a1"], "contact_ids": ["c1"], "deal_ids": []},
        applier=lambda *_args: (_ for _ in ()).throw(
            AssertionError("irrelevant fields must not write")),
        publisher=lambda *args, **kwargs: published.append((args, kwargs)),
    )
    assert outcome.ctx["prospect_account_id"] == "a1"
    assert len(crm.saved) == 1 and len(published) == 1
    assert crm.changes[-1][2]["Processing Status"] == "Processed"


def test_manual_note_rejects_model_invented_identity():
    class CRM:
        def __init__(self): self.changes, self.saved = [], []
        def begin_meeting_processing(self, meeting): pass
        def save_reasoning_result(self, meeting, result):
            self.saved.append((meeting, result))
        def change_columns(self, board, item, values):
            self.changes.append((board, item, values))
    crm = CRM()
    try:
        manual_reason_and_write(
            crm, notes="Met Acme about next quarter.",
            meeting_title="Manual call — Acme", meeting_date="2026-08-17",
            meeting={"id": "m6", "account_ids": [], "contact_ids": [],
                     "deal_ids": []},
            extractor=lambda *_args: {"relevant": True, "identity": {
                "account_name": "Acme", "account_domain": "invented.example",
                "contacts": [{"name": "Fake Person", "email": "fake@invented.example"}],
            }})
    except ManualIdentityError as error:
        assert "not present" in str(error)
    else:
        raise AssertionError("invented manual identity must be blocked")
    assert len(crm.saved) == 1
    assert crm.changes[-1][2]["Processing Status"] == "Ready"

    cached = crm.saved[0][1]
    cached["relevant"] = False
    published = []
    outcome = resume_manual_write(
        crm, result=cached, meeting_title="Manual call — Acme",
        meeting={"id": "m6", "date": "2026-08-17",
                 "manual_notes": "Met Acme about next quarter.",
                 "prospect_account_id": "a1", "contact_ids": [], "deal_ids": []},
        publisher=lambda *args, **kwargs: published.append(1))
    assert outcome.ctx["prospect_account_id"] == "a1" and published == [1]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
