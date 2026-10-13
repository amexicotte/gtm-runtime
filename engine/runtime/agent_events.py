"""Agent Events ledger orchestration and idempotent monday narrative Updates."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import html
import re
from typing import Any

from .business_events import BusinessEvent


SOURCE_LABELS = {
    "email": "Email",
    "meeting": "Meeting",
    "manual_note": "Manual note",
    "docusign": "DocuSign",
    "slack": "Slack",
    "teams": "Teams",
}


@dataclass
class PublishResult:
    event_key: str = ""
    agent_event_id: str = ""
    targets: int = 0
    posted: int = 0
    deduplicated: int = 0
    disabled: bool = False
    warnings: list[str] = field(default_factory=list)


def render_narrative_update(
        event: BusinessEvent, *, effect_key: str,
        target_type: str = "record") -> str:
    """Render concise rich text with a stable, unobtrusive source marker."""
    timeline = event.timeline_event
    if timeline is None:
        raise ValueError("business event has no material timeline_event")
    occurred = dt.datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
    date_label = occurred.strftime("%b %d, %Y").replace(" 0", " ")
    heading = (
        f"{date_label} — {SOURCE_LABELS[event.source]} · {timeline.headline}"
    )
    paragraphs = [part for part in re.split(r"\n\s*\n", timeline.body) if part][:2]
    body = "<br><br>".join(html.escape(part) for part in paragraphs)
    if timeline.labels:
        labels = " · ".join(html.escape(label) for label in timeline.labels)
        body = f"<strong>{labels}</strong><br>{body}"
    source = event.source_url or "https://monday.com/"
    joiner = "&" if "?" in source else "?"
    marker_url = f"{source}{joiner}harbor_event={effect_key}"
    marker = (
        f'<!--harbor-event:{effect_key}-->'
        f'<a href="{html.escape(marker_url, quote=True)}" '
        f'aria-label="Agent Event {html.escape(event.event_key)}">&#8203;</a>'
    )
    return f"<strong>{html.escape(heading)}</strong><br>{body}{marker}"


def publish_cached_event(
        crm, result: dict, *, activation_at: str = "") -> PublishResult:
    """Publish every missing narrative effect from a cached reasoning result.

    An empty activation timestamp is intentionally disabled: deployment must set
    the cutover explicitly, which prevents accidental historical backfill.
    """
    cached = result.get("_event") if isinstance(result, dict) else None
    if not isinstance(cached, dict):
        return PublishResult(warnings=["reasoning cache has no BusinessEvent"])
    event = BusinessEvent.from_cache(cached)
    outcome = PublishResult(event_key=event.event_key)
    if not activation_at:
        outcome.disabled = True
        return outcome
    if not _at_or_after(event.captured_at, activation_at):
        outcome.disabled = True
        return outcome
    if not hasattr(crm, "claim_agent_event"):
        outcome.warnings.append("monday client has no Agent Events ledger")
        return outcome

    record = crm.claim_agent_event(event, result)
    outcome.agent_event_id = str(record.get("id") or "")
    if not record.get("claimed", True):
        if record.get("busy"):
            raise RuntimeError(
                f"Agent Event {event.event_key} already has a fresh processing claim")
        outcome.deduplicated += len(record.get("effects") or {})
        return outcome
    effects = dict(record.get("effects") or {})
    try:
        _stamp_interaction_effects(
            crm, event, outcome.agent_event_id, effects, inbound_email_at="")
        if result.get("relevant") is not True or event.timeline_event is None:
            if event.timeline_event is None:
                outcome.warnings.append("event has no material timeline narrative")
            crm.complete_agent_event(outcome.agent_event_id)
            return outcome
        published = publish_claimed_event(
            crm, result=result, event=event,
            agent_event_id=outcome.agent_event_id, effects=effects)
        outcome.targets = published.targets
        outcome.posted += published.posted
        outcome.deduplicated += published.deduplicated
        outcome.warnings.extend(published.warnings)
        crm.complete_agent_event(outcome.agent_event_id)
    except Exception as error:
        if outcome.agent_event_id and hasattr(crm, "fail_agent_event"):
            crm.fail_agent_event(outcome.agent_event_id, str(error))
        raise
    return outcome


def publish_claimed_event(crm, *, result: dict, event: BusinessEvent,
                          agent_event_id: str,
                          effects: dict | None = None) -> PublishResult:
    """Publish missing narrative effects for an already-claimed Agent Event."""
    outcome = PublishResult(event_key=event.event_key, agent_event_id=agent_event_id)
    if result.get("relevant") is not True or event.timeline_event is None:
        if event.timeline_event is None:
            outcome.warnings.append("event has no material timeline narrative")
        return outcome
    known = dict(effects or {})
    targets = _narrative_targets(crm, event)
    outcome.targets = len(targets)
    for target_type, target_id in targets:
        effect_key = event.effect_key(target_type, target_id)
        if effect_key in known:
            outcome.deduplicated += 1
            continue
        if (hasattr(crm, "update_has_event_marker") and
                crm.update_has_event_marker(target_id, effect_key)):
            crm.record_agent_event_effect(agent_event_id, effect_key, {
                "kind": "narrative_update", "target_type": target_type,
                "target_id": target_id, "recovered_from_marker": True,
            })
            known[effect_key] = True
            outcome.deduplicated += 1
            continue
        body = render_narrative_update(
            event, effect_key=effect_key, target_type=target_type)
        update_id = str(crm.post_narrative_update(target_id, body))
        crm.record_agent_event_effect(agent_event_id, effect_key, {
            "kind": "narrative_update", "target_type": target_type,
            "target_id": target_id, "update_id": update_id,
        })
        known[effect_key] = True
        outcome.posted += 1
    return outcome


def _stamp_interaction_effects(crm, event: BusinessEvent, agent_event_id: str,
                               effects: dict, *, inbound_email_at: str) -> None:
    if not hasattr(crm, "stamp_last_interaction"):
        return
    target = event.deal_ids[0] if event.deal_ids else (
        event.contact_ids[0] if event.contact_ids else "none")
    effect_key = event.effect_key("interaction", target, kind="stamp")
    if effect_key in effects:
        return
    crm.stamp_last_interaction(
        deal_ids=list(event.deal_ids), contact_ids=list(event.contact_ids),
        occurred_at=event.occurred_at, event_key=event.event_key,
        inbound_email_at=inbound_email_at,
    )
    crm.record_agent_event_effect(agent_event_id, effect_key, {
        "kind": "last_interaction", "deal_ids": list(event.deal_ids),
        "contact_ids": list(event.contact_ids), "occurred_at": event.occurred_at,
        "inbound_email_at": inbound_email_at,
    })
    effects[effect_key] = True


def _narrative_targets(crm, event: BusinessEvent) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    targets.extend(("account", value) for value in event.account_ids)
    targets.extend(("contact", value) for value in event.contact_ids)
    targets.extend(("deal", value) for value in event.deal_ids)
    if (event.account_ids and len(event.deal_ids) == 1 and
            hasattr(crm, "active_client_project_for_deal")):
        project = crm.active_client_project_for_deal(
            event.account_ids[0], event.deal_ids[0])
        if project and project.get("id"):
            targets.append(("client_project", str(project["id"])))
    elif (len(event.account_ids) == 1 and not event.deal_ids and
          hasattr(crm, "client_projects_for_account")):
        projects = [row for row in crm.client_projects_for_account(
            event.account_ids[0]) if row.get("active")]
        if len(projects) == 1 and projects[0].get("id"):
            targets.append(("client_project", str(projects[0]["id"])))
    return list(dict.fromkeys(targets))


def _at_or_after(value: str, threshold: str) -> bool:
    def parse(raw: str) -> dt.datetime:
        parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    return parse(value) >= parse(threshold)
