"""Contract-specific DeepSeek extraction over bounded executed-SOW excerpts."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
from typing import Any

import yaml

from .reasoning import _complete


SYSTEM_PROMPT = """You extract authoritative commercial terms from a fully executed
MSA + SOW. You receive only approved bounded excerpts: Fees, Payment Terms,
Scope/Services, Term/Renewal/Termination, and first-page party/effective-date context.

Return one JSON object with a `fields` object. Allowed keys are fixed_fee,
monthly_retainer, hourly_rate, estimated_hours, total_contract_value,
billing_model, billing_cadence, payment_terms, service_type, contracted_scope,
engagement_start_date, engagement_end_date, effective_date, and
renewal_termination_terms. Every key must contain:
  {"status":"ANSWERED|NOT_FOUND","value":...,"evidence":{"excerpt":"exact quote",
  "section":"fees|payment_terms|scope_services|term_renewal_termination|parties_effective_date",
  "page":1,"confidence":0.0}}

Use only exact evidence in the supplied excerpts. Do not calculate total contract
value unless the agreement explicitly states it. Numeric values must be plain
USD numbers without symbols or commas. Dates must be YYYY-MM-DD. Map
billing_model and service_type only to the supplied CRM labels. Use NOT_FOUND
when evidence is absent or ambiguous. Do not infer company identity, Deal,
signature status, or signer names. Return no prose outside the JSON object."""


NUMBER_FIELDS = {
    "fixed_fee", "monthly_retainer", "hourly_rate", "estimated_hours",
    "total_contract_value",
}
DATE_FIELDS = {"engagement_start_date", "engagement_end_date", "effective_date"}
TEXT_FIELDS = {
    "billing_cadence", "payment_terms", "contracted_scope",
    "renewal_termination_terms",
}
ALL_FIELDS = NUMBER_FIELDS | DATE_FIELDS | TEXT_FIELDS | {
    "billing_model", "service_type",
}


def extract_contract_terms(excerpts: str) -> dict:
    allowed = _allowed_values()
    user = (
        "CRM billing model labels: " + ", ".join(allowed["billing_model"]) + "\n"
        "CRM service type labels: " + ", ".join(allowed["service_type"]) + "\n\n"
        "Executed-contract excerpts:\n" + excerpts
    )
    result = _complete(SYSTEM_PROMPT, user)
    return validate_reasoning_result(result, excerpts, allowed=allowed)


def validate_reasoning_result(result: dict, excerpts: str, *,
                              allowed: dict[str, list[str]] | None = None,
                              minimum_confidence: float = 0.95) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("fields"), dict):
        raise ValueError("contract reasoning omitted the fields object")
    allowed = allowed or _allowed_values()
    normalized_source = _normalize_evidence(excerpts)
    terms: dict[str, Any] = {}
    evidence: dict[str, dict] = {}
    for key, wrapper in result["fields"].items():
        if key not in ALL_FIELDS or not isinstance(wrapper, dict):
            continue
        if str(wrapper.get("status") or "") != "ANSWERED":
            continue
        proof = wrapper.get("evidence") or {}
        excerpt = str(proof.get("excerpt") or "").strip()
        try:
            confidence = float(proof.get("confidence"))
        except (TypeError, ValueError):
            continue
        if (confidence < minimum_confidence or len(excerpt) < 3 or
                _normalize_evidence(excerpt) not in normalized_source):
            continue
        value = wrapper.get("value")
        if key in NUMBER_FIELDS:
            value = _number(value, key)
        elif key in DATE_FIELDS:
            value = _date(value, key)
        elif key == "billing_model":
            value = _exact_label(value, allowed["billing_model"], key)
            if value == "Unknown":
                continue
        elif key == "service_type":
            raw_values = value if isinstance(value, list) else re.split(
                r"[|;,]", str(value or ""))
            value = list(dict.fromkeys(
                _exact_label(item, allowed["service_type"], key)
                for item in raw_values if str(item).strip()))
            if not value:
                continue
        else:
            value = re.sub(r"\s+", " ", str(value or "")).strip()[:1_800]
            if not value:
                continue
        terms[key] = value
        section = str(proof.get("section") or "")
        page_match = re.search(r"\d+", str(proof.get("page") or ""))
        if section not in {
                "fees", "payment_terms", "scope_services", "term_renewal_termination",
                "parties_effective_date"} or not page_match:
            terms.pop(key, None)
            continue
        evidence[key] = {
            "excerpt": excerpt[:500], "section": section,
            "page": int(page_match.group()), "confidence": confidence,
        }

    if not {"fixed_fee", "monthly_retainer", "hourly_rate"}.intersection(terms):
        raise ValueError("Fees section yielded no high-confidence commercial amount")
    terms.setdefault("billing_model", _billing_model_from_amounts(terms))
    start, end = terms.get("engagement_start_date"), terms.get("engagement_end_date")
    if bool(start) != bool(end):
        terms.pop("engagement_start_date", None)
        terms.pop("engagement_end_date", None)
        evidence.pop("engagement_start_date", None)
        evidence.pop("engagement_end_date", None)
    terms["contract_evidence"] = evidence
    if "_audit" in result:
        terms["reasoning_audit"] = result["_audit"]
    return terms


def _billing_model_from_amounts(terms: dict) -> str:
    found = [key for key in ("fixed_fee", "monthly_retainer", "hourly_rate")
             if key in terms]
    if len(found) > 1:
        return "Mixed"
    return {"fixed_fee": "Fixed fee", "monthly_retainer": "Monthly",
            "hourly_rate": "Hourly"}[found[0]]


def _number(value: Any, key: str) -> float:
    raw = str(value or "").strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        raise ValueError(f"{key} is not a supported non-negative amount")
    number = float(raw)
    if number > 100_000_000:
        raise ValueError(f"{key} exceeds the safety limit")
    return number


def _date(value: Any, key: str) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ValueError(f"{key} is not an ISO date") from error


def _exact_label(value: Any, labels: list[str], key: str) -> str:
    matches = [label for label in labels
               if label.casefold() == str(value or "").strip().casefold()]
    if len(matches) != 1:
        raise ValueError(f"{key} is not an exact CRM label")
    return matches[0]


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _allowed_values() -> dict[str, list[str]]:
    path = Path(__file__).resolve().parents[2] / "reasoning/config/fields.yaml"
    fields = yaml.safe_load(path.read_text(encoding="utf-8"))["deal"]
    return {
        "billing_model": list(fields["billing_model"]["allowed_values"]),
        "service_type": list(fields["service_type"]["allowed_values"]),
    }
