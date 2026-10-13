"""Pure monday E&A normalization, filtering, threading, and entity resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import re
from typing import Iterable

from matcher import classify_domain

from .business_events import BusinessEvent, Participant


HARBOR_DOMAINS = frozenset({"harboradvisors.example"})
AUTOMATED_LOCAL_PARTS = frozenset({
    "no-reply", "noreply", "notifications", "notification", "mailer-daemon",
    "postmaster", "billing", "receipts", "updates",
})
NOISE_SUBJECTS = re.compile(
    r"\b(?:receipt|invoice|payment received|calendar|invitation|accepted:|"
    r"declined:|tentative:|delivery status|out of office|automatic reply|"
    r"newsletter|digest|security alert|verify your email)\b", re.I)
NOISE_BODY = re.compile(
    r"\b(?:unsubscribe|manage (?:email )?preferences|view in browser|"
    r"this is an automated message|do not reply to this email)\b", re.I)
DELIVERY_WORDS = frozenset({
    "attach", "attached", "attachment", "deliver", "document", "deck",
    "export", "file", "link", "plan", "prep", "proposal", "quote", "recap",
    "scope", "send", "sent", "share", "shared", "sow",
})
VERIFICATION_METADATA_PREFIX = "HARBOR_VERIFY_EMAIL:"


def _iso(value: str) -> str:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat()


def _address(value: str) -> tuple[str, str]:
    name, email = parseaddr(str(value or ""))
    email = email.strip().lower()
    if not email and re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", str(value or "").strip()):
        email = str(value).strip().lower()
    return re.sub(r"\s+", " ", name).strip(), email


def _addresses(values) -> list[tuple[str, str]]:
    if isinstance(values, str):
        values = [values]
    return [parsed for value in (values or [])
            if (parsed := _address(str(value)))[1]]


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].casefold() if "@" in email else ""


def _internal(email: str) -> bool:
    return _domain(email) in HARBOR_DOMAINS


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = {str(key).casefold(): str(value or "") for key, value in attrs}
        classes = attrs.get("class", "").casefold()
        if self._skip_depth or tag in {"script", "style", "blockquote"} or any(
                marker in classes for marker in ("gmail_quote", "gmail_extra", "yahoo_quoted")):
            self._skip_depth += 1
            return
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "a":
            href = attrs.get("href", "").strip()
            if href.startswith(("http://", "https://")):
                self.parts.append(f"\ufff0{len(self.links)}\ufff1")
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def normalize_body(value: str) -> tuple[
        str, tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return readable new-message text and links, excluding quoted history."""
    raw = str(value or "")
    parser = _HTMLText()
    try:
        parser.feed(raw)
        text = "".join(parser.parts) if "<" in raw else raw
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    kept_link_indexes: list[int] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if re.match(r"^(?:On .+ wrote:|From: .+|-{2,}\s*Original Message\s*-{2,})$", line, re.I):
            break
        if line.startswith(">"):
            continue
        if (line in {"--", "—"} or re.match(r"^Sent from my\b", line, re.I) or
                (lines and re.match(
                    r"^(?:best|regards|thanks|sincerely|cheers)[,!]?$", line, re.I))):
            break
        kept_link_indexes.extend(_delivery_link_indexes(line))
        line = re.sub("\ufff0[0-9]+\ufff1", "", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    links = tuple(dict.fromkeys(
        parser.links[index] for index in kept_link_indexes
        if 0 <= index < len(parser.links)))
    contexts = tuple(dict.fromkeys(
        (parser.links[index], _link_sentence(text, index))
        for index in kept_link_indexes if 0 <= index < len(parser.links)))
    return "\n".join(lines).strip(), links, contexts


def _delivery_link_indexes(line: str) -> list[int]:
    """Keep only links whose own sentence describes a delivery action."""
    indexes = []
    for marker in re.finditer("\ufff0([0-9]+)\ufff1", line):
        start = max(line.rfind(token, 0, marker.start()) for token in ".!?") + 1
        endings = [line.find(token, marker.end()) for token in ".!?"
                   if line.find(token, marker.end()) >= 0]
        end = min(endings) + 1 if endings else len(line)
        context = re.sub("\ufff0[0-9]+\ufff1", " ", line[start:end]).casefold()
        words = set(re.findall(r"[a-z]+", context))
        if words & DELIVERY_WORDS:
            indexes.append(int(marker.group(1)))
    return indexes


def _link_sentence(text: str, index: int) -> str:
    marker = f"\ufff0{index}\ufff1"
    position = text.find(marker)
    if position < 0:
        return ""
    start = max(text.rfind(token, 0, position) for token in ".!?\n") + 1
    endings = [text.find(token, position + len(marker)) for token in ".!?\n"
               if text.find(token, position + len(marker)) >= 0]
    end = min(endings) + 1 if endings else len(text)
    return re.sub(r"\s+", " ", re.sub(
        "\ufff0[0-9]+\ufff1", " ", text[start:end])).strip()


@dataclass
class NormalizedEmail:
    timeline_id: str
    subject: str
    occurred_at: str
    sender_name: str
    sender_email: str
    recipients: tuple[tuple[str, str], ...]
    body: str
    links: tuple[str, ...] = ()
    link_contexts: tuple[tuple[str, str], ...] = ()
    direction: str = "unknown"
    observed_contact_ids: set[str] = field(default_factory=set)

    @property
    def all_addresses(self) -> tuple[tuple[str, str], ...]:
        return ((self.sender_name, self.sender_email), *self.recipients)

    @property
    def external_emails(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            email for _name, email in self.all_addresses if email and not _internal(email)))


