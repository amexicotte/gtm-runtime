"""One-call reasoning-to-writer bridge for meetings released by the gate."""
from __future__ import annotations

import hashlib

from .agent_events import publish_cached_event
from .business_events import attach_business_event, retarget_business_event
from .reasoning import extract
from .call_notes import reasoning_content
from .config import config
from .evidence import block_nonverbatim_evidence, enrich_reasoning_evidence
from .lifecycle import is_active_client_type


def reason_and_write(crm, *, transcript: str, meeting_title: str,
                     meeting_date: str, ctx: dict, manual_notes: str = "",
                     notes_status: str,
                     extractor=extract,
                     applier=None, publisher=None) -> dict:
    """Run exactly one reasoning call and apply its final CRM updates."""
    if not ctx.get("meeting_id") or not ctx.get("prospect_account_id"):
        raise ValueError("reasoning requires a ready meeting and resolved prospect")
    if not transcript or not transcript.strip():
        raise ValueError("reasoning requires a non-empty transcript")
    if notes_status not in ("Received", "Fallback"):
        raise ValueError("reasoning waits for manual notes or the transcript-only fallback")

    ctx["meeting_date"] = meeting_date

    content = reasoning_content(transcript, manual_notes)
    # Claim the meeting before the only model call. If extraction itself fails,
    # release it for a retry. Once extraction succeeds, leave a failed write in
    # Processing so a scheduler restart cannot spend a second model call.
    if hasattr(crm, "begin_meeting_processing"):
        crm.begin_meeting_processing(ctx["meeting_id"])
    else:
        crm.change_columns("meeting", ctx["meeting_id"], {
            "Processing Status": "Processing",
        })
    try:
        result = extractor(content, meeting_title, meeting_date, ctx=ctx)
    except Exception:
        crm.change_columns("meeting", ctx["meeting_id"], {
            "Processing Status": "Ready",
        })
        raise
    if not isinstance(result, dict):
        crm.change_columns("meeting", ctx["meeting_id"], {
            "Processing Status": "Ready",
        })
        raise TypeError("reasoning result must be a JSON object")
    _annotate_and_validate(
        result, transcript=transcript, manual_notes=manual_notes,
        notes_status=notes_status, ctx=ctx)
    event = attach_business_event(
        result, source="meeting", meeting_id=str(ctx["meeting_id"]),
        occurred_at=meeting_date, title=meeting_title, content=content,
        ctx=ctx, source_url=str(ctx.get("source_url") or ""),
    )
    enrich_reasoning_evidence(result, event)
    if hasattr(crm, "save_reasoning_result"):
        try:
            crm.save_reasoning_result(ctx["meeting_id"], result)
        except Exception:
            # Without the durable result a retry must make another model call;
            # release the claim instead of stranding the Meeting forever.
            crm.change_columns("meeting", ctx["meeting_id"], {
                "Processing Status": "Ready",
            })
            raise
    return resume_reasoning_write(
        crm, result=result, meeting_title=meeting_title, ctx=ctx,
        applier=applier, publisher=publisher,
    )


def resume_reasoning_write(crm, *, result: dict, meeting_title: str,
                           ctx: dict, applier=None, publisher=None) -> dict:
    """Apply a previously persisted model result without another model call."""
    if not isinstance(result, dict):
        raise TypeError("reasoning result must be a JSON object")
    if result.get("relevant") is not True:
        publisher = publisher or publish_cached_event
        publisher(
            crm, result, activation_at=config.BUSINESS_EVENTS_ACTIVATED_AT)
        crm.change_columns("meeting", ctx["meeting_id"], {
            "Processing Status": "Processed",
        })
        return result
    if not ctx.get("deal_id") and hasattr(crm, "create_deal"):
        from .deals import ensure_deal_from_result
        ensure_deal_from_result(
            crm, ctx, result, meeting_title=meeting_title)
    if retarget_business_event(result, ctx) and hasattr(crm, "save_reasoning_result"):
        # A newly created exact Deal is a deterministic target discovered after
        # the first cache write. Persist it before any field/narrative effect.
        crm.save_reasoning_result(str(ctx["meeting_id"]), result)
    if applier is None:
        from reasoning.writer import apply
        applier = apply
    applier(result, ctx)
    publisher = publisher or publish_cached_event
    publisher(
        crm, result, activation_at=config.BUSINESS_EVENTS_ACTIVATED_AT)
    crm.change_columns("meeting", ctx["meeting_id"], {
        "Processing Status": "Processed",
    })
    return result


def _annotate_and_validate(result: dict, *, transcript: str,
                           manual_notes: str, notes_status: str,
                           ctx: dict) -> None:
    """Persist input provenance and enforce attendee-identity boundaries."""
    audit = result.setdefault("_audit", {})
    if not isinstance(audit, dict):
        audit = {}
        result["_audit"] = audit
    audit["input"] = {
        "transcript_chars": len(transcript),
        "transcript_words": len(transcript.split()),
        "transcript_sha256": hashlib.sha256(
            transcript.encode("utf-8")).hexdigest(),
        "manual_notes_chars": len(manual_notes),
        "manual_notes_words": len(manual_notes.split()),
        "manual_notes_sha256": hashlib.sha256(
            manual_notes.encode("utf-8")).hexdigest(),
        "notes_status": notes_status,
    }
    audit["crm_snapshot"] = ctx.get("crm_snapshot") or {}
    audit["speaker_identity"] = ctx.get("speaker_identity") or {}
    warnings = audit.setdefault("validation_warnings", [])
    block_nonverbatim_evidence(result, [transcript, manual_notes])

    allowed = {str(email).lower(): str(item_id)
               for email, item_id in (ctx.get("contact_email_ids") or {}).items()}
    contacts = result.get("contacts")
    if isinstance(contacts, list):
        retained = []
        for contact in contacts:
            email = str((contact or {}).get("email") or "").strip().lower()
            if email and email in allowed:
                retained.append(contact)
            else:
                warnings.append(
                    f"ignored model Contact outside external attendee map: {email or 'missing email'}")
        result["contacts"] = retained

    champion = (result.get("deal") or {}).get("champion_name")
    champion_value = _semantic_value(champion)
    if champion_value and len(allowed) == 1:
        contact_id = next(iter(allowed.values()))
        known = _known_contact_names(ctx)
        normalized = str(champion_value).strip().casefold()
        if not str(champion_value).isdigit() and normalized not in known:
            warnings.append(
                f"replaced unverified champion label {champion_value!r} with the "
                "single resolved external Contact ID")
            (result.setdefault("deal", {}))["champion_name"] = contact_id

    account_type = str(
        ((ctx.get("crm_snapshot") or {}).get("account") or {}).get("Type") or ""
    ).strip().casefold()
    call_type = str(_semantic_value(
        (result.get("meeting") or {}).get("call_type")) or "").strip()
    if is_active_client_type(account_type) and call_type in ("Intro", "Sales"):
        warnings.append(
            f"model labeled a Customer meeting {call_type}; deterministic "
            "Deal routing will preserve the existing Won engagement")


def _semantic_value(value):
    if not isinstance(value, dict):
        return value
    if str(value.get("status") or "").upper() == "NOT_DISCUSSED":
        return None
    return value.get("value")


def _known_contact_names(ctx: dict) -> set[str]:
    contacts = ((ctx.get("crm_snapshot") or {}).get("contacts") or [])
    return {str(row.get("name") or "").strip().casefold()
            for row in contacts if row.get("name")}
