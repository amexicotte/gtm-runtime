"""Deterministic evidence normalization and field-write safeguards."""
from __future__ import annotations

import datetime as dt
from typing import Any
import re

from .business_events import BusinessEvent


def enrich_reasoning_evidence(result: dict, event: BusinessEvent) -> list[str]:
    """Add deterministic source metadata and report unsupported field claims.

    The model supplies the excerpt and confidence.  Code supplies the stable
    source ID and timestamp, which are facts the model must not invent.
    Legacy scalar values remain readable for cached pre-activation meetings but
    production field policies can reject them from new automatic writes.
    """
    warnings: list[str] = []
    for target, semantic_key, value in _field_values(result):
        if value in (None, "", [], {}):
            continue
        if not isinstance(value, dict):
            warnings.append(f"{target}.{semantic_key} has no evidence wrapper")
            continue
        status = str(value.get("status") or "").strip().upper()
        if status in {"NOT_DISCUSSED", "PARTIAL"}:
            continue
        evidence = value.get("evidence")
        if isinstance(evidence, str):
            evidence = {"excerpt": evidence}
        elif not isinstance(evidence, dict):
            evidence = {}
        if value.get("confidence") is not None and evidence.get("confidence") is None:
            evidence["confidence"] = value.get("confidence")
        evidence.setdefault("source_id", event.source_id)
        evidence.setdefault("occurred_at", event.occurred_at)
        value["evidence"] = evidence
        problem = evidence_problem(value)
        if problem:
            warnings.append(f"{target}.{semantic_key} {problem}")
    audit = result.setdefault("_audit", {})
    if isinstance(audit, dict) and warnings:
        audit.setdefault("evidence_warnings", []).extend(warnings)
    return warnings


def evidence_problem(value: Any, *, minimum_confidence: float = 0.8) -> str:
    """Return why an answered value lacks the required evidence, if any."""
    if not isinstance(value, dict):
        return "has no evidence wrapper"
    status = str(value.get("status") or "").strip().upper()
    if status in {"NOT_DISCUSSED", "PARTIAL"}:
        return ""
    if status != "ANSWERED":
        return "must have status ANSWERED"
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        return "has no structured evidence"
    if not str(evidence.get("excerpt") or "").strip():
        return "has no source excerpt"
    try:
        confidence = float(evidence.get("confidence"))
    except (TypeError, ValueError):
        return "has no numeric confidence"
    if not 0 <= confidence <= 1:
        return "has confidence outside 0..1"
    if confidence < minimum_confidence:
        return f"has confidence below {minimum_confidence:.2f}"
    if not str(evidence.get("source_id") or "").strip():
        return "has no source ID"
    occurred_at = str(evidence.get("occurred_at") or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return "has no valid event timestamp"
    if parsed.tzinfo is None:
        return "has a timezone-naive event timestamp"
    return ""


def block_nonverbatim_evidence(result: dict, sources: list[str]) -> list[str]:
    """Downgrade answered field claims not found verbatim in source material."""
    warnings: list[str] = []
    normalized_sources = [_normalized(value) for value in sources if str(value).strip()]
    for target, semantic_key, value in _field_values(result):
        if not isinstance(value, dict):
            continue
        if str(value.get("status") or "").strip().upper() != "ANSWERED":
            continue
        evidence = value.get("evidence")
        excerpt = str((evidence or {}).get("excerpt") or "") \
            if isinstance(evidence, dict) else str(evidence or "")
        if not excerpt or not any(
                _normalized(excerpt) in source for source in normalized_sources):
            value["status"] = "PARTIAL"
            warnings.append(
                f"{target}.{semantic_key} evidence is not verbatim source text")
    if warnings:
        audit = result.setdefault("_audit", {})
        if isinstance(audit, dict):
            existing = audit.setdefault("validation_warnings", [])
            for warning in warnings:
                if warning not in existing:
                    existing.append(warning)
    return warnings


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).casefold()).strip()


def _field_values(result: dict):
    for target in ("account", "deal", "meeting"):
        values = result.get(target) or {}
        if isinstance(values, dict):
            for key, value in values.items():
                yield target, key, value
    contacts = result.get("contacts") or []
    if isinstance(contacts, list):
        for index, contact in enumerate(contacts):
            if not isinstance(contact, dict):
                continue
            for key, value in contact.items():
                if key not in {"email", "_id"}:
                    yield f"contacts[{index}]", key, value
