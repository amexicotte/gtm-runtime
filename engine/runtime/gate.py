"""Three-way monday matcher/gate and resolve-or-create orchestration.

This is deterministic: it never reads a transcript and never calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from matcher import (
    CompanyMatch, classify_domain, classify_meeting, domain_of, parse_domains,
)


@dataclass
class GateOutcome:
    state: str                         # Gated | Ready
    reason: str
    ctx: dict | None = None
    slack_ts: str | None = None


def process_meeting(crm, slack, *, meeting_id: str, title: str,
                    attendee_emails: list[str] | str,
                    attendee_names: dict[str, str] | None = None,
                    prospect_choice: str | int | None = None,
                    alerts_channel: str = "deal-alerts") -> GateOutcome:
    """Apply the 0/1/2+ gate and return the writer context when ready.

    ``prospect_choice`` is required only when 2+ company domains are present;
    it may be the displayed 1-based number or a bare domain.
    """
    emails = normalize_emails(attendee_emails)
    attendee_names = {str(key).lower(): str(value)
                      for key, value in (attendee_names or {}).items()}

    def lookup(domain: str) -> CompanyMatch:
        account = crm.find_account_by_domain(domain)
        return CompanyMatch(
            domain=domain, exists=bool(account),
            company_url=str(account["id"]) if account else None,
            name=account.get("name") if account else None,
        )

    match = classify_meeting(emails, lookup, title=title)
    domains = match.external_domains

    if not domains:
        crm.set_meeting_gated(meeting_id, match.meeting_type)
        return GateOutcome("Gated", match.reason)

    if len(domains) > 1 and prospect_choice is None:
        prompt = f"[meeting:{meeting_id}]\n{match.slack_prompt}"
        slack_ts = slack.post(alerts_channel, prompt)
        crm.set_meeting_gated(meeting_id, match.meeting_type)
        return GateOutcome("Gated", match.reason, slack_ts=slack_ts)

    prospect_domain = (
        domains[0] if len(domains) == 1
        else select_prospect(domains, prospect_choice)
    )
    if prospect_domain is None:
        crm.set_meeting_gated(meeting_id, match.meeting_type)
        return GateOutcome("Gated", f"invalid prospect choice: {prospect_choice!r}")

    accounts = {domain: _resolve_account(crm, domain) for domain in domains}
    contact_ids = []
    contact_email_ids = {}
    created_prospect_contact_ids = []
    for email in emails:
        domain = domain_of(email)
        if domain not in accounts:
            continue
        existing = crm.find_contact_by_email(email)
        contact_id = (str(existing["id"]) if existing else
                      str(crm.create_contact(
                          email, accounts[domain], name=attendee_names.get(email))))
        if contact_id not in contact_ids:
            contact_ids.append(contact_id)
        if not existing and domain == prospect_domain:
            created_prospect_contact_ids.append(contact_id)
        contact_email_ids[email] = contact_id

    prospect_id = accounts[prospect_domain]
    partner_ids = [accounts[domain] for domain in domains if domain != prospect_domain]
    active_deals = (crm.active_deals_for_account(prospect_id)
                    if hasattr(crm, "active_deals_for_account") else [])
    if len(active_deals) > 1:
        names = ", ".join(deal.get("name") or deal["id"] for deal in active_deals)
        slack.post(
            alerts_channel,
            f"[meeting:{meeting_id}] ⚠️ {title} found multiple active Deals for "
            f"the same Account: {names}. Close the duplicate before processing; "
            "the engine will not guess.",
        )
        crm.set_meeting_gated(meeting_id, match.meeting_type)
        return GateOutcome("Gated", "multiple active Deals; human cleanup required")
    try:
        deal_id = (crm.preferred_deal_for_account(prospect_id)
                   if hasattr(crm, "preferred_deal_for_account") else
                   (str(active_deals[0]["id"]) if active_deals else
                    crm.find_deal_for_account(prospect_id)))
    except RuntimeError as error:
        slack.post(alerts_channel, f"[meeting:{meeting_id}] ⚠️ {error}")
        crm.set_meeting_gated(meeting_id, match.meeting_type)
        return GateOutcome("Gated", str(error))
    if (deal_id and created_prospect_contact_ids and
            hasattr(crm, "link_contacts_to_deal")):
        crm.link_contacts_to_deal(created_prospect_contact_ids, str(deal_id))
    crm.set_meeting_ready(
        meeting_id, [prospect_id, *partner_ids], contact_ids, deal_id,
        meeting_type=match.meeting_type, contact_email_ids=contact_email_ids,
    )
    ctx = {
        "meeting_id": str(meeting_id),
        "prospect_account_id": prospect_id,
        "partner_account_id": partner_ids[0] if partner_ids else None,
        "deal_id": deal_id,
        "contact_ids": contact_ids,
        "contact_email_ids": contact_email_ids,
    }
    # The specified writer contract is singular. Preserve it and also carry all
    # partners so 3+ company calls do not silently lose a relationship.
    if len(partner_ids) > 1:
        ctx["partner_account_ids"] = partner_ids
    return GateOutcome("Ready", f"prospect resolved to {prospect_domain}", ctx=ctx)


def resume_from_slack(crm, slack, *, meeting_id: str, title: str,
                      attendee_emails: list[str] | str,
                      attendee_names: dict[str, str] | None = None,
                      alerts_channel: str = "deal-alerts") -> GateOutcome:
    """Resume a multi-company meeting only after a human thread reply.

    Gate prompts carry a stable meeting marker, so similarly titled meetings do
    not cross-wire. This function never posts another prompt while waiting.
    """
    emails = normalize_emails(attendee_emails)
    domains = parse_domains(emails)["external"]
    if len(domains) < 2:
        return process_meeting(
            crm, slack, meeting_id=meeting_id, title=title,
            attendee_emails=emails, attendee_names=attendee_names,
            alerts_channel=alerts_channel,
        )
    marker = f"[meeting:{meeting_id}]"
    roots = slack.history(alerts_channel)
    root = next((message for message in roots
                 if marker in (message.get("text") or "")), None)
    if not root:
        return GateOutcome("Gated", "Slack gate prompt not found")
    replies = slack.replies(alerts_channel, root["ts"])
    human = next((message for message in replies
                  if message.get("ts") != root["ts"] and
                  not message.get("bot_id") and not message.get("subtype")), None)
    if not human:
        return GateOutcome("Gated", "waiting for human prospect choice",
                           slack_ts=root["ts"])
    reply = (human.get("text") or "").strip()
    number = re.search(r"\b\d+\b", reply)
    choice: str | int = int(number.group()) if number else reply.lower()
    return process_meeting(
        crm, slack, meeting_id=meeting_id, title=title,
        attendee_emails=emails, attendee_names=attendee_names,
        prospect_choice=choice,
        alerts_channel=alerts_channel,
    )


def resume_background_from_slack(
        crm, slack, *, meeting_id: str, title: str,
        attendee_emails: list[str] | str,
        prompt_ts: str, attendee_names: dict[str, str] | None = None,
        call_notes_channel: str = "call-notes",
        alerts_channel: str = "deal-alerts") -> GateOutcome:
    """Resolve or scrap a recording that has no identifying attendee domain.

    The exact ``#call-notes`` thread is the human gate. A valid mapping reply
    contains a company domain and at least one real contact email. ``scrap``
    tombstones the monday Meeting so its Fireflies ID remains available to the
    ingestion deduper while the recording is excluded from further processing.
    """
    replies = slack.replies(call_notes_channel, prompt_ts)
    human = [
        message for message in replies
        if str(message.get("ts") or "") != str(prompt_ts)
        and not message.get("bot_id") and not message.get("subtype")
        and (message.get("text") or "").strip()
    ]
    human.sort(key=lambda item: float(item.get("ts") or 0), reverse=True)
    for message in human:
        text = (message.get("text") or "").strip()
        if is_scrap_command(text):
            crm.scrap_meeting(meeting_id)
            slack.post(
                call_notes_channel,
                "Scrapped — this recording was marked ignored in monday and will "
                "not be processed or imported again.",
                thread_ts=prompt_ts,
            )
            return GateOutcome("Scrapped", "human scrapped background recording")

        identity = parse_background_identity(text)
        if not identity:
            continue
        company_domain, contact_emails, supplied_names = identity
        account_id = _resolve_account(crm, company_domain)
        contact_ids = []
        contact_email_ids = {}
        created_contact_ids = []
        for email in contact_emails:
            existing = crm.find_contact_by_email(email)
            contact_id = (str(existing["id"]) if existing else
                          str(crm.create_contact(
                              email, account_id, name=supplied_names.get(email))))
            if contact_id not in contact_ids:
                contact_ids.append(contact_id)
            if not existing:
                created_contact_ids.append(contact_id)
            contact_email_ids[email] = contact_id

        active_deals = (crm.active_deals_for_account(account_id)
                        if hasattr(crm, "active_deals_for_account") else [])
        if len(active_deals) > 1:
            names = ", ".join(deal.get("name") or deal["id"]
                              for deal in active_deals)
            slack.post(
                alerts_channel,
                f"[meeting:{meeting_id}] ⚠️ {title} found multiple active Deals "
                f"for the same Account: {names}. Close the duplicate before "
                "processing; the engine will not guess.",
            )
            return GateOutcome("Gated", "multiple active Deals; human cleanup required")
        try:
            deal_id = (crm.preferred_deal_for_account(account_id)
                       if hasattr(crm, "preferred_deal_for_account") else
                       (str(active_deals[0]["id"]) if active_deals else
                        crm.find_deal_for_account(account_id)))
        except RuntimeError as error:
            slack.post(alerts_channel, f"[meeting:{meeting_id}] ⚠️ {error}")
            return GateOutcome("Gated", str(error))
        if (deal_id and created_contact_ids and
                hasattr(crm, "link_contacts_to_deal")):
            crm.link_contacts_to_deal(created_contact_ids, str(deal_id))
        original = normalize_emails(attendee_emails)
        corrected = list(dict.fromkeys([*original, *contact_emails]))
        names = {str(key).lower(): str(value)
                 for key, value in (attendee_names or {}).items()}
        names.update(supplied_names)
        crm.save_meeting_attendees(meeting_id, corrected, names)
        crm.set_meeting_ready(
            meeting_id, [account_id], contact_ids, deal_id,
            meeting_type="External", contact_email_ids=contact_email_ids,
        )
        return GateOutcome("Ready", f"manually mapped to {company_domain}", ctx={
            "meeting_id": str(meeting_id),
            "prospect_account_id": account_id,
            "partner_account_id": None,
            "deal_id": deal_id,
            "contact_ids": contact_ids,
            "contact_email_ids": contact_email_ids,
        })
    return GateOutcome(
        "Gated", "waiting for company domain and contact email, or scrap")


def is_scrap_command(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    return normalized in {
        "scrap", "scrap meeting", "scrap this meeting",
        "delete", "delete meeting", "delete this meeting",
        "ignore", "ignore meeting", "ignore this meeting",
    }


def parse_background_identity(
        text: str) -> tuple[str, list[str], dict[str, str]] | None:
    """Parse the explicit mapping template from a background-recording reply."""
    emails = normalize_emails(text)
    contacts = [
        email for email in emails
        if classify_domain(domain_of(email) or "") not in {
            "harbor", "notetaker", "internal_adjacent",
        }
    ]
    if not contacts:
        return None

    explicit_domain = None
    line = re.search(
        r"(?im)^\s*(?:company\s+)?domain\s*:\s*([^\n]+)$", text or "")
    if not line:
        line = re.search(r"(?im)^\s*company\s*:\s*([^\n]+)$", text or "")
    if line:
        match = re.search(
            r"(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,})",
            line.group(1), re.I)
        if match:
            explicit_domain = match.group(1).lower().rstrip(".")
    business_domains = parse_domains(contacts)["external"]
    company_domain = explicit_domain or (
        business_domains[0] if len(business_domains) == 1 else None)
    if not company_domain or classify_domain(company_domain) != "external":
        return None

    names: dict[str, str] = {}
    for contact_line in re.finditer(
            r"(?im)^\s*contact(?:\s+\d+)?\s*:\s*([^\n]+)$", text or ""):
        value = contact_line.group(1).strip()
        found = normalize_emails(value)
        if not found:
            continue
        email = found[0]
        name = value[:value.lower().find(email.lower())].strip(" <([:-")
        if name:
            names[email] = name
    return company_domain, contacts, names


def normalize_emails(value: list[str] | str) -> list[str]:
    values = value if isinstance(value, list) else re.split(r"[,;\n\s]+", value or "")
    out = []
    for raw in values:
        match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", raw or "", re.I)
        if match:
            email = match.group(0).lower().rstrip(".")
            if email not in out:
                out.append(email)
    return out


def select_prospect(domains: list[str], choice: str | int | None) -> str | None:
    if isinstance(choice, int) or (isinstance(choice, str) and choice.strip().isdigit()):
        index = int(choice) - 1
        return domains[index] if 0 <= index < len(domains) else None
    selected = str(choice or "").strip().lower()
    return selected if selected in domains else None


def _resolve_account(crm, domain: str) -> str:
    account = crm.find_account_by_domain(domain)
    return str(account["id"]) if account else str(crm.create_account(domain))
