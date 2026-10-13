"""Deterministic validation and retry-safe effects for one email event."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import re

from .activities import create_email_activities
from .agent_events import publish_claimed_event, _stamp_interaction_effects
from .business_events import BusinessEvent, TimelineEvent
from .evidence import enrich_reasoning_evidence


DECISIONS = {"completed", "partially_completed", "not_completed", "unrelated"}


def prepare_email_result(result: dict, *, event: BusinessEvent,
                         contact_email_ids: dict[str, str],
                         ambiguous_deal: bool,
                         completion_confidence: float,
                         message_sources: list[tuple[str, str, str]],
                         outbound_sources: list[tuple],
                         inbound_sources: list[tuple[str, str, str]]) -> BusinessEvent:
    """Constrain model output to exact entities/evidence before caching it."""
    if not isinstance(result, dict):
        raise TypeError("email reasoning result must be a JSON object")
    warnings = result.setdefault("_audit", {}).setdefault("validation_warnings", [])
    contacts = []
    for row in result.get("contacts") or []:
        if not isinstance(row, dict):
            continue
        email = str(row.get("email") or "").strip().lower()
        if email in contact_email_ids:
            contacts.append(row)
        else:
            warnings.append(
                f"ignored email Contact outside exact participant map: {email or 'missing'}")
    result["contacts"] = contacts
    if ambiguous_deal:
        if result.get("deal"):
            warnings.append("blocked Deal fields because multiple active Deals are plausible")
        result["deal"] = {}

    _block_nonverbatim_field_evidence(result, message_sources, warnings)
    _annotate_email_field_evidence(result, message_sources)
    timeline = TimelineEvent.from_dict(result.get("timeline_event"))
    event = replace(event, timeline_event=timeline)
    result["_event"] = event.to_cache()
    enrich_reasoning_evidence(result, event)

    candidates = {str(row.get("id")): row
                  for row in event.open_activity_candidates if row.get("id")}
    valid_completions = []
    for row in result.get("activity_completions") or []:
        validated, problem = validate_completion(
            row, candidates=candidates, event=event,
            confidence_threshold=completion_confidence,
            outbound_sources=outbound_sources,
        )
        if validated:
            valid_completions.append(validated)
        elif problem:
            warnings.append(problem)
    result["_validated_activity_completions"] = valid_completions

    valid_new = []
    for row in result.get("new_activities") or []:
        validated, problem = validate_new_activity(row, event=event)
        if validated:
            valid_new.append(validated)
        elif problem:
            warnings.append(problem)
    result["_validated_new_activities"] = valid_new
    result["_reconciliation"] = {
        "candidate_ids": sorted(candidates),
        "last_inbound_at": max(
            (occurred_at for _timeline_id, _body, occurred_at in inbound_sources),
            default=""),
    }
    return event


def validate_completion(row: dict, *, candidates: dict[str, dict],
                        event: BusinessEvent, confidence_threshold: float,
                        outbound_sources: list[tuple]) -> tuple[dict | None, str]:
    if not isinstance(row, dict):
        return None, "ignored non-object Activity completion"
    activity_id = str(row.get("activity_id") or "")
    decision = str(row.get("decision") or "").strip().casefold()
    if decision not in DECISIONS:
        return None, f"Activity {activity_id or '?'} has unsupported completion decision"
    if decision != "completed":
        return None, ""
    candidate = candidates.get(activity_id)
    if not candidate:
        return None, f"Activity {activity_id or '?'} was not a retrieved candidate"
    if not candidate.get("unique_match"):
        return None, f"Activity {activity_id} has a competing plausible candidate"
    evidence = row.get("evidence") or {}
    excerpt = str(evidence.get("excerpt") or "").strip()
    try:
        confidence = float(evidence.get("confidence"))
    except (TypeError, ValueError):
        return None, f"Activity {activity_id} completion has no numeric confidence"
    if confidence < confidence_threshold:
        return None, f"Activity {activity_id} completion is below confidence threshold"
    if not excerpt or not _contains(event.content, excerpt):
        return None, f"Activity {activity_id} completion evidence is not verbatim new email text"
    candidate_contacts = (
        {str(value) for value in candidate.get("related_ids") or []} &
        {str(value) for value in event.contact_ids})
    if not candidate_contacts:
        return None, f"Activity {activity_id} has no exact related recipient Contact"
    matches = []
    for source in outbound_sources:
        timeline_id, body, occurred_at = source[:3]
        links = tuple(source[3]) if len(source) > 3 else ()
        recipient_contacts = set(source[4]) if len(source) > 4 else set()
        link_contexts = tuple(source[5]) if len(source) > 5 else ()
        supported_links = tuple(
            url for url, context in link_contexts
            if url in links and _link_supports_delivery(
                context, excerpt, str(candidate.get("title") or "")))
        if (_contains(body, excerpt) and
                candidate_contacts.issubset(recipient_contacts)):
            matches.append((timeline_id, occurred_at, supported_links))
    if not matches:
        return None, (f"Activity {activity_id} completion is not proven by an "
                      "outbound message to the exact related Contact")
    if candidate.get("requires_delivery_evidence") and not matches[-1][2]:
        return None, f"Activity {activity_id} requires a delivered attachment or link"
    timeline_id, occurred_at, _links = matches[-1]
    return {
        "activity_id": activity_id,
        "evidence": excerpt,
        "confidence": confidence,
        "expects_client_response": bool(row.get("expects_client_response")),
        "source_timeline_id": timeline_id,
        "occurred_at": occurred_at,
        "expected_title": str(candidate.get("title") or ""),
        "expected_related_ids": [
            str(value) for value in candidate.get("related_ids") or []],
    }, ""


def validate_new_activity(row: dict, *, event: BusinessEvent
                          ) -> tuple[dict | None, str]:
    if not isinstance(row, dict):
        return None, "ignored non-object new Activity"
    title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
    evidence = row.get("evidence") or {}
    excerpt = str(evidence.get("excerpt") or "").strip()
    try:
        confidence = float(evidence.get("confidence"))
    except (TypeError, ValueError):
        confidence = -1
    if not title:
        return None, "ignored new Activity without title"
    if confidence < 0.8:
        return None, f"new Activity {title!r} is below confidence threshold"
    if not excerpt or not _contains(event.content, excerpt):
        return None, f"new Activity {title!r} has no verbatim new-message evidence"
    return {
        "title": title,
        "due_date": str(row.get("due_date") or "")[:10],
        "evidence": {"excerpt": excerpt, "confidence": confidence,
                     "source_id": event.source_id,
                     "occurred_at": event.occurred_at},
    }, ""


def apply_claimed_email_event(
        crm, *, result: dict, event: BusinessEvent, agent_event_id: str,
        effects: dict, ctx: dict, applier=None) -> dict:
    """Resume every missing effect without another reasoning call."""
    known = dict(effects or {})
    stats = {"field_writes": 0, "activities_created": 0,
             "activities_completed": 0, "updates_posted": 0,
             "updates_deduplicated": 0, "records_changed": 0}
    try:
        relevant = result.get("relevant") is True
        if relevant:
            effect_key = event.effect_key("crm", "fields", kind="field_writes")
            if effect_key not in known:
                if applier is None:
                    from reasoning.writer import apply
                    applier = apply
                applied = applier(result, ctx)
                changed = (applied.get("records_changed")
                           if isinstance(applied, dict) else []) or []
                crm.record_agent_event_effect(agent_event_id, effect_key, {
                    "kind": "field_writes", "account_ids": list(event.account_ids),
                    "contact_ids": list(event.contact_ids),
                    "deal_ids": list(event.deal_ids),
                    "records_changed": changed,
                })
                known[effect_key] = True
                stats["field_writes"] += 1
                stats["records_changed"] += len(changed)

        activity_effect = event.effect_key("activity", "create", kind="new_activities")
        if relevant and activity_effect not in known:
            created = create_email_activities(
                crm, event=event, ctx=ctx,
                items=result.get("_validated_new_activities") or [])
            crm.record_agent_event_effect(agent_event_id, activity_effect, {
                "kind": "new_activities", "created": created,
            })
            known[activity_effect] = True
            stats["activities_created"] += len(created)

        for completion in (
                result.get("_validated_activity_completions") or []) if relevant else ():
            activity_id = str(completion["activity_id"])
            effect_key = event.effect_key(
                "activity", activity_id, kind="completion")
            if effect_key in known:
                continue
            changed = crm.complete_activity_from_event(
                activity_id, event_key=event.event_key,
                occurred_at=completion["occurred_at"], source_type=event.source,
                evidence=completion["evidence"],
                confidence=float(completion["confidence"]),
                awaiting_client_reply=bool(completion.get("expects_client_response")),
                source_timeline_id=completion["source_timeline_id"],
                expected_title=completion.get("expected_title") or "",
                expected_related_ids=completion.get("expected_related_ids") or [],
            )
            crm.record_agent_event_effect(agent_event_id, effect_key, {
                "kind": "activity_completion", "activity_id": activity_id,
                "evidence_sha256": hashlib.sha256(
                    completion["evidence"].encode()).hexdigest(),
                "confidence": completion["confidence"],
                "source_timeline_id": completion["source_timeline_id"],
            })
            known[effect_key] = True
            stats["activities_completed"] += int(changed is not False)

        inbound_at = str(
            (result.get("_reconciliation") or {}).get("last_inbound_at") or "")
        _stamp_interaction_effects(
            crm, event, agent_event_id, known, inbound_email_at=inbound_at)
        narrative = publish_claimed_event(
            crm, result=result, event=event,
            agent_event_id=agent_event_id, effects=known)
        stats["updates_posted"] += narrative.posted
        stats["updates_deduplicated"] += narrative.deduplicated
        crm.complete_agent_event(agent_event_id)
    except Exception as error:
        crm.fail_agent_event(agent_event_id, str(error))
        raise
    return stats


def _block_nonverbatim_field_evidence(
        result: dict, message_sources: list[tuple[str, str, str]],
        warnings: list[str]) -> None:
    sections = [("account", result.get("account") or {}),
                ("deal", result.get("deal") or {})]
    sections.extend((f"contacts[{index}]", row)
                    for index, row in enumerate(result.get("contacts") or []))
    for target, values in sections:
        if not isinstance(values, dict):
            continue
        for key, wrapper in values.items():
            if key in {"email", "_id"} or not isinstance(wrapper, dict):
                continue
            if str(wrapper.get("status") or "").upper() != "ANSWERED":
                continue
            evidence = wrapper.get("evidence") or {}
            excerpt = str(evidence.get("excerpt") or "").strip()
            if not excerpt or not any(
                    _contains(body, excerpt)
                    for _timeline_id, body, _occurred_at in message_sources):
                wrapper["status"] = "PARTIAL"
                warnings.append(
                    f"blocked {target}.{key}: evidence is not verbatim new email text")


def _annotate_email_field_evidence(
        result: dict, message_sources: list[tuple[str, str, str]]) -> None:
    sections = [result.get("account") or {}, result.get("deal") or {},
                *(result.get("contacts") or [])]
    for values in sections:
        if not isinstance(values, dict):
            continue
        for key, wrapper in values.items():
            if key in {"email", "_id"} or not isinstance(wrapper, dict):
                continue
            evidence = wrapper.get("evidence")
            if not isinstance(evidence, dict):
                continue
            excerpt = str(evidence.get("excerpt") or "").strip()
            matches = [(timeline_id, occurred_at)
                       for timeline_id, body, occurred_at in message_sources
                       if _contains(body, excerpt)]
            if matches:
                timeline_id, occurred_at = matches[-1]
                evidence.setdefault("source_timeline_id", timeline_id)
                evidence.setdefault("occurred_at", occurred_at)


def _contains(haystack: str, needle: str) -> bool:
    normalize = lambda value: re.sub(r"\s+", " ", str(value).casefold()).strip()
    return bool(normalize(needle)) and normalize(needle) in normalize(haystack)


def _link_supports_delivery(context: str, excerpt: str, title: str) -> bool:
    """Bind delivery evidence to the link's own sentence, not the whole email."""
    if _contains(context, excerpt):
        return True
    generic = {
        "send", "sent", "share", "shared", "deliver", "delivered",
        "attach", "attached", "attachment", "document", "file", "link",
        "the", "for", "from", "this", "that", "with", "your", "you",
    }
    title_words = {
        word for word in re.findall(r"[a-z0-9]+", str(title).casefold())
        if len(word) > 2 and word not in generic}
    context_words = set(re.findall(r"[a-z0-9]+", str(context).casefold()))
    return bool(title_words & context_words)
