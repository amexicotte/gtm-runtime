"""Write-discipline — pure, deterministic, unit-tested. No Notion, no network.

Given the current field values and the model's extraction, decide what to SET
(blank fields), what to PROPOSE (filled fields — never overwritten), and the
qualification gaps. Nothing here touches a record whose Review Status is
Verified. The pipeline translates this plan into Notion API calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .reasoning import OPP_NUMBER_FIELDS, OPP_TEXT_FIELDS, STAGES

# json_key -> (kind, Notion property name)
_FIELD_MAP: dict[str, tuple[str, str]] = {
    **{k: ("number", v) for k, v in OPP_NUMBER_FIELDS.items()},
    **{k: ("text", v) for k, v in OPP_TEXT_FIELDS.items()},
    "stage_signal": ("stage", "Stage"),
}


@dataclass
class WritePlan:
    set: dict[str, object] = field(default_factory=dict)   # Notion prop name -> value
    propose: list[str] = field(default_factory=list)        # "was X → proposes Y" lines
    gaps: list[str] = field(default_factory=list)
    skipped_verified: bool = False


def _answered(item) -> bool:
    return (isinstance(item, dict)
            and item.get("status") == "ANSWERED"
            and item.get("value") not in (None, "", []))


def plan_opportunity(current: dict, extracted_opp: dict, *,
                     verified: bool, meeting_date: str,
                     qualification_gaps: list[str] | None = None) -> WritePlan:
    """current: {Notion prop name -> current plain value (None if blank)}.
    extracted_opp: the model's `opportunity` object. verified: record is locked."""
    plan = WritePlan()
    if verified:
        plan.skipped_verified = True
        return plan  # never touch a Verified record

    for json_key, (kind, prop) in _FIELD_MAP.items():
        item = extracted_opp.get(json_key)
        if not _answered(item):
            continue
        value = item["value"]

        if kind == "number":
            value = _to_number(value)
            if value is None:
                continue
        elif kind == "stage":
            if value not in STAGES:      # Won is human-only; Lost requires model evidence.
                continue

        cur = current.get(prop)
        if _blank(cur):
            plan.set[prop] = value
        elif str(cur).strip() != str(value).strip():
            plan.propose.append(
                f'{prop}: was "{cur}" → proposes "{value}" (source: {meeting_date} call)'
            )
        # equal → no-op

    # Qualification gaps: agent-derived agenda; refresh directly each run.
    plan.gaps = list(qualification_gaps or _derive_gaps(extracted_opp))
    return plan


def _derive_gaps(extracted_opp: dict) -> list[str]:
    gaps = []
    for json_key, (_kind, prop) in _FIELD_MAP.items():
        if json_key == "stage_signal":
            continue
        item = extracted_opp.get(json_key)
        if isinstance(item, dict) and item.get("status") == "NOT_DISCUSSED":
            gaps.append(f"{prop} — not discussed")
    return gaps


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _to_number(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        cleaned = v.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


# --- task dedupe (pure) ---
def new_tasks(extracted_tasks: list[dict], existing_titles: set[str]) -> list[dict]:
    """Drop tasks whose normalized title already exists for this meeting."""
    out = []
    for t in extracted_tasks or []:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        if _norm(title) in existing_titles:
            continue
        out.append(t)
    return out


def _norm(s: str) -> str:
    return " ".join(s.lower().split())