@dataclass
class ResolvedEmailThread:
    key: str
    subject: str
    messages: tuple[NormalizedEmail, ...]
    event: BusinessEvent
    direction: str
    timeline_ids: tuple[str, ...]
    ambiguities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContactDiscovery:
    """One missing external participant that can be placed without guessing."""

    email: str
    name: str
    account_id: str
    deal_ids: tuple[str, ...] = ()


def discover_missing_contacts(
        messages: list[NormalizedEmail], *, contact_index: dict[str, dict],
        account_index: dict[str, dict], deal_index: dict[str, dict],
        ) -> tuple[ContactDiscovery, ...]:
    """Plan exact-domain Contact creation for previously unknown participants.

    Account identity comes only from an existing unique domain match. A new
    Contact is related to a Deal only when that Account has exactly one active
    Deal; ambiguity leaves the Contact safely Account-only.
    """
    names: dict[str, str] = {}
    external_emails: list[str] = []
    for message in sorted(messages, key=lambda row: (
            row.occurred_at, row.timeline_id)):
        for name, email in message.all_addresses:
            if not email or _internal(email):
                continue
            if email not in external_emails:
                external_emails.append(email)
            if name:
                names[email] = name

    discoveries = []
    for email in external_emails:
        if email in contact_index:
            continue
        domain = _domain(email)
        if classify_domain(domain) != "external":
            continue
        account = account_index.get(domain)
        if not account or not account.get("id"):
            continue
        account_id = str(account["id"])
        active_deals = [
            str(deal_id) for deal_id, deal in deal_index.items()
            if deal.get("active") and account_id in {
                str(value) for value in deal.get("account_ids") or []}
        ]
        discoveries.append(ContactDiscovery(
            email=email,
            name=names.get(email, ""),
            account_id=account_id,
            deal_ids=(active_deals[0],) if len(active_deals) == 1 else (),
        ))
    return tuple(discoveries)


