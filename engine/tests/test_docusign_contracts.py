"""Bounded contract reasoning and deterministic finalization tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.docusign_contracts import (  # noqa: E402
    contract_narrative, finalize_contract_terms,
)
from runtime.docusign_reasoning import validate_reasoning_result  # noqa: E402


ALLOWED = {
    "billing_model": ["Unknown", "Monthly", "Hourly", "Fixed fee", "Mixed"],
    "service_type": ["RevOps", "List Building"],
}
SOURCE = """=== fees ===
[Page 7]
4. Fees
Client will pay a fixed fee of $25,000, invoiced 50% on signature and 50% on delivery.

=== scope_services ===
[Page 5]
Services
Harbor will implement the revenue operations system.

=== parties_effective_date ===
[Page 1]
This SOW is effective September 1, 2026.
"""


def answered(value, excerpt, *, section="fees", page=7, confidence=.98):
    return {"status": "ANSWERED", "value": value, "evidence": {
        "excerpt": excerpt, "section": section, "page": page,
        "confidence": confidence,
    }}


def test_reasoning_accepts_only_exact_high_confidence_evidence():
    result = validate_reasoning_result({"fields": {
        "fixed_fee": answered(
            "25000", "Client will pay a fixed fee of $25,000"),
        "billing_cadence": answered(
            "50% on signature and 50% on delivery",
            "invoiced 50% on signature and 50% on delivery"),
        "service_type": answered(
            ["RevOps"], "revenue operations system",
            section="scope_services", page=5),
        "effective_date": answered(
            "2026-09-01", "effective September 1, 2026",
            section="parties_effective_date", page=1),
    }, "_audit": {"provider": "OpenRouter"}}, SOURCE, allowed=ALLOWED)
    assert result["fixed_fee"] == 25000.0
    assert result["billing_model"] == "Fixed fee"
    assert result["service_type"] == ["RevOps"]
    assert result["effective_date"] == "2026-09-01"
    assert result["reasoning_audit"]["provider"] == "OpenRouter"


def test_hallucinated_or_low_confidence_commercials_fail_closed():
    for field in (
            answered("25000", "This quote is not in the source"),
            answered("25000", "fixed fee of $25,000", confidence=.70)):
        try:
            validate_reasoning_result(
                {"fields": {"fixed_fee": field}}, SOURCE, allowed=ALLOWED)
        except ValueError as error:
            assert "high-confidence commercial amount" in str(error)
        else:
            raise AssertionError("unsupported commercial must not finalize")


def test_finalization_adds_metadata_and_maps_explicit_total_value():
    terms = finalize_contract_terms({
        "fixed_fee": 25000.0, "total_contract_value": 25000.0,
        "engagement_start_date": "2026-09-01",
        "engagement_end_date": "2026-12-31",
        "billing_model": "Fixed fee",
    }, signer_names=("Jane Buyer",),
        completed_at="2026-08-23T17:30:00Z",
        signed_contract_drive_link="https://drive.google.com/drive/folders/folder12345")
    assert terms["deal_value"] == 25000.0
    assert terms["engagement_dates"] == {
        "from": "2026-09-01", "to": "2026-12-31"}
    assert terms["contract_signed_at"] == "2026-08-23"
    assert contract_narrative(terms)["headline"] == "SOW signed"


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
