"""Executed-contract, archive, onboarding, alert, and retry tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.client_onboarding import (  # noqa: E402
    parse_channel_reply, signed_client_message,
)
from runtime.docusign import CompletedEnvelope  # noqa: E402
from runtime.docusign_documents import ContractSection  # noqa: E402
from runtime.docusign_runner import run_docusign  # noqa: E402
from runtime.drive_archive import ArchiveResult  # noqa: E402


NOW = dt.datetime(2026, 8, 24, 12, tzinfo=dt.timezone.utc)
FOLDER_URL = "https://drive.google.com/drive/folders/folder12345"


def envelope():
    return CompletedEnvelope(
        envelope_id="env-1", completed_at="2026-08-23T17:30:00Z",
        subject="Harbor MSA + SOW", signer_names=("Jane Buyer",),
        signer_emails=("jane@buyer.example",),
        recipient_emails=("jane@buyer.example", "alex@harboradvisors.example"),
        source_url="https://app.docusign.com/documents/details/env-1")


class FakeDocuSign:
    def __init__(self, item=None): self.item = item or envelope(); self.downloads = 0
    def list_completed(self, **kwargs): return [self.item]
    def completed_envelope(self, envelope_id): return self.item
    def download_combined_pdf(self, envelope_id):
        self.downloads += 1
        return b"%PDF-executed-agreement"


class FakeArchive:
    def __init__(self): self.calls = 0
    def upload(self, *args, **kwargs):
        self.calls += 1
        return ArchiveResult(
            "2026-08-23 - Buyer - Signed MSA + SOW - env-1.pdf",
            "folder12345", FOLDER_URL, True)


class FakeSlack:
    def __init__(self): self.messages = []
    def post(self, channel, text, **kwargs):
        self.messages.append((channel, text, kwargs)); return "100.0"
    def permalink(self, *args):
        return "https://harbor.slack.com/archives/deal-alerts/p100"
    def replies(self, *args, **kwargs): return []


class FakeCRM:
    def __init__(self):
        self.effects = {}; self.cached = {}; self.completed = False
        self.applied = 0; self.updates = 0; self.saved_state = {}
        self.ambiguous = False; self.failed = False
        self.project = None; self.alert = {}

    def verify_docusign_schema(self): pass
    def docusign_reconciliation_state(self): return self.saved_state
    def save_docusign_reconciliation_state(self, value): self.saved_state = value
    def resolve_docusign_deal(self, recipient_emails):
        return {"id": "123", "name": "Deal", "stage": "Negotiation",
                "account_id": "a1", "account_name": "Buyer",
                "matched_domain": "buyer.example", "contact_ids": ["c1"],
                "envelope_id": "", "provenance": {},
                "drive_folder_url": FOLDER_URL}
    def claim_agent_event(self, event, result):
        if self.completed:
            return {"id": "e1", "claimed": False, "completed": True,
                    "effects": self.effects, "reasoning_cache": self.cached}
        return {"id": "e1", "claimed": True, "effects": self.effects,
                "reasoning_cache": self.cached}
    def save_agent_event_reasoning(self, item_id, result): self.cached = result
    def apply_docusign_contract(self, **kwargs): self.applied += 1; return True
    def record_agent_event_effect(self, item_id, key, details): self.effects[key] = details
    def ensure_client_project(self, **kwargs): self.project = {"id": "p1"}; return "p1"
    def account_type(self, account_id): return "Prospect"
    def set_account_active_client(self, account_id): pass
    def active_client_project_for_deal(self, *args): return self.project
    def stamp_last_interaction(self, **kwargs): pass
    def update_has_event_marker(self, item_id, key): return False
    def post_narrative_update(self, item_id, body): self.updates += 1; return str(self.updates)
    def client_onboarding_alert(self, deal_id): return self.alert
    def save_client_onboarding_alert(self, deal_id, ts, link):
        self.alert = {"slack_ts": ts, "slack_link": link, "slack_last_ts": ts}
    def pending_client_onboarding(self): return []
    def complete_agent_event(self, item_id): self.completed = True
    def mark_agent_event_ambiguous(self, item_id, error): self.ambiguous = True
    def fail_agent_event(self, item_id, error): self.failed = True


def sections(_):
    return {"fees": ContractSection(
        "fees", (7,), "[Page 7]\nFees\nFixed fee: $25,000")}


class Reasoner:
    def __init__(self): self.calls = 0
    def __call__(self, excerpt):
        self.calls += 1
        return {"fixed_fee": 25000.0, "billing_model": "Fixed fee",
                "contract_evidence": {}, "reasoning_audit": {"provider": "test"}}


def test_shadow_reads_only_bounded_sections_and_proposes_no_writes():
    crm, reasoner = FakeCRM(), Reasoner()
    stats = run_docusign(
        shadow=True, since="2026-08-23T00:00:00Z", crm=crm,
        docusign=FakeDocuSign(), reasoner=reasoner,
        document_extractor=sections, now=NOW, check_config=False)
    assert stats.envelopes_processed == 1 and stats.reasoning_calls == 1
    assert stats.envelopes[0]["matched_domain"] == "buyer.example"
    assert stats.envelopes[0]["would_archive"] is True
    assert crm.applied == 0 and crm.updates == 0


def test_live_retry_reuses_reasoning_and_deduplicates_all_effects():
    crm, reasoner, archive, slack = (
        FakeCRM(), Reasoner(), FakeArchive(), FakeSlack())
    kwargs = dict(
        shadow=False, since="2026-08-23T00:00:00Z", crm=crm,
        docusign=FakeDocuSign(), reasoner=reasoner,
        document_extractor=sections, archive=archive, slack=slack,
        now=NOW, check_config=False)
    first = run_docusign(**kwargs)
    second = run_docusign(**kwargs)
    assert first.deal_fields_finalized == 1 and first.contracts_archived == 1
    assert first.onboarding_alerts_posted == 1
    assert second.envelopes_deduplicated == 1
    assert second.onboarding_alerts_posted == 0
    assert reasoner.calls == 1 and archive.calls == 1 and crm.applied == 1
    assert crm.updates == 4  # Account, Contact, Deal, and Client Project.
    assert len(slack.messages) == 1


def test_unmatched_recipient_domain_alerts_deal_alerts_and_holds_watermark():
    crm, slack = FakeCRM(), FakeSlack()
    crm.resolve_docusign_deal = lambda emails: (_ for _ in ()).throw(
        ValueError("recipient domains did not resolve to exactly one existing Account"))
    stats = run_docusign(
        shadow=False, since="2026-08-23T00:00:00Z", crm=crm,
        docusign=FakeDocuSign(), reasoner=Reasoner(),
        document_extractor=sections, archive=FakeArchive(), slack=slack,
        now=NOW, check_config=False)
    assert stats.envelopes_ambiguous == 1 and stats.review_alerts_posted == 1
    assert stats.high_water_after == "" and crm.saved_state == {}
    assert slack.messages[0][0] == "deal-alerts"
    assert "No CRM fields or stage were changed" in slack.messages[0][1]


def test_onboarding_reply_accepts_only_platform_specific_https_links():
    assert parse_channel_reply(
        "slack: https://harbor.slack.com/archives/C123")[0] == "Slack"
    assert parse_channel_reply(
        "teams: <https://teams.microsoft.com/l/channel/abc|Client>")[0] == "Teams"
    assert parse_channel_reply("slack: https://evil.example/x") is None
    assert "Buyer Co just signed" in signed_client_message("Buyer Co")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