def normalize_timeline_item(
        row: dict, *, verification_custom_activity_id: str = ""
        ) -> NormalizedEmail | None:
    row_type = str(row.get("type") or "").strip().casefold()
    if row_type != "email":
        fixture_id = str(verification_custom_activity_id or "").strip()
        # monday's list endpoint currently reports API-created custom activity
        # rows as ``custom`` (the direct item endpoint reports ``activity``).
        if (row_type not in {"activity", "custom"} or not fixture_id or
                str(row.get("custom_activity_id") or "").strip() != fixture_id):
            return None
        raw_content = str(row.get("content") or "")
        marker, separator, fixture_content = raw_content.partition("\n")
        if not separator or not marker.startswith(VERIFICATION_METADATA_PREFIX):
            return None
        try:
            fixture_metadata = json.loads(
                marker[len(VERIFICATION_METADATA_PREFIX):])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(fixture_metadata, dict):
            return None
        row = {**row, "type": "email", "metadata": fixture_metadata,
               "content": fixture_content}
    metadata = row.get("metadata") or {}
    sender_name, sender_email = _address(metadata.get("from") or "")
    recipients = tuple(dict.fromkeys(
        _addresses(metadata.get("to")) + _addresses(metadata.get("cc")) +
        _addresses(metadata.get("bcc"))))
    if not sender_email:
        return None
    body, links, link_contexts = normalize_body(row.get("content") or "")
    if not body:
        return None
    all_emails = [sender_email, *(email for _name, email in recipients)]
    if all(_internal(email) for email in all_emails):
        return None
    direction = "outbound" if _internal(sender_email) else "inbound"
    return NormalizedEmail(
        timeline_id=str(row.get("id") or "").strip(),
        subject=re.sub(r"\s+", " ", str(row.get("title") or "(no subject)")).strip(),
        occurred_at=_iso(str(row.get("created_at") or "")),
        sender_name=sender_name,
        sender_email=sender_email,
        recipients=recipients,
        body=body,
        links=links,
        link_contexts=link_contexts,
        direction=direction,
    )


def exclusion_reason(message: NormalizedEmail) -> str:
    local = message.sender_email.split("@", 1)[0].casefold()
    if local in AUTOMATED_LOCAL_PARTS or any(
            token in local for token in ("no-reply", "noreply", "notification")):
        return "automated_sender"
    if NOISE_SUBJECTS.search(message.subject):
        return "calendar_receipt_or_notification"
    if NOISE_BODY.search(message.body):
        return "newsletter_or_automated_body"
    return ""


def canonical_subject(value: str) -> str:
    subject = str(value or "").strip()
    while True:
        cleaned = re.sub(r"^(?:re|fw|fwd)\s*:\s*", "", subject, flags=re.I)
        if cleaned == subject:
            break
        subject = cleaned.strip()
    return re.sub(r"\s+", " ", subject).casefold() or "(no subject)"


def thread_key(message: NormalizedEmail) -> str:
    external = ",".join(sorted(message.external_emails))
    raw = f"{canonical_subject(message.subject)}\n{external}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def group_thread_messages(messages: Iterable[NormalizedEmail]) -> list[list[NormalizedEmail]]:
    grouped: dict[str, list[NormalizedEmail]] = {}
    for message in messages:
        grouped.setdefault(thread_key(message), []).append(message)
    return [sorted(group, key=lambda row: (row.occurred_at, row.timeline_id))
            for _key, group in sorted(grouped.items())]


