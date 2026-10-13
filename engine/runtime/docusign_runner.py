"""Retry-safe executed-contract finalization and client onboarding."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import datetime as dt
import hashlib
import json

from .agent_events import publish_claimed_event, _stamp_interaction_effects
from .business_events import BusinessEvent, Participant, TimelineEvent
from .client_onboarding import parse_channel_reply, signed_client_message
from .config import config
from .docusign import CompletedEnvelope, DocuSign
from .docusign_contracts import contract_narrative, finalize_contract_terms
from .docusign_documents import extract_contract_sections, reasoning_excerpt
from .docusign_reasoning import extract_contract_terms
from .drive_archive import SignedContractArchive
from .engagements import reconcile_won_engagement
from .monday import Monday
from .slack import Slack


@dataclass
class DocuSignRunStats:
    mode: str = "shadow"
    envelopes_seen: int = 0
    envelopes_processed: int = 0
    envelopes_deduplicated: int = 0
    envelopes_ambiguous: int = 0
    envelopes_failed: int = 0
    reasoning_calls: int = 0
    deal_fields_finalized: int = 0
    contracts_archived: int = 0
    archive_deduplicated: int = 0
    review_alerts_posted: int = 0
    narrative_updates_posted: int = 0
    narrative_updates_deduplicated: int = 0
    client_projects_reconciled: int = 0
    onboarding_alerts_posted: int = 0
    channels_connected: int = 0
    high_water_before: str = ""
    high_water_after: str = ""
    threshold: str = ""
    cap_reached: bool = False
    warnings: list[str] = field(default_factory=list)
    envelopes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_docusign(*, shadow: bool = True, since: str = "", crm=None,
                 docusign=None, reasoner=None, document_extractor=None,
                 archive=None, slack=None,
                 now: dt.datetime | None = None,
                 check_config: bool = True) -> DocuSignRunStats:
    """Process completed envelopes from bounded executed-document excerpts."""
    if check_config:
        if shadow:
            config.require_docusign_reasoning()
        else:
            config.require_docusign_live()
    crm = crm or Monday()
    docusign = docusign or DocuSign()
    reasoner = reasoner or extract_contract_terms
    document_extractor = document_extractor or extract_contract_sections
    archive = archive or (None if shadow else SignedContractArchive())
    slack = slack or (None if shadow else Slack())
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    stats = DocuSignRunStats(mode="shadow" if shadow else "live")
    if not shadow:
        crm.verify_docusign_schema()
    state = {} if shadow else crm.docusign_reconciliation_state()
    stats.high_water_before = str(state.get("high_water_at") or "")
    activation = since or config.DOCUSIGN_ACTIVATED_AT
    if not activation:
        raise RuntimeError(
            "DOCUSIGN_ACTIVATED_AT or --since is required; refusing backfill")
    activation_at = _parse(activation)
    threshold = activation_at
    if stats.high_water_before:
        threshold = max(
            activation_at,
            _parse(stats.high_water_before) - dt.timedelta(
                hours=config.DOCUSIGN_RECONCILIATION_OVERLAP_HOURS),
        )
    stats.threshold = threshold.isoformat()

    listed = docusign.list_completed(from_date=stats.threshold)
    stats.envelopes_seen = len(listed)
    if len(listed) > config.DOCUSIGN_MAX_ENVELOPES_PER_RUN:
        stats.cap_reached = True
        listed = listed[:config.DOCUSIGN_MAX_ENVELOPES_PER_RUN]
        stats.warnings.append("DocuSign completed-envelope hard cap reached")
    newest = _parse(stats.high_water_before) if stats.high_water_before else threshold
    for summary in sorted(
            listed, key=lambda item: (item.completed_at, item.envelope_id)):
        record = None
        envelope = summary
        deal = None
        try:
            envelope = docusign.completed_envelope(summary.envelope_id)
            completed = _parse(envelope.completed_at)
            if completed < activation_at:
                continue
            newest = max(newest, completed)
            deal = crm.resolve_docusign_deal(
                envelope.recipient_emails or envelope.signer_emails)
            pdf_bytes = docusign.download_combined_pdf(envelope.envelope_id)
            pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
            event = _contract_event(
                envelope, deal, content_sha256=pdf_sha, captured_at=now)

            cached = {}
            if not shadow:
                record = crm.claim_agent_event(event, {})
                if not record.get("claimed", True):
                    stats.envelopes_deduplicated += 1
                    if record.get("completed"):
                        stats.onboarding_alerts_posted += _ensure_onboarding_alert(
                            crm, slack, deal, envelope.envelope_id)
                    continue
                cached = record.get("reasoning_cache") or {}
            if isinstance(cached, dict) and cached.get("_validated_contract"):
                extracted = cached["_validated_contract"]
                event = BusinessEvent.from_cache(cached["_event"])
            else:
                sections = document_extractor(pdf_bytes)
                excerpt = reasoning_excerpt(sections)
                extracted = reasoner(excerpt)
                stats.reasoning_calls += 1
                event = replace(
                    event, timeline_event=TimelineEvent.from_dict(
                        contract_narrative(finalize_contract_terms(
                            extracted, signer_names=envelope.signer_names,
                            completed_at=envelope.completed_at))))
                cached = {
                    "relevant": True, "_validated_contract": extracted,
                    "timeline_event": asdict(event.timeline_event),
                    "_event": event.to_cache(),
                    "_audit": {
                        "source": "bounded_executed_contract_sections",
                        "external_reasoning": True,
                        "section_names": sorted(sections),
                        "input_chars": len(excerpt),
                        "pdf_sha256": pdf_sha,
                    },
                }
                if not shadow:
                    crm.save_agent_event_reasoning(str(record["id"]), cached)

            pre_archive_terms = finalize_contract_terms(
                extracted, signer_names=envelope.signer_names,
                completed_at=envelope.completed_at)
            if shadow:
                stats.envelopes_processed += 1
                stats.envelopes.append({
                    "envelope_id_sha256": _sha(envelope.envelope_id),
                    "account_id": deal["account_id"], "deal_id": deal["id"],
                    "matched_domain": deal["matched_domain"],
                    "field_names": sorted(
                        key for key in pre_archive_terms
                        if key not in {"contract_signers", "contract_summary"}),
                    "would_archive": True, "would_set_won": True,
                    "would_prompt_client_channel": True,
                    "model_input_chars": cached["_audit"]["input_chars"],
                })
                continue
            outcome = _apply_claimed(
                crm, archive=archive, slack=slack, result=cached, event=event,
                extracted=extracted, pdf_bytes=pdf_bytes,
                envelope=envelope, deal=deal,
                agent_event_id=str(record["id"]),
                effects=dict(record.get("effects") or {}))
            stats.deal_fields_finalized += int(outcome["deal_finalized"])
            stats.contracts_archived += outcome["archived"]
            stats.archive_deduplicated += outcome["archive_deduplicated"]
            stats.narrative_updates_posted += outcome["updates_posted"]
            stats.narrative_updates_deduplicated += outcome["updates_deduplicated"]
            stats.client_projects_reconciled += outcome["projects_reconciled"]
            stats.onboarding_alerts_posted += outcome["alerts_posted"]
            stats.envelopes_processed += 1
            stats.envelopes.append({
                "envelope_id_sha256": _sha(envelope.envelope_id),
                "account_id": deal["account_id"], "deal_id": deal["id"],
                "status": "processed",
            })
        except ValueError as error:
            stats.envelopes_ambiguous += 1
            stats.warnings.append(str(error))
            if not shadow:
                posted, alert_error = _try_review_alert(
                    crm, slack, envelope, str(error), now, deal=deal)
                stats.review_alerts_posted += posted
                if alert_error:
                    stats.envelopes_failed += 1
                    stats.warnings.append(alert_error)
        except Exception as error:
            stats.envelopes_failed += 1
            stats.warnings.append(
                f"envelope {_sha(summary.envelope_id)[:12]} failed: "
                f"{type(error).__name__}")
            if not shadow:
                if record and record.get("id"):
                    crm.fail_agent_event(str(record["id"]), str(error))
                posted, alert_error = _try_review_alert(
                    crm, slack, envelope,
                    f"processing failed ({type(error).__name__})", now,
                    deal=deal)
                stats.review_alerts_posted += posted
                if alert_error:
                    stats.warnings.append(alert_error)

    if not shadow:
        try:
            stats.channels_connected = _sync_links(crm, slack)
        except Exception as error:
            stats.envelopes_failed += 1
            stats.warnings.append(
                f"client channel synchronization failed: {type(error).__name__}")
    if (not shadow and not stats.envelopes_failed and
            not stats.envelopes_ambiguous and not stats.cap_reached):
        stats.high_water_after = newest.isoformat()
        crm.save_docusign_reconciliation_state({
            "high_water_at": stats.high_water_after,
            "updated_at": now.isoformat(),
        })
    else:
        stats.high_water_after = stats.high_water_before
    return stats


def _apply_claimed(crm, *, archive, slack, result: dict, event: BusinessEvent,
                   extracted: dict, pdf_bytes: bytes,
                   envelope: CompletedEnvelope, deal: dict,
                   agent_event_id: str, effects: dict) -> dict:
    known = dict(effects)
    output = {
        "deal_finalized": False, "archived": 0, "archive_deduplicated": 0,
        "updates_posted": 0, "updates_deduplicated": 0,
        "projects_reconciled": 0, "alerts_posted": 0,
    }
    try:
        archive_key = event.effect_key(
            "account", deal["account_id"], kind="signed_contract_archive")
        if archive_key not in known:
            archived = archive.upload(
                pdf_bytes, folder_url=deal["drive_folder_url"],
                account_name=deal["account_name"],
                signed_date=_parse(envelope.completed_at).date().isoformat(),
                envelope_id=envelope.envelope_id)
            crm.record_agent_event_effect(agent_event_id, archive_key, {
                "kind": "signed_contract_archive",
                "account_id": deal["account_id"], "name": archived.name,
                "uploaded": archived.uploaded,
            })
            known[archive_key] = True
            output["archived"] = int(archived.uploaded)
            output["archive_deduplicated"] = int(not archived.uploaded)

        terms = finalize_contract_terms(
            extracted, signer_names=envelope.signer_names,
            completed_at=envelope.completed_at,
            signed_contract_drive_link=deal["drive_folder_url"])
        narrative = contract_narrative(terms)
        event = replace(event, timeline_event=TimelineEvent.from_dict(narrative))
        result = {**result, "timeline_event": narrative, "_event": event.to_cache()}
        crm.save_agent_event_reasoning(agent_event_id, result)

        field_key = event.effect_key("deal", deal["id"], kind="contract_fields")
        if field_key not in known:
            changed = crm.apply_docusign_contract(
                deal_id=deal["id"], envelope_id=envelope.envelope_id,
                completed_at=event.occurred_at, terms=terms)
            crm.record_agent_event_effect(agent_event_id, field_key, {
                "kind": "contract_fields", "deal_id": deal["id"],
                "field_names": sorted(
                    key for key in terms if key not in {"contract_summary"}),
                "set_won": True,
            })
            known[field_key] = True
            output["deal_finalized"] = bool(changed)

        project_key = event.effect_key(
            "deal", deal["id"], kind="won_engagement_reconciliation")
        if project_key not in known:
            engagement = reconcile_won_engagement(crm, deal=deal, dry_run=False)
            crm.record_agent_event_effect(agent_event_id, project_key, {
                "kind": "won_engagement_reconciliation",
                "projects": engagement.projects_created_or_found,
                "accounts_activated": engagement.accounts_activated,
            })
            known[project_key] = True
            output["projects_reconciled"] = engagement.projects_created_or_found
        _stamp_interaction_effects(
            crm, event, agent_event_id, known, inbound_email_at="")
        published = publish_claimed_event(
            crm, result=result, event=event, agent_event_id=agent_event_id,
            effects=known)
        output["updates_posted"] = published.posted
        output["updates_deduplicated"] = published.deduplicated
        output["alerts_posted"] = _ensure_onboarding_alert(
            crm, slack, deal, envelope.envelope_id)
        alert_key = event.effect_key(
            "deal", deal["id"], kind="client_onboarding_alert")
        if alert_key not in known:
            alert = crm.client_onboarding_alert(deal["id"])
            crm.record_agent_event_effect(agent_event_id, alert_key, {
                "kind": "client_onboarding_alert", "deal_id": deal["id"],
                "slack_ts": alert.get("slack_ts", ""),
            })
        crm.complete_agent_event(agent_event_id)
        return output
    except Exception as error:
        crm.fail_agent_event(agent_event_id, str(error))
        raise


def _ensure_onboarding_alert(crm, slack, deal: dict, envelope_id: str) -> int:
    project = crm.active_client_project_for_deal(
        deal["account_id"], deal["id"])
    if not project:
        raise RuntimeError("Won Deal has no active Client Project")
    existing = crm.client_onboarding_alert(deal["id"])
    if existing.get("slack_ts"):
        return 0
    ts = str(slack.post(
        config.DEAL_ALERTS_CHANNEL,
        signed_client_message(deal["account_name"]),
        client_msg_id=envelope_id))
    crm.save_client_onboarding_alert(
        deal["id"], ts, slack.permalink(config.DEAL_ALERTS_CHANNEL, ts))
    return 1


def _sync_links(crm, slack) -> int:
    count = 0
    for row in crm.pending_client_onboarding():
        messages = slack.replies(
            config.DEAL_ALERTS_CHANNEL, row["slack_ts"],
            oldest_ts=row.get("slack_last_ts") or row["slack_ts"])
        valid = []
        for message in messages:
            if message.get("bot_id") or str(message.get("ts")) == row["slack_ts"]:
                continue
            parsed = parse_channel_reply(message.get("text", ""))
            if parsed:
                valid.append((str(message.get("ts") or "0"), parsed))
        if valid:
            ts, (platform, url) = max(valid, key=lambda value: float(value[0]))
            crm.connect_client_channel(row, platform, url, ts)
            count += 1
        elif messages:
            ts = max(
                (str(message.get("ts") or "0") for message in messages),
                key=float)
            crm.save_client_onboarding_cursor(row["deal_id"], ts)
    return count


def _contract_event(envelope: CompletedEnvelope, deal: dict, *,
                    content_sha256: str,
                    captured_at: dt.datetime) -> BusinessEvent:
    return BusinessEvent(
        source="docusign", source_id=envelope.envelope_id,
        occurred_at=envelope.completed_at, content_sha256=content_sha256,
        title=envelope.subject or "Executed agreement",
        participants=tuple(
            Participant(name=name, email=email)
            for name, email in zip(envelope.signer_names, envelope.signer_emails)),
        source_url=envelope.source_url,
        account_ids=(deal["account_id"],),
        contact_ids=tuple(deal["contact_ids"]), deal_ids=(deal["id"],),
        captured_at=captured_at.isoformat())


def _record_review_alert(crm, slack, envelope: CompletedEnvelope, message: str,
                         now: dt.datetime, *, deal: dict | None) -> int:
    payload = json.dumps({"envelope_id": envelope.envelope_id}, sort_keys=True)
    event = BusinessEvent(
        source="docusign", source_id=envelope.envelope_id,
        occurred_at=envelope.completed_at,
        content_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        title=envelope.subject or "Completed envelope",
        source_url=envelope.source_url, captured_at=now.isoformat(),
        account_ids=((str(deal["account_id"]),) if deal else ()),
        deal_ids=((str(deal["id"]),) if deal else ()))
    record = crm.claim_agent_event(
        event, {"relevant": False, "_event": event.to_cache()})
    effects = dict(record.get("effects") or {})
    effect_key = event.effect_key(
        "slack", config.DEAL_ALERTS_CHANNEL, kind="contract_review_alert")
    posted = 0
    if effect_key not in effects:
        account = f" for {deal['account_name']}" if deal else ""
        text = (
            f"[docusign:{_sha(envelope.envelope_id)[:12]}] ⚠️ Contract review "
            f"required{account}: {message}. No CRM fields or stage were changed. "
            f"<{envelope.source_url}|Open completed envelope>"
        )
        ts = slack.post(
            config.DEAL_ALERTS_CHANNEL, text,
            client_msg_id=effect_key[:36])
        crm.record_agent_event_effect(str(record["id"]), effect_key, {
            "kind": "contract_review_alert", "message_ts": ts,
        })
        posted = 1
    crm.mark_agent_event_ambiguous(str(record["id"]), message)
    return posted


def _try_review_alert(crm, slack, envelope: CompletedEnvelope, message: str,
                      now: dt.datetime, *, deal: dict | None) -> tuple[int, str]:
    try:
        return _record_review_alert(
            crm, slack, envelope, message, now, deal=deal), ""
    except Exception as error:
        return 0, f"contract review alert failed: {type(error).__name__}"


def _field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _parse(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()
