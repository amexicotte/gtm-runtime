"""Normalized, source-agnostic business events for CRM listening effects.

Adapters build a :class:`BusinessEvent`; they never mutate CRM state.  Meeting
and manual-note processing persist the compact event envelope beside the cached
reasoning result so retries can resume deterministic effects without another
model call or another source read.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime as dt
import hashlib
import json
import re
from typing import Any


SUPPORTED_SOURCES = frozenset({
    "email", "meeting", "manual_note", "docusign", "slack", "teams",
})
ALLOWED_TIMELINE_LABELS = frozenset({
    "Decision", "Risk", "Next step", "Task completed",
})


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso_datetime(value: str, *, default: dt.datetime | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        parsed = default or dt.datetime.now(dt.timezone.utc)
    else:
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = dt.datetime.combine(
                    dt.date.fromisoformat(raw[:10]), dt.time.min,
                    tzinfo=dt.timezone.utc,
                )
            except ValueError as error:
                raise ValueError(f"invalid event timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat()


def _clean_line(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip()


@dataclass(frozen=True)
class Participant:
    name: str = ""
    email: str = ""
    role: str = "recipient"

    def __post_init__(self) -> None:
        role = self.role.strip().lower()
        if role not in {"sender", "recipient"}:
            raise ValueError(f"unsupported participant role: {self.role!r}")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "name", _clean_line(self.name, limit=200))
        object.__setattr__(self, "email", self.email.strip().lower())


@dataclass(frozen=True)
class TimelineEvent:
    headline: str
    body: str
    labels: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict | None) -> "TimelineEvent | None":
        if not isinstance(value, dict):
            return None
        headline = _clean_line(value.get("headline"), limit=160)
        body = str(value.get("body") or "").strip()
        body = "\n\n".join(
            re.sub(r"\s+", " ", part).strip()
            for part in re.split(r"\n\s*\n", body) if part.strip()
        )
        if not headline or not body:
            return None
        labels = tuple(dict.fromkeys(
            label for raw in (value.get("labels") or [])
            if (label := _clean_line(raw, limit=40)) in ALLOWED_TIMELINE_LABELS
        ))
        # One event is deliberately concise.  The model is asked for 30-70
        # words; leave room for the source/date heading and optional labels so
        # the rendered Update remains below the 100-word hard limit.
        words = body.split()
        narrative_limit = max(
            1, 90 - len(headline.split())
            - sum(len(label.split()) for label in labels))
        if len(words) > narrative_limit:
            body = " ".join(words[:narrative_limit]).rstrip(" ,;:") + "…"
        return cls(headline=headline, body=body, labels=labels)

    @property
    def word_count(self) -> int:
        return len(f"{self.headline} {self.body}".split())


@dataclass(frozen=True)
class BusinessEvent:
    source: str
    source_id: str
    occurred_at: str
    content_sha256: str
    title: str
    participants: tuple[Participant, ...] = ()
    source_url: str = ""
    account_ids: tuple[str, ...] = ()
    contact_ids: tuple[str, ...] = ()
    deal_ids: tuple[str, ...] = ()
    content: str = field(default="", repr=False)
    attachments: tuple[dict, ...] = ()
    crm_snapshot: dict = field(default_factory=dict, repr=False)
    open_activity_candidates: tuple[dict, ...] = ()
    timeline_event: TimelineEvent | None = None
    captured_at: str = ""

    def __post_init__(self) -> None:
        source = self.source.strip().lower()
        if source not in SUPPORTED_SOURCES:
            raise ValueError(f"unsupported business-event source: {self.source!r}")
        if not self.source_id.strip():
            raise ValueError("business event requires a stable source_id")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256.lower()):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "occurred_at", _iso_datetime(self.occurred_at))
        object.__setattr__(self, "captured_at", _iso_datetime(self.captured_at))
        object.__setattr__(self, "title", _clean_line(self.title, limit=240))

    @property
    def event_key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def effect_key(self, target_type: str, target_id: str,
                   kind: str = "narrative") -> str:
        raw = f"{self.event_key}:{kind}:{target_type}:{target_id}"
        return _sha256(raw)

    def to_cache(self) -> dict:
        payload = asdict(self)
        # Full transcripts/manual notes never need to be duplicated into the
        # reasoning cache or Agent Events board.  Their durable hash is enough.
        payload.pop("content", None)
        payload.pop("crm_snapshot", None)
        payload.pop("open_activity_candidates", None)
        return payload

    @classmethod
    def from_cache(cls, value: dict) -> "BusinessEvent":
        if not isinstance(value, dict):
            raise TypeError("cached business event must be an object")
        participants = tuple(Participant(**row) for row in value.get("participants") or [])
        timeline = TimelineEvent.from_dict(value.get("timeline_event"))
        return cls(
            source=value.get("source") or "",
            source_id=value.get("source_id") or "",
            occurred_at=value.get("occurred_at") or "",
            content_sha256=value.get("content_sha256") or "",
            title=value.get("title") or "",
            participants=participants,
            source_url=value.get("source_url") or "",
            account_ids=tuple(str(item) for item in value.get("account_ids") or []),
            contact_ids=tuple(str(item) for item in value.get("contact_ids") or []),
            deal_ids=tuple(str(item) for item in value.get("deal_ids") or []),
            attachments=tuple(value.get("attachments") or []),
            timeline_event=timeline,
            captured_at=value.get("captured_at") or "",
        )


def attach_business_event(
        result: dict, *, source: str, meeting_id: str, occurred_at: str,
        title: str, content: str, ctx: dict, source_url: str = "",
        captured_at: dt.datetime | None = None) -> BusinessEvent:
    """Attach a retry-safe event envelope to one cached reasoning result."""
    if not isinstance(result, dict):
        raise TypeError("reasoning result must be a JSON object")
    if isinstance(result.get("_event"), dict):
        return BusinessEvent.from_cache(result["_event"])

    stable_result = {
        key: value for key, value in result.items() if key not in {"_event"}
    }
    reasoning_sha = _sha256(json.dumps(
        stable_result, sort_keys=True, separators=(",", ":"), default=str))
    source_id = f"{meeting_id}:{reasoning_sha[:20]}"
    occurred = _iso_datetime(occurred_at)
    content_sha = _sha256(content)

    names_by_email = {
        str(row.get("email") or "").strip().lower(): str(row.get("name") or "")
        for row in ((ctx.get("crm_snapshot") or {}).get("contacts") or [])
        if row.get("email")
    }
    participants = tuple(
        Participant(name=names_by_email.get(email, ""), email=email)
        for email in dict.fromkeys(
            str(item).strip().lower() for item in ctx.get("attendee_emails") or []
            if str(item).strip()
        )
    )
    event = BusinessEvent(
        source=source,
        source_id=source_id,
        occurred_at=occurred,
        content_sha256=content_sha,
        title=title,
        participants=participants,
        source_url=source_url,
        account_ids=tuple(dict.fromkeys(
            str(item) for item in [ctx.get("prospect_account_id")]
            if item
        )),
        contact_ids=tuple(dict.fromkeys(
            str(item) for item in ctx.get("contact_ids") or [] if item
        )),
        deal_ids=tuple(dict.fromkeys(
            str(item) for item in [ctx.get("deal_id")] if item
        )),
        content=content,
        crm_snapshot=ctx.get("crm_snapshot") or {},
        timeline_event=TimelineEvent.from_dict(result.get("timeline_event")),
        captured_at=(captured_at or dt.datetime.now(dt.timezone.utc)).isoformat(),
    )
    result["_event"] = event.to_cache()
    return event


def retarget_business_event(result: dict, ctx: dict) -> bool:
    """Refresh deterministic entity IDs after a Deal is safely created."""
    cached = result.get("_event") if isinstance(result, dict) else None
    if not isinstance(cached, dict):
        return False
    original = BusinessEvent.from_cache(cached).to_cache()
    updated = dict(cached)
    updated["account_ids"] = tuple(dict.fromkeys(
        str(item) for item in [ctx.get("prospect_account_id")] if item))
    updated["contact_ids"] = tuple(dict.fromkeys(
        str(item) for item in ctx.get("contact_ids") or [] if item))
    updated["deal_ids"] = tuple(dict.fromkeys(
        str(item) for item in [ctx.get("deal_id")] if item))
    # Validate the changed envelope before replacing the durable cached value.
    validated = BusinessEvent.from_cache(updated)
    replacement = validated.to_cache()
    if replacement == original:
        return False
    result["_event"] = replacement
    return True
