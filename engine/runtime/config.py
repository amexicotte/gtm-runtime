"""Runtime configuration — all from env vars, no secrets in code.

Secrets live in GitHub Actions secrets (or a local .env for testing). See
../.env.example. Nothing here is written to the repo.
"""
from __future__ import annotations

import os
import datetime as dt
from zoneinfo import ZoneInfo


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"missing required env var: {name}")
    return v


def _opt(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


class Config:
    # --- Reasoning (OpenRouter, OpenAI-compatible) ---
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = _opt("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    # Verify the exact slug on openrouter.ai/models — DeepSeek V4-Pro is post-cutoff.
    OPENROUTER_MODEL = _opt("OPENROUTER_MODEL", "deepseek/deepseek-v4-pro")

    # --- Notion ---
    NOTION_API_TOKEN = os.environ.get("NOTION_API_TOKEN", "")
    # Notion API version. The data-sources API is 2025-09-03+. Confirm against the
    # token; if the classic databases API is in play, switch endpoints in notion.py.
    NOTION_VERSION = _opt("NOTION_VERSION", "2025-09-03")

    # --- Bounded business context (Phase 3) ---
    BUSINESS_CONTEXT_ENABLED = _bool("BUSINESS_CONTEXT_ENABLED", False)
    GOOGLE_DRIVE_RCLONE_REMOTE = _opt(
        "GOOGLE_DRIVE_RCLONE_REMOTE", "harbor-drive")
    GOOGLE_DRIVE_WRITER_RCLONE_REMOTE = _opt(
        "GOOGLE_DRIVE_WRITER_RCLONE_REMOTE", "harbor-drive-writer")
    HARBOR_WEBSITE_URL = _opt("HARBOR_WEBSITE_URL", "https://harboradvisors.example/")
    BUSINESS_CONTEXT_MAX_TOOL_CALLS = int(_opt(
        "BUSINESS_CONTEXT_MAX_TOOL_CALLS", "6"))
    BUSINESS_CONTEXT_MAX_ENTITY_IDS = int(_opt(
        "BUSINESS_CONTEXT_MAX_ENTITY_IDS", "20"))
    BUSINESS_CONTEXT_MAX_UPDATES = int(_opt(
        "BUSINESS_CONTEXT_MAX_UPDATES", "10"))
    BUSINESS_CONTEXT_MAX_DOC_RESULTS = int(_opt(
        "BUSINESS_CONTEXT_MAX_DOC_RESULTS", "5"))
    BUSINESS_CONTEXT_MAX_EXCERPT_CHARS = int(_opt(
        "BUSINESS_CONTEXT_MAX_EXCERPT_CHARS", "4000"))
    BUSINESS_CONTEXT_MAX_PREFETCH_CHARS = int(_opt(
        "BUSINESS_CONTEXT_MAX_PREFETCH_CHARS", "12000"))
    BUSINESS_CONTEXT_MAX_FILE_BYTES = int(_opt(
        "BUSINESS_CONTEXT_MAX_FILE_BYTES", "5000000"))

    # --- Advisory portfolio pattern learning (Phase 5) ---
    PORTFOLIO_PATTERN_ENABLED = _bool("PORTFOLIO_PATTERN_ENABLED", False)
    PORTFOLIO_REASONING_APPROVED = _bool(
        "PORTFOLIO_REASONING_APPROVED", False)
    PORTFOLIO_PATTERN_WRITES_APPROVED = _bool(
        "PORTFOLIO_PATTERN_WRITES_APPROVED", False)
    PORTFOLIO_MAX_TERMINAL_DEALS = int(_opt(
        "PORTFOLIO_MAX_TERMINAL_DEALS", "200"))
    PORTFOLIO_UPDATES_PER_DEAL = int(_opt(
        "PORTFOLIO_UPDATES_PER_DEAL", "10"))
    PORTFOLIO_MIN_COHORT_SIZE = int(_opt(
        "PORTFOLIO_MIN_COHORT_SIZE", "3"))

    # --- DocuSign executed-contract processing (Phase 4) ---
    DOCUSIGN_INTEGRATION_KEY = os.environ.get(
        "DOCUSIGN_INTEGRATION_KEY", "").strip()
    DOCUSIGN_USER_ID = os.environ.get("DOCUSIGN_USER_ID", "").strip()
    DOCUSIGN_ACCOUNT_ID = os.environ.get("DOCUSIGN_ACCOUNT_ID", "").strip()
    DOCUSIGN_AUTH_SERVER = _opt(
        "DOCUSIGN_AUTH_SERVER", "account.docusign.com")
    DOCUSIGN_PRIVATE_KEY = os.environ.get("DOCUSIGN_PRIVATE_KEY", "")
    DOCUSIGN_PRIVATE_KEY_PATH = os.environ.get(
        "DOCUSIGN_PRIVATE_KEY_PATH", "").strip()
    DOCUSIGN_ACTIVATED_AT = os.environ.get(
        "DOCUSIGN_ACTIVATED_AT", "").strip()
    DOCUSIGN_RECONCILIATION_OVERLAP_HOURS = int(_opt(
        "DOCUSIGN_RECONCILIATION_OVERLAP_HOURS", "168"))
    DOCUSIGN_MAX_ENVELOPES_PER_RUN = int(_opt(
        "DOCUSIGN_MAX_ENVELOPES_PER_RUN", "20"))
    DOCUSIGN_AUTOMATIC_WRITES_APPROVED = _bool(
        "DOCUSIGN_AUTOMATIC_WRITES_APPROVED", False)
    DOCUSIGN_REASONING_APPROVED = _bool(
        "DOCUSIGN_REASONING_APPROVED", False)
    DOCUSIGN_DOCUMENT_ARCHIVE_APPROVED = _bool(
        "DOCUSIGN_DOCUMENT_ARCHIVE_APPROVED", False)

    # --- Slack ---
    SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
    CALL_NOTES_CHANNEL = _opt("SLACK_CALL_NOTES_CHANNEL", "call-notes")
    DEAL_ALERTS_CHANNEL = _opt("SLACK_DEAL_ALERTS_CHANNEL", "deal-alerts")
    MANUAL_NOTES_LOOKBACK_DAYS = int(_opt("SLACK_MANUAL_NOTES_LOOKBACK_DAYS", "14"))

    # --- monday + Fireflies polling ---
    MONDAY_API_TOKEN = os.environ.get("MONDAY_API_TOKEN", "")
    MONDAY_API_VERSION = _opt("MONDAY_API_VERSION", "2026-07")
    MONDAY_AGENT_EVENTS_BOARD_ID = os.environ.get(
        "MONDAY_AGENT_EVENTS_BOARD_ID", "").strip()
    # Blank is deliberately disabled so deploying Phase 1 cannot backfill old
    # Meetings before an explicit, timezone-aware cutover is approved.
    BUSINESS_EVENTS_ACTIVATED_AT = os.environ.get(
        "BUSINESS_EVENTS_ACTIVATED_AT", "").strip()
    # Email listening has its own explicit cutover. Shadow runs may use the
    # cutover read-only, but live effects remain independently approval-gated.
    EMAIL_LISTENING_ACTIVATED_AT = os.environ.get(
        "EMAIL_LISTENING_ACTIVATED_AT", "").strip()
    EMAIL_RECONCILIATION_OVERLAP_HOURS = int(_opt(
        "EMAIL_RECONCILIATION_OVERLAP_HOURS", "72"))
    MAX_EMAIL_TIMELINE_ITEMS_PER_RUN = int(_opt(
        "MAX_EMAIL_TIMELINE_ITEMS_PER_RUN", "500"))
    MAX_EMAIL_THREADS_PER_RUN = int(_opt("MAX_EMAIL_THREADS_PER_RUN", "50"))
    MAX_EMAIL_REASONING_PER_RUN = int(_opt(
        "MAX_EMAIL_REASONING_PER_RUN", "20"))
    EMAIL_COMPLETION_CONFIDENCE = float(_opt(
        "EMAIL_COMPLETION_CONFIDENCE", "0.90"))
    # Non-secret monday custom-activity ID used only with an explicit reversible
    # Contact allowlist. Scheduled ingestion never treats this fixture as email.
    EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID = os.environ.get(
        "EMAIL_VERIFICATION_CUSTOM_ACTIVITY_ID", "").strip()
    # Explicit non-destructive resolution of a duplicate exact-email identity:
    # JSON object of SHA-256(lowercase email) -> canonical monday Contact ID.
    EMAIL_CONTACT_IDENTITY_RESOLUTIONS = os.environ.get(
        "EMAIL_CONTACT_IDENTITY_RESOLUTIONS", "").strip()
    EMAIL_AUTOMATIC_WRITES_APPROVED = _bool(
        "EMAIL_AUTOMATIC_WRITES_APPROVED", False)
    BUSINESS_LISTENING_WRITE_KILL_SWITCH = _bool(
        "BUSINESS_LISTENING_WRITE_KILL_SWITCH", False)
    # Separate data-processing consent: live E&A bodies do not leave monday
    # until an authorized owner approves the configured reasoning provider.
    EMAIL_REASONING_APPROVED = _bool("EMAIL_REASONING_APPROVED", False)
    EMAIL_SHADOW_REVIEW_APPROVED = _bool(
        "EMAIL_SHADOW_REVIEW_APPROVED", False)
    # Owner-authorized alternative to the calendar shadow: deterministic CI plus
    # one exact disposable live canary and a zero-reasoning idempotent retry.
    EMAIL_IMMEDIATE_ROLLOUT_APPROVED = _bool(
        "EMAIL_IMMEDIATE_ROLLOUT_APPROVED", False)
    # Comma-separated America/New_York calendar dates from reviewed nightly
    # workflow artifacts. Ad-hoc/manual passes deliberately do not count.
    EMAIL_SHADOW_OBSERVATION_DATES = tuple(
        value.strip() for value in os.environ.get(
            "EMAIL_SHADOW_OBSERVATION_DATES", "").split(",") if value.strip())
    EMAIL_SHADOW_MIN_NIGHTS = int(_opt("EMAIL_SHADOW_MIN_NIGHTS", "3"))
    NUDGE_STALLED_HOURS = int(_opt("NUDGE_STALLED_HOURS", "36"))
    NUDGE_UNANSWERED_HOURS = int(_opt("NUDGE_UNANSWERED_HOURS", "48"))
    SLACK_USER_IDS_BY_MONDAY_ID = os.environ.get(
        "SLACK_USER_IDS_BY_MONDAY_ID", "").strip()
    FIREFLIES_API_KEY = os.environ.get("FIREFLIES_API_KEY", "")
    FIREFLIES_LOOKBACK_DAYS = int(_opt("FIREFLIES_LOOKBACK_DAYS", "2"))
    FIREFLIES_NOT_BEFORE = os.environ.get("FIREFLIES_NOT_BEFORE", "").strip()
    # Default CRM accountability owner. Override in GitHub variables if the
    # operating owner changes; every new Account, Deal, and Activity gets one.
    MONDAY_DEFAULT_OWNER_ID = _opt("MONDAY_DEFAULT_OWNER_ID", "100000007")

    # --- Cost / safety guardrails ---
    # Hard ceiling for new Fireflies rows ingested in one reconciliation pass.
    MAX_MEETINGS_PER_RUN = int(_opt("MAX_MEETINGS_PER_RUN", "20"))
    # Keep slow model work bounded so one poll finishes before the next cadence.
    MAX_REASONING_PER_RUN = int(_opt("MAX_REASONING_PER_RUN", "3"))
    # A killed runner can leave a claim without a saved result. Release it after
    # this window; cached results are always resumed without another model call.
    PROCESSING_STALE_MINUTES = int(_opt("PROCESSING_STALE_MINUTES", "30"))
    # Fireflies occasionally creates a permanent shell with no sentences.
    TRANSCRIPT_WAIT_HOURS = int(_opt("TRANSCRIPT_WAIT_HOURS", "24"))
    # Process on transcript alone if notes never arrive after this many hours.
    NOTES_FALLBACK_HOURS = int(_opt("NOTES_FALLBACK_HOURS", "12"))

    # --- Live data source IDs (confirmed under Harbor Advisors HQ, 2026-07-28) ---
    DS_MEETINGS = "4c64743a-d5f3-821a-a437-079879c23879"
    DS_COMPANIES = "f6c4743a-d5f3-83a1-9fc3-07e6670d5709"
    DS_CONTACTS = "c4f4743a-d5f3-83ea-a272-07aa7574ce23"
    DS_OPPORTUNITIES = "14d4743a-d5f3-8254-992b-07cb10000013"
    DS_TASKS = "84e4cfb6-a1ed-4c8b-b8d3-b884bbf4c6fe"

    # Harbor's own + notetaker domains live in matcher/domains.py (single source).

    def require_all(self) -> None:
        """Fail loud if a real run is missing secrets (skip in --dry-run)."""
        for name in ("OPENROUTER_API_KEY", "NOTION_API_TOKEN", "SLACK_BOT_TOKEN"):
            _req(name)

    def require_monday(self) -> None:
        for name in ("MONDAY_API_TOKEN", "FIREFLIES_API_KEY",
                     "OPENROUTER_API_KEY", "SLACK_BOT_TOKEN"):
            _req(name)

    def require_email_shadow(self) -> None:
        for name in ("MONDAY_API_TOKEN", "OPENROUTER_API_KEY"):
            _req(name)
        if not self.EMAIL_REASONING_APPROVED:
            raise RuntimeError(
                "EMAIL_REASONING_APPROVED is required before live email bodies "
                "may be sent to the configured reasoning provider")

    def require_email_ingest(self) -> None:
        _req("MONDAY_API_TOKEN")

    def require_email_live(self) -> None:
        self.require_business_writes()
        for name in ("MONDAY_API_TOKEN", "OPENROUTER_API_KEY", "SLACK_BOT_TOKEN"):
            _req(name)
        if not self.EMAIL_REASONING_APPROVED:
            raise RuntimeError("email reasoning data processing is not approved")
        if not self.EMAIL_LISTENING_ACTIVATED_AT:
            raise RuntimeError(
                "EMAIL_LISTENING_ACTIVATED_AT is required for live email effects")
        if not self.EMAIL_AUTOMATIC_WRITES_APPROVED:
            raise RuntimeError(
                "email automatic writes are not explicitly approved")
        if self.EMAIL_IMMEDIATE_ROLLOUT_APPROVED:
            return
        if not self.EMAIL_SHADOW_REVIEW_APPROVED:
            raise RuntimeError(
                "email shadow false-positive/negative review is not approved")
        observed = self.validated_shadow_observation_dates()
        if len(observed) < self.EMAIL_SHADOW_MIN_NIGHTS:
            raise RuntimeError(
                f"email live mode requires {self.EMAIL_SHADOW_MIN_NIGHTS} distinct "
                f"reviewed nightly shadow dates; found {len(observed)}")

    def require_business_writes(self) -> None:
        if self.BUSINESS_LISTENING_WRITE_KILL_SWITCH:
            raise RuntimeError("business-listening write kill switch is active")

    def require_docusign_read(self) -> None:
        for name in (
                "DOCUSIGN_INTEGRATION_KEY", "DOCUSIGN_USER_ID",
                "DOCUSIGN_ACCOUNT_ID"):
            _req(name)
        if not (self.DOCUSIGN_PRIVATE_KEY.strip() or
                self.DOCUSIGN_PRIVATE_KEY_PATH):
            raise RuntimeError(
                "DOCUSIGN_PRIVATE_KEY or DOCUSIGN_PRIVATE_KEY_PATH is required")

    def require_docusign_reasoning(self) -> None:
        self.require_docusign_read()
        for name in ("MONDAY_API_TOKEN", "OPENROUTER_API_KEY"):
            _req(name)
        if not self.DOCUSIGN_REASONING_APPROVED:
            raise RuntimeError(
                "DocuSign contract reasoning data processing is not approved")

    def require_docusign_live(self) -> None:
        self.require_docusign_reasoning()
        self.require_business_writes()
        _req("SLACK_BOT_TOKEN")
        if not self.MONDAY_AGENT_EVENTS_BOARD_ID:
            raise RuntimeError("MONDAY_AGENT_EVENTS_BOARD_ID is required")
        if not self.DOCUSIGN_ACTIVATED_AT:
            raise RuntimeError(
                "DOCUSIGN_ACTIVATED_AT is required; refusing contract backfill")
        if not self.DOCUSIGN_AUTOMATIC_WRITES_APPROVED:
            raise RuntimeError(
                "DocuSign automatic CRM writes are not explicitly approved")
        if not self.DOCUSIGN_DOCUMENT_ARCHIVE_APPROVED:
            raise RuntimeError(
                "DocuSign signed-document archive is not explicitly approved")

    def require_portfolio_reasoning(self) -> None:
        for name in ("MONDAY_API_TOKEN", "OPENROUTER_API_KEY"):
            _req(name)
        if not self.PORTFOLIO_REASONING_APPROVED:
            raise RuntimeError("portfolio reasoning is not explicitly approved")

    def require_portfolio_live(self) -> None:
        self.require_portfolio_reasoning()
        self.require_business_writes()
        if not self.MONDAY_AGENT_EVENTS_BOARD_ID:
            raise RuntimeError("MONDAY_AGENT_EVENTS_BOARD_ID is required")
        if not self.PORTFOLIO_PATTERN_WRITES_APPROVED:
            raise RuntimeError("portfolio brief writes are not explicitly approved")

    def validated_shadow_observation_dates(self) -> set[dt.date]:
        observed: set[dt.date] = set()
        today = dt.datetime.now(ZoneInfo("America/New_York")).date()
        for raw in self.EMAIL_SHADOW_OBSERVATION_DATES:
            try:
                value = dt.date.fromisoformat(str(raw))
            except ValueError as error:
                raise RuntimeError(
                    f"invalid EMAIL_SHADOW_OBSERVATION_DATES value: {raw!r}") from error
            if value >= today:
                raise RuntimeError(
                    "reviewed nightly shadow dates must be completed past ET dates")
            observed.add(value)
        return observed


config = Config()
