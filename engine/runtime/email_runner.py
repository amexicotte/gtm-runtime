"""Nightly monday E&A listening, reconciliation, and shadow reporting."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime as dt
import hashlib
import json
from typing import Callable
from zoneinfo import ZoneInfo

from .business_events import BusinessEvent
from .config import config
from .email_events import (
    discover_missing_contacts, exclusion_reason, group_thread_messages,
    normalize_timeline_item, rank_activity_candidates, resolve_thread,
)
from .email_reconciliation import (
    apply_claimed_email_event, prepare_email_result,
)
from .monday import Monday
from .nudges import sync_nudges
from .reasoning import extract_email
from .slack import Slack


ET = ZoneInfo("America/New_York")


@dataclass
class EmailRunStats:
    mode: str = "shadow"
    contacts_scanned: int = 0
    contact_identity_ambiguities: int = 0
    contact_identity_resolutions: int = 0
    contact_identity_ambiguity_events: int = 0
    contact_discoveries_proposed: int = 0
    contacts_created: int = 0
    contact_creation_conflicts: int = 0
    timeline_items_read: int = 0
    timeline_items_normalized: int = 0
    timeline_items_deduplicated: int = 0
    timeline_items_seen: int = 0
    timeline_items_excluded: int = 0
    normalization_failures: int = 0
    threads_candidates: int = 0
    threads_reasoned: int = 0
    threads_ingest_only: int = 0
    threads_resumed: int = 0
    threads_processed: int = 0
    threads_ambiguous: int = 0
    threads_failed: int = 0
    threads_relevant: int = 0
    threads_irrelevant: int = 0
    threads_skipped_busy: int = 0
    page_saturation_gaps: int = 0
    cap_reached: bool = False
    field_write_batches: int = 0
    records_changed: int = 0
    activities_created: int = 0
    activities_completed: int = 0
    narrative_updates_posted: int = 0
    narrative_updates_deduplicated: int = 0
    high_water_before: str = ""
    high_water_after: str = ""
    threshold: str = ""
    observation_date: str = ""
    observation_eligible: bool = False
    exclusions: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    threads: list[dict] = field(default_factory=list)
    nudges: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def run_email_listening(
        *, shadow: bool = True, since: str = "", nightly_observation: bool = False,
        crm=None, slack=None, reasoner: Callable = extract_email,
        now: dt.datetime | None = None, max_threads: int | None = None,
        max_reasoning: int | None = None, applier=None,
        ingest_only: bool = False,
        verification_contact_ids: set[str] | None = None,
        business_context=None) -> EmailRunStats:
    """Run one bounded E&A pass; shadow mode performs no monday/Slack mutation."""
    crm = crm or Monday()
    if not shadow:
        config.require_email_live()
        if not hasattr(crm, "verify_phase2_schema"):
            raise RuntimeError("live email adapter cannot verify the Phase 2 schema")
        crm.verify_phase2_schema()
        slack = slack or Slack()
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    max_threads = max_threads or config.MAX_EMAIL_THREADS_PER_RUN
    max_reasoning = max_reasoning or config.MAX_EMAIL_REASONING_PER_RUN
    stats = EmailRunStats(mode="shadow" if shadow else "live")
    stats.observation_date = now.astimezone(ET).date().isoformat()

    verification_mode = verification_contact_ids is not None
    state = {} if shadow or verification_mode else crm.email_reconciliation_state()
    seen: dict[str, int] = {
        str(key): int(value) for key, value in (
            state.get("seen_timeline_hashes") or {}).items()}
    stats.high_water_before = str(state.get("high_water_at") or "")
    activation = since or config.EMAIL_LISTENING_ACTIVATED_AT
    if not activation:
        raise RuntimeError(
            "EMAIL_LISTENING_ACTIVATED_AT or --since is required; refusing email backfill")
    activation_time = _parse(activation)
    if stats.high_water_before:
        overlap = dt.timedelta(hours=config.EMAIL_RECONCILIATION_OVERLAP_HOURS)
        threshold = max(activation_time, _parse(stats.high_water_before) - overlap)
    else:
        threshold = activation_time
    stats.threshold = threshold.isoformat()

    contacts = crm.email_contact_index()
    stats.contact_identity_ambiguities = int(
        getattr(crm, "email_contact_ambiguity_count", 0) or 0)
    stats.contact_identity_resolutions = len(
        getattr(crm, "email_contact_identity_resolutions", []) or [])
    ambiguous_groups = getattr(crm, "email_contact_ambiguities", None)
    if stats.contact_identity_ambiguities:
        stats.warnings.append(
            f"{stats.contact_identity_ambiguities} duplicate Contact email "
            "identity group(s) quarantined")
    accounts = (crm.email_account_index()
                if hasattr(crm, "email_account_index") else {})
    deals = crm.email_deal_index()
    if verification_contact_ids is not None:
        found = {str(row["id"]) for row in contacts.values()}
        missing = verification_contact_ids - found
        if missing:
            raise RuntimeError(
                "verification Contact IDs were not found: " + ", ".join(sorted(missing)))
        contacts = {email: row for email, row in contacts.items()
                    if str(row["id"]) in verification_contact_ids}
        allowed_accounts = {
            str(value) for row in contacts.values()
            for value in row.get("account_ids") or []}
        allowed_deals = {
            str(value) for row in contacts.values()
            for value in row.get("deal_ids") or []}
        accounts = {domain: row for domain, row in accounts.items()
                    if str(row.get("id") or "") in allowed_accounts}
        deals = {deal_id: row for deal_id, row in deals.items()
                 if str(deal_id) in allowed_deals}
    by_id: dict[str, object] = {}
    excluded_ids: dict[str, str] = {}
    newest_seen = _parse(stats.high_water_before) if stats.high_water_before else threshold

    # Duplicate exact-email identities are never used for entity resolution or
    # writes. Inspect their timelines read-only so an old dormant duplicate does
    # not invalidate every observation forever, while any new in-window email
    # still fails the night closed instead of being silently skipped.
    if ambiguous_groups is None and stats.contact_identity_ambiguities:
        stats.contact_identity_ambiguity_events = stats.contact_identity_ambiguities
        stats.warnings.append(
            "duplicate Contact timeline coverage is unavailable")
    else:
        ambiguous_timeline_ids: set[str] = set()
        for group in ambiguous_groups or []:
            for contact in group:
                page = crm.email_timeline_items(str(contact["id"]))
                stats.contacts_scanned += 1
                rows = page.get("items") or []
                stats.timeline_items_read += len(rows)
                if page.get("cursor") and len(rows) >= 25:
                    try:
                        raw_times = [_parse(str(row.get("created_at") or ""))
                                     for row in rows]
                        saturated = min(raw_times) > threshold
                    except (AttributeError, TypeError, ValueError):
                        saturated = True
                    if saturated:
                        stats.page_saturation_gaps += 1
                        stats.warnings.append(
                            f"Contact {contact['id']} latest E&A page is saturated "
                            "inside the overlap")
                for raw in rows:
                    try:
                        message = normalize_timeline_item(raw)
                    except (TypeError, ValueError) as error:
                        stats.normalization_failures += 1
                        stats.warnings.append(
                            "timeline item normalization failed: "
                            f"{type(error).__name__}")
                        continue
                    if message is None or _parse(message.occurred_at) < threshold:
                        continue
                    if exclusion_reason(message):
                        continue
                    ambiguous_timeline_ids.add(message.timeline_id)
        stats.contact_identity_ambiguity_events = len(ambiguous_timeline_ids)
        if stats.contact_identity_ambiguity_events:
            stats.warnings.append(
                f"{stats.contact_identity_ambiguity_events} in-window email item(s) "
                "quarantined for duplicate Contact identity")

    for contact in sorted(contacts.values(), key=lambda row: str(row["id"])):
        timeline_contact_ids = contact.get("timeline_contact_ids") or [contact["id"]]
        for timeline_contact_id in timeline_contact_ids:
            page = crm.email_timeline_items(str(timeline_contact_id))
            stats.contacts_scanned += 1
            rows = page.get("items") or []
            stats.timeline_items_read += len(rows)
            normalized_page = []
            for raw in rows:
                try:
                    message = normalize_timeline_item(
                        raw,
                        verification_custom_activity_id=(
                            config.EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID
                            if verification_mode else ""),
                    )
                except (TypeError, ValueError) as error:
                    stats.normalization_failures += 1
                    stats.warnings.append(
                        f"timeline item normalization failed: {type(error).__name__}")
                    continue
                if message is None:
                    continue
                normalized_page.append(message)
            if page.get("cursor") and len(rows) >= 25:
                # Prove coverage from raw E&A timestamps even when every row is a
                # non-email type. A cursor cannot otherwise be continued safely.
                try:
                    raw_times = [_parse(str(row.get("created_at") or "")) for row in rows]
                    saturated = min(raw_times) > threshold
                except (AttributeError, TypeError, ValueError):
                    saturated = True
                if saturated:
                    stats.page_saturation_gaps += 1
                    stats.warnings.append(
                        f"Contact {timeline_contact_id} latest E&A page is saturated inside the overlap")
            for message in normalized_page:
                occurred = _parse(message.occurred_at)
                if occurred < threshold:
                    continue
                newest_seen = max(newest_seen, occurred)
                message.observed_contact_ids.add(str(contact["id"]))
                if _id_hash(message.timeline_id) in seen:
                    stats.timeline_items_seen += 1
                    continue
                reason = exclusion_reason(message)
                if reason:
                    excluded_ids[message.timeline_id] = message.occurred_at
                    stats.timeline_items_excluded += 1
                    stats.exclusions[reason] = stats.exclusions.get(reason, 0) + 1
                    continue
                existing = by_id.get(message.timeline_id)
                if existing:
                    existing.observed_contact_ids.update(message.observed_contact_ids)
                    stats.timeline_items_deduplicated += 1
                else:
                    by_id[message.timeline_id] = message
                    stats.timeline_items_normalized += 1

    messages = sorted(by_id.values(), key=lambda row: (row.occurred_at, row.timeline_id))
    if len(messages) > config.MAX_EMAIL_TIMELINE_ITEMS_PER_RUN:
        stats.cap_reached = True
        stats.warnings.append("email timeline-item hard cap reached")
        messages = messages[:config.MAX_EMAIL_TIMELINE_ITEMS_PER_RUN]
    groups = group_thread_messages(messages)
    stats.threads_candidates = len(groups)
    if len(groups) > max_threads:
        stats.cap_reached = True
        stats.warnings.append("email thread hard cap reached")
        groups = groups[:max_threads]

    processed_ids = dict(excluded_ids)
    reasoning_used = 0
    activity_cache: dict[tuple[str, ...], list[dict]] = {}
    proposed_contact_emails: set[str] = set()
    for group in groups:
        record = None
        try:
            discoveries = (() if verification_mode else discover_missing_contacts(
                group, contact_index=contacts, account_index=accounts,
                deal_index=deals))
            _materialize_contact_discoveries(
                crm, discoveries, contacts=contacts, deals=deals, stats=stats,
                shadow=shadow, proposed_emails=proposed_contact_emails)
            preliminary = resolve_thread(
                group, contact_index=contacts, deal_index=deals,
                account_index=accounts,
                captured_at=now)
            entity_ids = set(preliminary.event.account_ids) | set(
                preliminary.event.contact_ids) | set(preliminary.event.deal_ids)
            cache_key = tuple(sorted(entity_ids))
            if cache_key not in activity_cache:
                rows = crm.open_activity_candidates_for_entities(entity_ids)
                activity_cache[cache_key] = rank_activity_candidates(
                    preliminary.event.content, rows)
            resolved = resolve_thread(
                group, contact_index=contacts, deal_index=deals,
                account_index=accounts,
                activity_candidates=activity_cache[cache_key], captured_at=now)
            event = resolved.event
            if resolved.ambiguities:
                stats.threads_ambiguous += 1
            contact_email_ids = {
                email: str(contacts[email]["id"])
                for email in {item for message in group for item in message.external_emails}
                if email in contacts
            }
            account_id = event.account_ids[0] if len(event.account_ids) == 1 else None
            deal_id = event.deal_ids[0] if len(event.deal_ids) == 1 else None
            snapshot = crm.email_reasoning_context(
                account_id=account_id, contact_ids=list(event.contact_ids),
                deal_id=deal_id)
            ctx = {
                "prospect_account_id": account_id,
                "contact_ids": list(event.contact_ids),
                "contact_email_ids": contact_email_ids,
                "deal_id": deal_id,
                "crm_snapshot": snapshot,
                "open_activity_candidates": list(event.open_activity_candidates),
                "ambiguities": list(resolved.ambiguities),
            }
            if business_context is not None:
                ctx["business_context"] = business_context.for_email(snapshot)
            if ingest_only:
                stats.threads_ingest_only += 1
                stats.threads.append({
                    "source_id": event.source_id,
                    "occurred_at": event.occurred_at,
                    "timeline_item_count": len(resolved.timeline_ids),
                    "account_ids": list(event.account_ids),
                    "contact_ids": list(event.contact_ids),
                    "deal_ids": list(event.deal_ids),
                    "ambiguities": list(resolved.ambiguities),
                    "open_activity_candidates": len(event.open_activity_candidates),
                    "ingest_only": True,
                })
                continue
            result = None
            if not shadow:
                record = crm.claim_agent_event(event, {})
                if not record.get("claimed", True):
                    if record.get("completed"):
                        stats.threads_processed += 1
                        stats.narrative_updates_deduplicated += len(
                            record.get("effects") or {})
                        processed_ids.update({row.timeline_id: row.occurred_at for row in group})
                    else:
                        stats.threads_skipped_busy += 1
                        stats.warnings.append(f"Agent Event {event.event_key} is busy")
                    continue
                result = record.get("reasoning_cache") or None
                if result and result.get("_event"):
                    event = BusinessEvent.from_cache(result["_event"])
                    stats.threads_resumed += 1
            if result is None:
                if reasoning_used >= max_reasoning:
                    stats.cap_reached = True
                    stats.warnings.append("email reasoning hard cap reached")
                    if record and record.get("id"):
                        crm.fail_agent_event(record["id"], "reasoning hard cap reached")
                    break
                result = reasoner(
                    event.content, event.title, event.occurred_at, ctx=ctx)
                reasoning_used += 1
                stats.threads_reasoned += 1
                message_sources = [
                    (row.timeline_id, row.body, row.occurred_at) for row in group]
                outbound_sources = [
                    (row.timeline_id, row.body, row.occurred_at, row.links,
                     tuple(dict.fromkeys(
                         contact_email_ids[email] for _name, email in row.recipients
                         if email in contact_email_ids)), row.link_contexts)
                    for row in group if row.direction == "outbound"]
                inbound_sources = [
                    (row.timeline_id, row.body, row.occurred_at)
                    for row in group if row.direction == "inbound"]
                event = prepare_email_result(
                    result, event=event, contact_email_ids=contact_email_ids,
                    ambiguous_deal=any("multiple plausible active Deals" in item
                                       for item in resolved.ambiguities),
                    completion_confidence=config.EMAIL_COMPLETION_CONFIDENCE,
                    message_sources=message_sources,
                    outbound_sources=outbound_sources,
                    inbound_sources=inbound_sources,
                )
                if record and record.get("id"):
                    crm.save_agent_event_reasoning(str(record["id"]), result)
            report = _thread_report(resolved, result, event)
            stats.threads.append(report)
            if result.get("relevant") is True:
                stats.threads_relevant += 1
            elif result.get("relevant") is False:
                stats.threads_irrelevant += 1
            if shadow:
                continue
            effects = apply_claimed_email_event(
                crm, result=result, event=event,
                agent_event_id=str(record["id"]),
                effects=record.get("effects") or {}, ctx=ctx, applier=applier)
            if resolved.ambiguities:
                crm.record_agent_event_ambiguity(
                    str(record["id"]), "; ".join(resolved.ambiguities))
            stats.field_write_batches += effects["field_writes"]
            stats.records_changed += effects["records_changed"]
            stats.activities_created += effects["activities_created"]
            stats.activities_completed += effects["activities_completed"]
            stats.narrative_updates_posted += effects["updates_posted"]
            stats.narrative_updates_deduplicated += effects["updates_deduplicated"]
            stats.threads_processed += 1
            processed_ids.update({row.timeline_id: row.occurred_at for row in group})
        except Exception as error:
            stats.threads_failed += 1
            if not shadow and record and record.get("id"):
                try:
                    crm.fail_agent_event(str(record["id"]), str(error))
                except Exception:
                    pass
            stats.warnings.append(f"email thread failed: {type(error).__name__}")

    nudge_deals = deals
    activities = crm.followup_nudge_candidates()
    if verification_mode:
        allowed_deal_ids = set(nudge_deals)
        activities = [row for row in activities if
                      set(str(value) for value in row.get("deal_ids") or []) &
                      allowed_deal_ids]
    nudge_stats = sync_nudges(
        slack, crm, deals=nudge_deals, activities=activities,
        channel=config.DEAL_ALERTS_CHANNEL, shadow=shadow,
        stalled_hours=config.NUDGE_STALLED_HOURS,
        unanswered_hours=config.NUDGE_UNANSWERED_HOURS, now=now,
        slack_user_map=config.SLACK_USER_IDS_BY_MONDAY_ID)
    stats.nudges = nudge_stats.to_dict()

    if not shadow and not verification_mode:
        seen.update({_id_hash(item_id): int(_parse(timestamp).timestamp())
                     for item_id, timestamp in processed_ids.items()})
        retain_after = int((threshold - dt.timedelta(
            hours=config.EMAIL_RECONCILIATION_OVERLAP_HOURS)).timestamp())
        seen = {item_hash: timestamp for item_hash, timestamp in seen.items()
                if timestamp >= retain_after}
        safe_advance = (
            not stats.threads_failed and not stats.normalization_failures and
            not stats.page_saturation_gaps and not stats.cap_reached and
            not stats.contact_identity_ambiguity_events)
        high_water = newest_seen if safe_advance else (
            _parse(stats.high_water_before) if stats.high_water_before else threshold)
        stats.high_water_after = high_water.isoformat()
        crm.save_email_reconciliation_state({
            "high_water_at": stats.high_water_after,
            "seen_timeline_hashes": seen,
            "last_run_at": now.isoformat(),
        })
    else:
        stats.high_water_after = stats.high_water_before

    stats.observation_eligible = bool(
        shadow and nightly_observation and not stats.threads_failed and
        not stats.normalization_failures and
        not stats.page_saturation_gaps and not stats.cap_reached and
        not stats.contact_identity_ambiguity_events and
        not ingest_only)
    return stats


def _materialize_contact_discoveries(
        crm, discoveries, *, contacts: dict[str, dict], deals: dict[str, dict],
        stats: EmailRunStats, shadow: bool, proposed_emails: set[str]) -> None:
    """Create exact planned Contacts once and update this run's indexes."""
    for discovery in discoveries:
        if discovery.email not in proposed_emails:
            proposed_emails.add(discovery.email)
            stats.contact_discoveries_proposed += 1
        if shadow or discovery.email in contacts:
            continue
        # Recheck the live board immediately before creating. This protects
        # retries and any unexpected cross-run identity race.
        if crm.find_contact_by_email(discovery.email):
            stats.contact_creation_conflicts += 1
            continue
        contact_id = str(crm.create_contact(
            discovery.email, discovery.account_id,
            name=discovery.name or None,
            deal_ids=list(discovery.deal_ids)))
        contacts[discovery.email] = {
            "id": contact_id,
            "name": discovery.name,
            "email": discovery.email,
            "account_ids": [discovery.account_id],
            "deal_ids": list(discovery.deal_ids),
        }
        for deal_id in discovery.deal_ids:
            deal = deals.get(deal_id)
            if deal is not None:
                deal["contact_ids"] = list(dict.fromkeys([
                    *(str(value) for value in deal.get("contact_ids") or []),
                    contact_id,
                ]))
        stats.contacts_created += 1