def resolve_thread(
        messages: list[NormalizedEmail], *, contact_index: dict[str, dict],
        deal_index: dict[str, dict], account_index: dict[str, dict] | None = None,
        activity_candidates: list[dict] | None = None,
        captured_at: dt.datetime | None = None) -> ResolvedEmailThread:
    """Resolve exact Contacts/Accounts and at most one active Deal."""
    if not messages:
        raise ValueError("cannot resolve an empty email thread")
    messages = sorted(messages, key=lambda row: (row.occurred_at, row.timeline_id))
    external_emails = tuple(dict.fromkeys(
        email for message in messages for email in message.external_emails))
    contacts = [contact_index[email] for email in external_emails
                if email in contact_index]
    contact_ids = tuple(dict.fromkeys(
        str(row["id"]) for row in contacts if row.get("id")))
    account_ids = list(dict.fromkeys(
        str(item) for row in contacts for item in row.get("account_ids") or [] if item))
    account_index = account_index or {}
    for email in external_emails:
        domain = _domain(email)
        account = (account_index.get(domain)
                   if classify_domain(domain) == "external" else None)
        if account and account.get("id"):
            account_ids.append(str(account["id"]))
    account_ids = tuple(dict.fromkeys(account_ids))
    ambiguities: list[str] = []
    if len(account_ids) > 1:
        ambiguities.append(
            f"email participants resolve to multiple Accounts: {', '.join(account_ids)}")
        resolved_accounts: tuple[str, ...] = ()
    else:
        resolved_accounts = account_ids

    related_deals: set[str] = set()
    if resolved_accounts:
        related_deals.update(
            str(item) for row in contacts for item in row.get("deal_ids") or [] if item)
        for deal_id, deal in deal_index.items():
            if set(deal.get("account_ids") or []) & set(resolved_accounts):
                related_deals.add(str(deal_id))
            if set(deal.get("contact_ids") or []) & set(contact_ids):
                related_deals.add(str(deal_id))
    plausible = [deal_index[item] for item in sorted(related_deals)
                 if item in deal_index and deal_index[item].get("active")]
    deal_ids: tuple[str, ...] = ()
    if len(plausible) == 1:
        deal_ids = (str(plausible[0]["id"]),)
    elif len(plausible) > 1:
        ids = [str(row["id"]) for row in plausible]
        ambiguities.append(f"multiple plausible active Deals: {', '.join(ids)}")

    content_parts = []
    for message in messages:
        content_parts.append(
            f"[{message.timeline_id} | {message.occurred_at} | {message.direction} | "
            f"from {message.sender_email}]\n{message.body}")
    content = "\n\n---\n\n".join(content_parts)
    timeline_ids = tuple(message.timeline_id for message in messages)
    source_material = "\n".join(timeline_ids)
    source_id = "thread:" + hashlib.sha256(source_material.encode("utf-8")).hexdigest()[:40]
    links = tuple(dict.fromkeys(link for message in messages for link in message.links))
    participants = tuple(dict.fromkeys(
        Participant(name=name, email=email,
                    role="sender" if email == messages[-1].sender_email else "recipient")
        for message in messages for name, email in message.all_addresses if email))
    latest = messages[-1]
    event = BusinessEvent(
        source="email",
        source_id=source_id,
        occurred_at=latest.occurred_at,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        title=latest.subject,
        participants=participants,
        source_url=(f"https://harboradvisors.monday.com/boards/10000000002/"
                    f"pulses/{contact_ids[0]}" if contact_ids else ""),
        account_ids=resolved_accounts,
        contact_ids=contact_ids,
        deal_ids=deal_ids,
        content=content,
        attachments=tuple({"type": "link", "url": link} for link in links),
        open_activity_candidates=tuple(activity_candidates or []),
        captured_at=(captured_at or dt.datetime.now(dt.timezone.utc)).isoformat(),
    )
    directions = {message.direction for message in messages}
    direction = directions.pop() if len(directions) == 1 else "mixed"
    return ResolvedEmailThread(
        key=thread_key(latest), subject=latest.subject, messages=tuple(messages),
        event=event, direction=direction, timeline_ids=timeline_ids,
        ambiguities=tuple(ambiguities),
    )


def candidate_keywords(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", str(value).casefold())
            if len(word) > 2 and word not in {
                "and", "for", "from", "the", "this", "that", "with", "will",
                "harbor", "client", "customer", "prospect",
            }}


def rank_activity_candidates(content: str, rows: list[dict], *, limit: int = 8) -> list[dict]:
    words = candidate_keywords(content)
    ranked = []
    for row in rows:
        title_words = candidate_keywords(row.get("title") or "")
        score = len(words & title_words)
        if not score:
            continue
        ranked.append({
            **row,
            "lexical_score": score,
            "requires_delivery_evidence": bool(title_words & DELIVERY_WORDS),
        })
    ranked.sort(key=lambda row: (-row["lexical_score"], str(row.get("id") or "")))
    for row in ranked:
        row["unique_match"] = len(ranked) == 1
    return ranked[:limit]
