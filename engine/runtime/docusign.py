"""DocuSign client for completed-envelope metadata and executed PDFs."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from pathlib import Path
from typing import Any

import jwt
import requests

from .config import config


JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


@dataclass(frozen=True)
class CompletedEnvelope:
    envelope_id: str
    completed_at: str
    subject: str
    signer_names: tuple[str, ...]
    signer_emails: tuple[str, ...]
    recipient_emails: tuple[str, ...]
    source_url: str

    def __post_init__(self) -> None:
        if not self.envelope_id.strip():
            raise ValueError("DocuSign envelope ID is required")
        object.__setattr__(self, "completed_at", _parse_datetime(
            self.completed_at).isoformat())
        object.__setattr__(self, "signer_names", tuple(dict.fromkeys(
            value.strip() for value in self.signer_names if value.strip())))
        object.__setattr__(self, "signer_emails", tuple(dict.fromkeys(
            value.strip().casefold() for value in self.signer_emails
            if value.strip())))
        object.__setattr__(self, "recipient_emails", tuple(dict.fromkeys(
            value.strip().casefold() for value in self.recipient_emails
            if value.strip())))


class DocuSign:
    """JWT Grant client with completed-document reads and no provider writes."""

    def __init__(self, *, session=None, now=None):
        self.session = session or requests.Session()
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._access_token = ""
        self._base_uri = ""

    def connectivity(self) -> dict:
        self.access_token()
        self.base_uri()
        return {"status": "ready", "account_id": config.DOCUSIGN_ACCOUNT_ID,
                "user_id": config.DOCUSIGN_USER_ID,
                "base_uri": self._base_uri}

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        issued = int(self.now().timestamp())
        assertion = jwt.encode({
            "iss": config.DOCUSIGN_INTEGRATION_KEY,
            "sub": config.DOCUSIGN_USER_ID,
            "aud": config.DOCUSIGN_AUTH_SERVER,
            "iat": issued, "exp": issued + 3600,
            "scope": "signature impersonation",
        }, _private_key(), algorithm="RS256")
        response = self.session.post(
            f"https://{config.DOCUSIGN_AUTH_SERVER}/oauth/token",
            data={"grant_type": JWT_GRANT, "assertion": assertion}, timeout=30)
        _raise(response, "DocuSign JWT token")
        token = str(response.json().get("access_token") or "").strip()
        if not token:
            raise RuntimeError("DocuSign JWT response omitted access_token")
        self._access_token = token
        return token

    def base_uri(self) -> str:
        if self._base_uri:
            return self._base_uri
        response = self.session.get(
            f"https://{config.DOCUSIGN_AUTH_SERVER}/oauth/userinfo",
            headers=self._headers(), timeout=30)
        _raise(response, "DocuSign userinfo")
        accounts = response.json().get("accounts") or []
        matches = [row for row in accounts
                   if str(row.get("account_id") or "") ==
                   config.DOCUSIGN_ACCOUNT_ID]
        if len(matches) != 1:
            raise RuntimeError(
                "configured DocuSign account was not uniquely visible")
        base = str(matches[0].get("base_uri") or "").rstrip("/")
        if not base.startswith("https://"):
            raise RuntimeError("DocuSign userinfo returned an invalid base_uri")
        self._base_uri = base
        return base

    def list_completed(self, *, from_date: str) -> list[CompletedEnvelope]:
        response = self.session.get(
            self._account_url("envelopes"), headers=self._headers(), params={
                "from_date": _parse_datetime(from_date).isoformat(),
                "status": "completed", "order_by": "completed",
                "order": "asc",
                "count": str(config.DOCUSIGN_MAX_ENVELOPES_PER_RUN + 1),
            }, timeout=60)
        _raise(response, "DocuSign completed-envelope list")
        return [self._envelope(row) for row in response.json().get("envelopes") or []
                if str(row.get("status") or "").casefold() == "completed"]

    def completed_envelope(self, envelope_id: str) -> CompletedEnvelope:
        response = self.session.get(
            self._account_url(f"envelopes/{envelope_id}"),
            headers=self._headers(), timeout=60)
        _raise(response, "DocuSign envelope")
        row = response.json()
        if str(row.get("status") or "").casefold() != "completed":
            raise ValueError("DocuSign envelope is not completed")
        signers, recipients = self._recipients(envelope_id)
        return self._envelope(row, signers=signers, recipients=recipients)

    def download_combined_pdf(self, envelope_id: str) -> bytes:
        response = self.session.get(
            self._account_url(f"envelopes/{envelope_id}/documents/combined"),
            headers={**self._headers(), "Accept": "application/pdf"},
            params={"certificate": "true"}, timeout=120)
        _raise(response, "DocuSign executed document")
        value = bytes(response.content or b"")
        if not value.startswith(b"%PDF"):
            raise RuntimeError("DocuSign combined document was not a PDF")
        if len(value) > 25 * 1024 * 1024:
            raise RuntimeError("DocuSign combined PDF exceeds the 25 MB cap")
        return value

    def _recipients(self, envelope_id: str) -> tuple[list[dict], list[dict]]:
        response = self.session.get(
            self._account_url(f"envelopes/{envelope_id}/recipients"),
            headers=self._headers(), timeout=60)
        _raise(response, "DocuSign recipients")
        signers = response.json().get("signers") or []
        if not signers or any(
                str(row.get("status") or "").casefold() != "completed"
                for row in signers):
            raise ValueError("all DocuSign signers must be completed")
        payload = response.json()
        recipients = []
        for key in ("signers", "carbonCopies", "certifiedDeliveries",
                    "editors", "agents", "intermediaries"):
            recipients.extend(payload.get(key) or [])
        return signers, recipients

    def _envelope(self, row: dict[str, Any], *, signers=None,
                  recipients=None) -> CompletedEnvelope:
        embedded_recipients = row.get("recipients") or {}
        signers = signers if signers is not None else (
            embedded_recipients.get("signers") or row.get("signers") or [])
        all_recipients = recipients if recipients is not None else signers
        envelope_id = str(row.get("envelopeId") or "")
        host = ("appdemo.docusign.com" if
                config.DOCUSIGN_AUTH_SERVER.startswith("account-d") else
                "app.docusign.com")
        return CompletedEnvelope(
            envelope_id=envelope_id,
            completed_at=str(row.get("completedDateTime") or ""),
            subject=str(row.get("emailSubject") or ""),
            signer_names=tuple(str(value.get("name") or "") for value in signers),
            signer_emails=tuple(str(value.get("email") or "") for value in signers),
            recipient_emails=tuple(
                str(value.get("email") or "") for value in all_recipients),
            source_url=f"https://{host}/documents/details/{envelope_id}",
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def _account_url(self, path: str) -> str:
        return (f"{self.base_uri()}/restapi/v2.1/accounts/"
                f"{config.DOCUSIGN_ACCOUNT_ID}/{path.lstrip('/')}")


def consent_url(integration_key: str, redirect_uri: str, *,
                auth_server: str = "account.docusign.com") -> str:
    from urllib.parse import urlencode
    auth_server = auth_server.removeprefix("https://").rstrip("/")
    return f"https://{auth_server}/oauth/auth?" + urlencode({
        "response_type": "code", "scope": "signature impersonation",
        "client_id": integration_key, "redirect_uri": redirect_uri,
    })


def _private_key() -> str:
    inline = config.DOCUSIGN_PRIVATE_KEY.strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = Path(config.DOCUSIGN_PRIVATE_KEY_PATH)
    if not path.is_file():
        raise RuntimeError("DocuSign RSA private key file was not found")
    return path.read_text(encoding="utf-8")


def _parse_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid DocuSign timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("DocuSign timestamp must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _raise(response, label: str) -> None:
    if not response.ok:
        raise RuntimeError(f"{label} failed with HTTP {response.status_code}")
