"""Reasoning step — the one LLM call per meeting, via OpenRouter (OpenAI-compatible).

The model EXTRACTS structured facts; it does not write to monday. Deterministic
code applies those facts as final CRM values. This keeps the LLM's role to
language understanding only, per the
"scripts over LLMs" principle, and makes the model swappable by env var.

Full operating spec: ../agent/reasoning-agent.md. This is its API form.
"""
from __future__ import annotations

import json
import datetime as dt
import hashlib

import requests

from .config import config
from .lifecycle import LISTENING_WRITABLE_DEAL_STAGES

# The seven MEDDICC fields + the direct opportunity fields the model must hunt.
# Maps the JSON key returned by the model → the Notion property name.
OPP_TEXT_FIELDS = {
    "metrics": "Metrics",
    "economic_buyer": "Economic Buyer",
    "decision_criteria": "Decision Criteria",
    "decision_process": "Decision Process",
    "identified_pain": "Identified Pain",
    "champion": "Champion",
    "competition": "Competition",
    "service_type": "Service Type",
}
OPP_NUMBER_FIELDS = {"expected_revenue": "Expected revenue"}
STAGES = list(LISTENING_WRITABLE_DEAL_STAGES)

def extract(content: str, meeting_title: str, meeting_date: str,
            ctx: dict | None = None, system_prompt: str | None = None) -> dict:
    """One OpenRouter call. Returns the parsed JSON dict (raises on hard failure)."""
    if system_prompt is None:
        # Import lazily so the pure parser/tests do not require PyYAML. Production
        # installs engine/requirements.txt before invoking this path.
        from reasoning.build_prompt import build
        system_prompt = build()
    user = (
        f"Meeting: {meeting_title}\nDate: {meeting_date}\n\n"
        f"Resolved CRM context (IDs; prospect is authoritative):\n"
        f"{json.dumps(ctx or {}, sort_keys=True)}\n\n"
        f"Meeting content (transcript + human notes):\n{content}"
    )
    return _complete(system_prompt, user)


def extract_manual(notes: str, meeting_title: str, meeting_date: str,
                   system_prompt: str | None = None) -> dict:
    """Extract identity, final CRM updates, and tasks from manual notes."""
    if system_prompt is None:
        from reasoning.build_prompt import build_manual
        system_prompt = build_manual()
    user = (
        f"Manual call: {meeting_title}\nDate: {meeting_date}\n\n"
        f"Unstructured human notes:\n{notes}"
    )
    return _complete(system_prompt, user)


def extract_email(content: str, subject: str, occurred_at: str,
                  ctx: dict | None = None,
                  system_prompt: str | None = None) -> dict:
    """Reason about one normalized set of new messages in an E&A thread."""
    if system_prompt is None:
        from reasoning.build_prompt import build_email
        system_prompt = build_email()
    user = (
        f"Email thread: {subject}\nLatest new message: {occurred_at}\n\n"
        f"Resolved CRM context and bounded Activity candidates:\n"
        f"{json.dumps(ctx or {}, sort_keys=True)}\n\n"
        "Normalized NEW messages only (quoted history was removed and cannot "
        f"authorize effects):\n{content}"
    )
    return _complete(system_prompt, user)


def _complete(system_prompt: str, user: str) -> dict:
    body = {
        "model": config.OPENROUTER_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ],
    }
    payload = choice = result = raw = None
    provider_attempts = 0
    for provider_attempts in (1, 2):
        r = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "X-Title": "Harbor GTM Engine",
            },
            json=body, timeout=120,
        )
        if not r.ok:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:400]}")
        try:
            payload = r.json()
            choice = payload["choices"][0]
            raw = _message_text(choice["message"].get("content"))
            result = _parse(raw)
            break
        except (AttributeError, IndexError, KeyError, TypeError, ValueError,
                json.JSONDecodeError) as error:
            if provider_attempts == 2:
                raise RuntimeError(
                    "OpenRouter returned unusable structured output twice") from error
    assert isinstance(payload, dict) and isinstance(choice, dict)
    assert isinstance(result, dict) and isinstance(raw, str)
    result["_audit"] = {
        "provider": "OpenRouter",
        "generation_id": str(payload.get("id") or ""),
        "requested_model": config.OPENROUTER_MODEL,
        "response_model": str(payload.get("model") or ""),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "system_prompt_sha256": hashlib.sha256(
            system_prompt.encode("utf-8")).hexdigest(),
        "user_prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
        "input_chars": len(user),
        "output_chars": len(raw),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "usage": payload.get("usage") or {},
        "provider_attempts": provider_attempts,
    }
    return result


def _message_text(content) -> str:
    """Normalize OpenAI-compatible string or text-part message content."""
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            value = part.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value)
        combined = "\n".join(parts).strip()
        if combined:
            return combined
    raise ValueError("OpenRouter response has no message text")


def _parse(raw: str) -> dict:
    """Defensive JSON parse — strip code fences if the model added them."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s
        s = s[s.find("{"):]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1:
            return json.loads(s[start:end + 1])
        raise
