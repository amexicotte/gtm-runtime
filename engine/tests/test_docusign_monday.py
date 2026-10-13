"""monday schema and atomic signed-contract write tests."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.monday import Monday  # noqa: E402


def client():
    return Monday(token="test")


def test_phase4_schema_adds_contract_and_onboarding_columns_after_fee_schema():
    crm = client()
    crm.prepare_deal_fee_schema = (
        lambda **kwargs: {"columns": ["Fixed Fee"]})
    existing = {}
    crm.optional_column = lambda board, title: existing.get((board, title))
    def ensure(board, title, kind):
        existing[(board, title)] = {"id": title, "type": kind}
        return existing[(board, title)]
    crm.ensure_column = ensure
    result = crm.prepare_docusign_schema()
    assert result["columns"] == [
        "deal.Fixed Fee", "deal.Agent Field Provenance",
        "deal.Contracted Scope",
        "deal.Renewal / Termination Terms", "deal.Contract Signers",
        "deal.Contract Signed At", "deal.DocuSign Envelope ID",
        "deal.Billing Cadence", "deal.Payment Terms",
        "deal.Contract Effective Date", "deal.Signed Contract Drive Link",
        "deal.Client Onboarding Slack TS",
        "deal.Client Onboarding Slack Link",
        "deal.Client Onboarding Slack Last TS",
        "deal.Client Collaboration Connected At",
        "account.Collaboration Platform",
        "account.Client Collaboration Channel",
        "client_project.Collaboration Platform",
        "client_project.Client Collaboration Channel",
        "client_project.Slack Channel",
    ]


def test_contract_write_is_atomic_authoritative_and_disallows_new_labels():
    crm = client()
    crm.docusign_deal_context = lambda deal_id: {
        "id": deal_id, "stage": "Negotiation", "envelope_id": "",
        "provenance": {}, "account_id": "a1", "contact_ids": ["c1"],
    }
    crm.optional_column = lambda board, title: (
        {"id": "review", "type": "status"} if title == "Review Status" else None)
    calls = []
    crm.change_columns = lambda *args, **kwargs: calls.append((args, kwargs))
    changed = crm.apply_docusign_contract(
        deal_id="d1", envelope_id="env1",
        completed_at="2026-08-23T17:30:00+00:00",
        terms={
            "contract_signed_at": "2026-08-23",
            "contract_signers": ["Jane Buyer"], "fixed_fee": 25000.0,
            "billing_model": "Fixed fee", "service_type": ["RevOps"],
            "payment_terms": "Net 30", "effective_date": "2026-09-01",
            "signed_contract_drive_link": "https://drive.google.com/drive/folders/folder12345",
        })
    assert changed is True
    args, kwargs = calls[0]
    values = args[2]
    assert values["Stage"] == "Won"
    assert values["Fixed Fee"] == 25000.0
    assert "Fee" not in values
    assert kwargs == {"create_labels_if_missing": False}
    provenance = json.loads(values["Agent Field Provenance"])
    assert provenance["billing_model"]["authoritative"] is True


def test_recipient_domain_resolves_one_existing_account_and_open_deal():
    crm = client()
    crm.email_account_index = lambda: {
        "buyer.example": {"id": "a1", "name": "Buyer"}}
    crm.active_deals_for_account = lambda account_id: [{"id": "d1"}]
    crm.docusign_deal_context = lambda deal_id: {
        "id": deal_id, "stage": "Negotiation", "envelope_id": "",
        "provenance": {}, "account_id": "a1", "contact_ids": ["c1"],
    }
    crm.account_drive_folder = lambda account_id: (
        "https://drive.google.com/drive/folders/folder12345")
    result = crm.resolve_docusign_deal((
        "alex@harboradvisors.example", "jane@buyer.example"))
    assert result["account_id"] == "a1" and result["id"] == "d1"
    assert result["matched_domain"] == "buyer.example"


def test_customer_lifecycle_uses_exact_live_label_without_creating_one():
    crm = client()
    crm.optional_column = lambda board, title: {"id": "type", "type": "status"}
    calls = []
    crm.change_columns = lambda *args, **kwargs: calls.append((args, kwargs))
    crm.set_account_active_client("a1")
    assert calls == [(('account', 'a1', {'Type': 'Active Client'}),
                      {'create_labels_if_missing': False})]


def test_contract_writer_never_reopens_lost_deal():
    crm = client()
    crm.docusign_deal_context = lambda deal_id: {
        "id": deal_id, "stage": "Lost", "envelope_id": "",
        "provenance": {}, "account_id": "a1", "contact_ids": ["c1"],
    }
    try:
        crm.apply_docusign_contract(
            deal_id="d1", envelope_id="env1",
            completed_at="2026-08-23T17:30:00+00:00",
            terms={"contract_signed_at": "2026-08-23",
                   "contract_signers": ["Jane"]})
    except ValueError as error:
        assert "Lost" in str(error)
    else:
        raise AssertionError("Lost Deal must remain terminal")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
