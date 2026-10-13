"""Domain parsing and drop-lists for the GTM matcher.

Pure string work — no Notion, no network. Everything here is deterministic and
unit-testable. The matcher's entire company-identification logic keys on the
email domains in a Meeting's `Invited` field (verified 2026-07-28: Fireflies
writes attendee emails to `Invited`; `Attendees` holds display names only).

The authoritative drop-lists live in ``reasoning/config/entities.yaml`` so the
matcher and the reasoning prompt cannot drift.  Conservative built-in defaults
keep this pure module usable if it is copied without the reasoning package.
"""
from __future__ import annotations

from pathlib import Path
import re

# --- Drop-lists --------------------------------------------------------------

# Harbor's own domain(s). BOTH spellings appear in real Invited data — the second
# is a recurring typo of the first and MUST be dropped, or a clean call looks
# like it has an external domain and wrongly trips the gate.
_DEFAULT_HARBOR_DOMAINS: set[str] = {
    "harboradvisors.example",
    "harboradvisers.example",  # real, recurring typo — do not remove
}

# Notetaker / transcription bots. Present as attendees on many calls; never a
# company we do business with.
_DEFAULT_NOTETAKER_DOMAINS: set[str] = {
    "fireflies.ai",
    "otter.ai",
}

# Free-mail / consumer providers. A free-mail address is a real person but does
# NOT identify a company, so it never counts toward the external-domain total.
# Its presence is recorded separately (an individual/consultant signal).
_DEFAULT_FREEMAIL_DOMAINS: set[str] = {
    "gmail.com", "googlemail.com",
    "yahoo.com", "ymail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "proton.me", "protonmail.com",
    "gmx.com", "gmx.net",
}

# Domains that are internal-adjacent but NOT Harbor: Unity is the parent PE firm
# whose workspace Harbor lives in. Treated as a known non-client party by default
# so an Harbor<>Unity call doesn't read as a fresh external prospect. This is a
# tuning choice — move a domain out if you want it to count as a real external.
# (The authoritative per-company treatment is the `Domain Role` field in Notion;
# this is only the cold-start default before a company record exists.)
_DEFAULT_INTERNAL_ADJACENT: set[str] = {
    "ridgeline.example",
}


def _configured_domains() -> dict[str, set[str]]:
    path = Path(__file__).resolve().parents[2] / "reasoning" / "config" / "entities.yaml"
    if not path.exists():
        return {}
    keys = (
        "internal_domains", "notetaker_domains", "freemail_domains",
        "internal_adjacent_domains",
    )
    # These four settings intentionally use one-line YAML flow lists. Parsing
    # only that tiny grammar keeps the no-network matcher stdlib-only; the full
    # prompt builder still uses PyYAML for the richer config below these lines.
    configured: dict[str, set[str]] = {}
    text = path.read_text()
    for key in keys:
        match = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*\[([^]]*)\]", text)
        if match:
            configured[key] = {
                value.strip().strip("'\"").lower()
                for value in match.group(1).split(",") if value.strip()
            }
    return configured


_CONFIGURED = _configured_domains()
HARBOR_DOMAINS = _CONFIGURED.get("internal_domains") or _DEFAULT_HARBOR_DOMAINS
NOTETAKER_DOMAINS = _CONFIGURED.get("notetaker_domains") or _DEFAULT_NOTETAKER_DOMAINS
FREEMAIL_DOMAINS = _CONFIGURED.get("freemail_domains") or _DEFAULT_FREEMAIL_DOMAINS
KNOWN_INTERNAL_ADJACENT = (
    _CONFIGURED.get("internal_adjacent_domains") or _DEFAULT_INTERNAL_ADJACENT
)


def domain_of(email: str) -> str | None:
    """Return the bare lowercase domain of an email, or None if unparseable."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    # strip a trailing dot and any stray whitespace/angle brackets
    domain = domain.strip(" <>").rstrip(".")
    return domain or None


def classify_domain(domain: str) -> str:
    """Bucket a single domain: 'harbor' | 'notetaker' | 'freemail' |
    'internal_adjacent' | 'external'."""
    if domain in HARBOR_DOMAINS:
        return "harbor"
    if domain in NOTETAKER_DOMAINS:
        return "notetaker"
    if domain in FREEMAIL_DOMAINS:
        return "freemail"
    if domain in KNOWN_INTERNAL_ADJACENT:
        return "internal_adjacent"
    return "external"


def parse_domains(emails: list[str]) -> dict[str, list[str]]:
    """Normalize a list of emails into deduped, bucketed domains.

    Returns {bucket: [domain, ...]} with each bucket's domains lowercased,
    deduped, and sorted. Buckets: harbor, notetaker, freemail,
    internal_adjacent, external. Unparseable emails are ignored.
    """
    buckets: dict[str, set[str]] = {
        "harbor": set(), "notetaker": set(), "freemail": set(),
        "internal_adjacent": set(), "external": set(),
    }
    for email in emails or []:
        d = domain_of(email)
        if d is None:
            continue
        buckets[classify_domain(d)].add(d)
    return {k: sorted(v) for k, v in buckets.items()}