def _thread_report(resolved, result: dict, event: BusinessEvent) -> dict:
    audit = result.get("_audit") or {}
    return {
        "source_id": event.source_id,
        "occurred_at": event.occurred_at,
        "timeline_item_count": len(resolved.timeline_ids),
        "account_ids": list(event.account_ids),
        "contact_ids": list(event.contact_ids),
        "deal_ids": list(event.deal_ids),
        "ambiguities": list(resolved.ambiguities),
        "relevant": result.get("relevant"),
        "proposed_fields": _reviewable_fields(result),
        "timeline_event": ({
            "headline": event.timeline_event.headline,
            "body": event.timeline_event.body,
            "labels": list(event.timeline_event.labels),
        } if event.timeline_event else None),
        "new_activities": [{
            "title": row.get("title"), "due_date": row.get("due_date"),
            "confidence": (row.get("evidence") or {}).get("confidence"),
        } for row in result.get("_validated_new_activities") or []],
        "activity_completions": [{
            "activity_id": row.get("activity_id"),
            "confidence": row.get("confidence"),
            "expects_client_response": row.get("expects_client_response"),
        } for row in result.get("_validated_activity_completions") or []],
        "validation_warning_codes": _warning_codes(
            audit.get("validation_warnings") or []),
    }


def _reviewable_fields(result: dict) -> list[dict]:
    rows = []
    sections = [("account", result.get("account") or {}),
                ("deal", result.get("deal") or {})]
    sections.extend((f"contact[{index}]", values)
                    for index, values in enumerate(result.get("contacts") or []))
    for target, values in sections:
        if not isinstance(values, dict):
            continue
        for field_name, wrapper in values.items():
            if field_name in {"email", "_id"} or not isinstance(wrapper, dict):
                continue
            if str(wrapper.get("status") or "").strip().upper() != "ANSWERED":
                continue
            evidence = wrapper.get("evidence") or {}
            rows.append({
                "target": target, "field": field_name,
                "value": wrapper.get("value"),
                "confidence": (evidence.get("confidence")
                               if isinstance(evidence, dict) else None),
            })
    return rows


def _warning_codes(warnings: list[str]) -> list[str]:
    codes = []
    for warning in warnings:
        text = str(warning).casefold()
        if "outside exact participant" in text:
            code = "unmatched_contact"
        elif "multiple active deals" in text:
            code = "ambiguous_deal"
        elif "not verbatim" in text:
            code = "nonverbatim_evidence"
        elif "completion" in text:
            code = "activity_completion_rejected"
        elif "new activity" in text:
            code = "new_activity_rejected"
        else:
            code = "validation_rejected"
        codes.append(code)
    return list(dict.fromkeys(codes))


def _parse(value: str) -> dt.datetime:
    raw = str(value).strip()
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(dt.timezone.utc)


def _id_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]
