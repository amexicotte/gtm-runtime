"""The GTM matcher — deterministic company identification + THE GATE.

No LLM, no network. Given a meeting's invited emails and a company-by-domain
lookup (injected, so this is fully unit-testable), decide:
  - how many external companies are present
  - whether one company can proceed or a human must choose the prospect
  - whether the pipeline may proceed or must hold at the gate

Also collapses Fireflies' duplicate Meeting rows (one meeting recorded by
several participants' Fireflies accounts) before the reasoning agent runs, so
one meeting costs one reasoning pass, not three.

Design: the core logic takes plain data in and returns a decision. All Notion
reads/writes live in the caller (the runtime), which supplies `company_lookup`
and acts on the returned decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .domains import parse_domains


# --- Company lookup contract -------------------------------------------------

@dataclass
class CompanyMatch:
    """What a domain→company lookup returns. `exists=False` means no live
    Company has this Domain yet (cold start)."""
    domain: str
    exists: bool = False
    company_url: str | None = None
    name: str | None = None
    domain_role: str | None = None      # "Client" | "Partner" | "Investor" | None
    represents_url: str | None = None    # for Investor/Partner: the client they act for


# A lookup is any callable domain -> CompanyMatch. `dict_lookup` adapts a dict.
def dict_lookup(table: dict[str, CompanyMatch]):
    def _lookup(domain: str) -> CompanyMatch:
        return table.get(domain, CompanyMatch(domain=domain, exists=False))
    return _lookup


# --- Decisions ---------------------------------------------------------------

PROCEED = "proceed"        # exactly one company — resolve/create, then reason
GATE = "gate"              # zero or 2+ companies — stop before reasoning
# Kept as an import-compatible alias for the retired Notion branch. The monday
# gate never lets a no-company meeting reach the reasoning model.
FLAG_NO_COMPANY = "flag"


@dataclass
class MatchResult:
    meeting_type: str              # "Internal" | "External"
    decision: str                  # PROCEED | GATE | FLAG_NO_COMPANY
    reason: str
    company_url: str | None = None
    create_company_domain: str | None = None  # agent should create Company from this
    represents_url: str | None = None
    external_domains: list[str] = field(default_factory=list)
    freemail_present: bool = False
    slack_prompt: str | None = None            # only set when decision == GATE


def _display(m: CompanyMatch) -> str:
    return m.name or m.domain


def _gate_prompt(title: str, matches: list[CompanyMatch]) -> str:
    lines = [f'🏢 "{title}" — which company is this meeting about?']
    for i, m in enumerate(matches, 1):
        lines.append(f"{i}. {_display(m)}")
    lines.append('Reply with a number, or "2 on behalf of PortfolioCo"')
    return "\n".join(lines)


def classify_meeting(
    invited_emails: list[str],
    company_lookup,
    title: str = "",
) -> MatchResult:
    """Classify one meeting and decide whether the pipeline proceeds.

    `company_lookup` maps a bare domain -> CompanyMatch. See CompanyMatch.
    """
    buckets = parse_domains(invited_emails)
    external = buckets["external"]
    freemail_present = bool(buckets["freemail"])

    # --- 0 identifying external domains: stop, including free-mail-only calls ---
    if not external:
        return MatchResult(
            meeting_type="External" if freemail_present else "Internal",
            decision=GATE,
            reason=("only free-mail external attendee(s); no company domain"
                    if freemail_present else
                    "no external company domains — gated without reasoning"),
            freemail_present=freemail_present,
        )

    # --- exactly 1 identifying external domain ---
    if len(external) == 1:
        m = company_lookup(external[0])
        if m.exists:
            return MatchResult(
                meeting_type="External",
                decision=PROCEED,
                reason=f"single external domain {external[0]} → linked {_display(m)}",
                company_url=m.company_url,
                external_domains=external,
                freemail_present=freemail_present,
            )
        return MatchResult(
            meeting_type="External",
            decision=PROCEED,
            reason=f"single external domain {external[0]} → no Company yet, flag create",
            create_company_domain=external[0],
            external_domains=external,
            freemail_present=freemail_present,
        )

    # --- 2+ identifying external domains → THE GATE ---
    matches = [company_lookup(d) for d in external]
    # Always ask. Existing CRM roles are useful labels but are not evidence of
    # which company is the prospect on this particular call.
    return MatchResult(
        meeting_type="External",
        decision=GATE,
        reason=f"{len(external)} external domains — human must pick the prospect",
        external_domains=external,
        freemail_present=freemail_present,
        slack_prompt=_gate_prompt(title or "(untitled meeting)", matches),
    )


# --- Fireflies duplicate-row collapse ----------------------------------------

_FF_SUFFIX = re.compile(r"\s*\(fireflies\)\s*$", re.IGNORECASE)
_TRAIL_DATE = re.compile(
    r"\s*[-–]\s*[A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}\s*$"
)


def title_core(title: str) -> str:
    """Normalize a meeting title for duplicate detection: drop the trailing
    ' (Fireflies)' tag and a trailing ' - Jul 27, 2026' style date, lowercase,
    collapse whitespace."""
    t = _FF_SUFFIX.sub("", title or "")
    t = _TRAIL_DATE.sub("", t)
    return re.sub(r"\s+", " ", t).strip().lower()


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def dedupe_meeting_rows(rows: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    """Collapse Fireflies duplicate Meeting rows.

    Two rows are duplicates when they share the SAME date AND (same normalized
    title-core OR an identical invited-email set containing at least two
    people and a non-Harbor attendee). Grouping is transitive.

    Single-attendee recordings are never collapsed by invitees alone. Fireflies
    background recordings often contain only the recorder, which otherwise
    makes every distinct recording by that person on the same day look like a
    duplicate. Same-title matching still collapses genuine copies.

    Each row is a dict with keys: url, title, date (YYYY-MM-DD), invited (list),
    optionally attendees (list), transcript (str|None), created (ISO str).

    Returns (canonical_rows, duplicate_groups) where duplicate_groups lists only
    the groups that had >1 row (for logging).
    """
    n = len(rows)
    uf = _UnionFind(n)
    # Index by date for cheap pairing.
    by_date: dict[str, list[int]] = {}
    cores = [title_core(r.get("title", "")) for r in rows]
    invsets = [frozenset(e.lower() for e in (r.get("invited") or [])) for r in rows]
    for i, r in enumerate(rows):
        by_date.setdefault(r.get("date"), []).append(i)

    for _, idxs in by_date.items():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                same_title = cores[i] and cores[i] == cores[j]
                buckets = parse_domains(list(invsets[i]))
                has_external_person = bool(
                    buckets["external"] or buckets["freemail"])
                same_invites = (len(invsets[i]) >= 2 and has_external_person and
                                invsets[i] == invsets[j])
                if same_title or same_invites:
                    uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    canonical: list[dict] = []
    duplicate_groups: list[list[dict]] = []
    for members in groups.values():
        group_rows = [rows[i] for i in members]
        canonical.append(_pick_canonical(group_rows))
        if len(group_rows) > 1:
            duplicate_groups.append(group_rows)
    return canonical, duplicate_groups


def _richness(row: dict) -> tuple:
    """Higher = keep. Prefer a row with a transcript, then more attendee/invited
    data, then the earliest created time (stable tiebreak)."""
    has_transcript = 1 if row.get("transcript") else 0
    breadth = len(row.get("invited") or []) + len(row.get("attendees") or [])
    # earliest created wins the tiebreak → negate by comparing created ascending
    created = row.get("created") or ""
    return (has_transcript, breadth, _neg_str(created))


def _neg_str(s: str):
    # Sort helper: we want the EARLIEST created to win on a tie, so wrap so that
    # a smaller string compares as "greater" under max().
    return tuple(-ord(c) for c in s)


def _pick_canonical(group_rows: list[dict]) -> dict:
    return max(group_rows, key=_richness)
