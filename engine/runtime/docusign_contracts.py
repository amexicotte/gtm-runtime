"""Finalize model-extracted executed-contract terms for deterministic writes."""
from __future__ import annotations

import datetime as dt


def finalize_contract_terms(extracted: dict, *, signer_names: tuple[str, ...],
                            completed_at: str,
                            signed_contract_drive_link: str = "") -> dict:
    if not signer_names:
        raise ValueError("completed envelope requires at least one signer")
    try:
        completed = dt.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid DocuSign completion timestamp") from error
    if completed.tzinfo is None:
        raise ValueError("DocuSign completion timestamp must be timezone-aware")
    terms = {
        key: value for key, value in extracted.items()
        if key not in {"reasoning_audit", "contract_evidence"}
    }
    if "total_contract_value" in terms:
        terms["deal_value"] = terms.pop("total_contract_value")
    start = terms.pop("engagement_start_date", None)
    end = terms.pop("engagement_end_date", None)
    if start and end:
        terms["engagement_dates"] = {"from": start, "to": end}
    terms["contract_signers"] = list(dict.fromkeys(signer_names))
    terms["contract_signed_at"] = completed.astimezone(
        dt.timezone.utc).date().isoformat()
    if signed_contract_drive_link:
        terms["signed_contract_drive_link"] = signed_contract_drive_link
    return terms


def contract_narrative(terms: dict) -> dict:
    amounts = []
    for key, label in (
            ("fixed_fee", "fixed fee"),
            ("monthly_retainer", "monthly retainer"),
            ("hourly_rate", "hourly rate")):
        if key in terms:
            amounts.append(f"{label} ${terms[key]:,.2f}")
    commercial = ", ".join(amounts) or str(terms.get("billing_model") or "")
    services = ", ".join(terms.get("service_type") or [])
    details = []
    if terms.get("billing_cadence"):
        details.append(f"Billing cadence: {terms['billing_cadence']}.")
    if terms.get("payment_terms"):
        details.append(f"Payment terms: {terms['payment_terms']}.")
    if terms.get("effective_date"):
        details.append(f"Effective date: {terms['effective_date']}.")
    body = (
        f"The fully executed MSA + SOW was received. Final commercials: {commercial}."
        + (f" Contracted services: {services}." if services else "")
        + (" " + " ".join(details) if details else "")
        + (" The signed agreement is archived in the Account client folder."
           if terms.get("signed_contract_drive_link") else "")
    )
    return {"headline": "SOW signed", "body": body[:1_500],
            "labels": ["Decision"]}
