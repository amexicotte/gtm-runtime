"""Advisory-only Closed Won/Lost portfolio learning for Phase 5."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import html
import json
import re
import statistics
from zoneinfo import ZoneInfo

from .config import config
from .reasoning import _complete


EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
COHORT_KEY = re.compile(r"\b[a-f0-9]{12}\b", re.IGNORECASE)
MAX_SNAPSHOT_CHARS = 120_000
MAX_SUMMARY_CHARS = 1_000
MAX_SECTION_ITEMS = 5
MAX_SECTION_ITEM_CHARS = 300
MAX_BRIEF_CHARS = 20_000
SECTIONS = (
    "won_patterns", "lost_patterns", "stage_velocity", "stakeholders",
    "pricing", "services", "objections", "loss_reasons", "questions",
    "limitations",
)


class PortfolioBriefError(ValueError):
    """Safe structural error for the aggregate advisory brief."""


def build_snapshot(rows: list[dict]) -> tuple[dict, str]:
    """Build exact population metrics and bounded balanced model evidence."""
    deals = []
    for row in rows:
        facts = row.get("facts") or {}
        stage = _stage(str(facts.get("Stage") or ""))
        if not stage:
            continue
        deal_id = str(row.get("id") or "")
        updates = [_clean_text(str(update.get("body") or ""))[:2_000]
                   for update in row.get("updates") or []]
        deals.append({
            "cohort_key": hashlib.sha256(deal_id.encode()).hexdigest()[:12],
            "outcome": stage,
            "created_at": str(row.get("created_at") or ""),
            "terminal_record_updated_at": str(row.get("updated_at") or ""),
            "terminal_record_duration_proxy_days": _duration_days(
                str(row.get("created_at") or ""),
                str(row.get("updated_at") or "")),
            "deal_value": _number(facts.get("Deal Value")),
            "fixed_fee": _number(facts.get("Fixed Fee")),
            "service_type": _split(facts.get("Service Type")),
            "billing_model": str(facts.get("Billing Model") or "")[:200],
            "source": str(facts.get("Source") or "")[:200],
            "stakeholder_count": int(facts.get("Contacts") or 0),
            "champion_count": int(facts.get("Champion") or 0),
            "identified_pain": _clean_text(str(facts.get("Identified Pain") or ""))[:2_000],
            "economic_buyer": _clean_text(str(facts.get("Economic Buyer") or ""))[:1_000],
            "decision_criteria": _clean_text(str(facts.get("Decision Criteria") or ""))[:1_000],
            "decision_process": _clean_text(str(facts.get("Decision Process") or ""))[:1_000],
            "qualification_gaps": _clean_text(str(facts.get("Qualification Gaps") or ""))[:1_000],
            "competition": _clean_text(str(facts.get("Competition") or ""))[:1_000],
            "notes": _clean_text(str(facts.get("Notes") or ""))[:2_000],
            "recent_updates": [value for value in updates if value],
        })
    deals.sort(key=lambda value: value["cohort_key"])
    metrics = _metrics(deals)
    population = json.dumps(
        {"metrics": metrics, "deals": deals},
        sort_keys=True, separators=(",", ":"))
    snapshot = _bounded_snapshot(deals, metrics)
    return snapshot, hashlib.sha256(population.encode()).hexdigest()


def reason_patterns(snapshot: dict, *, system_prompt: str | None = None) -> dict:
    if system_prompt is None:
        from reasoning.build_prompt import build_portfolio
        system_prompt = build_portfolio()
    user = "Anonymized terminal-Deal portfolio:\n" + json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"))
    return _complete(system_prompt, user)


def validate_brief(result: dict, *, metrics: dict,
                   now: dt.datetime | None = None) -> dict:
    if not isinstance(result, dict):
        raise TypeError("portfolio reasoning result must be an object")
    if not isinstance(result.get("executive_summary"), str):
        raise PortfolioBriefError("executive summary must be text")
    summary = _clean_brief_text(result["executive_summary"])[:MAX_SUMMARY_CHARS]
    if not summary:
        raise PortfolioBriefError("executive summary is empty")
    brief = {
        "as_of_date": (now or dt.datetime.now(dt.timezone.utc)).astimezone(
            ZoneInfo("America/New_York")).date().isoformat(),
        "metrics": metrics, "executive_summary": summary,
    }
    for section in SECTIONS:
        values = result.get(section, [])
        if values is None:
            values = []
        elif isinstance(values, str):
            values = [values]
        elif not isinstance(values, list):
            raise PortfolioBriefError(f"section must be text or a list: {section}")
        if any(not isinstance(value, str) for value in values):
            raise PortfolioBriefError(f"section contains non-text values: {section}")
        cleaned = [_clean_brief_text(value)[:MAX_SECTION_ITEM_CHARS]
                   for value in values[:MAX_SECTION_ITEMS]]
        brief[section] = [value for value in cleaned if value]
    rendered = json.dumps(brief, sort_keys=True)
    if EMAIL.search(rendered):
        raise PortfolioBriefError("brief contains an email address")
    if COHORT_KEY.search(rendered.casefold()):
        raise PortfolioBriefError("brief contains a record cohort key")
    if len(rendered) > MAX_BRIEF_CHARS:
        raise PortfolioBriefError("brief exceeds the aggregate content cap")
    return brief


def run_portfolio_patterns(*, crm, reasoner=reason_patterns, live: bool = False,
                           now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = crm.terminal_deal_portfolio(
        max_deals=config.PORTFOLIO_MAX_TERMINAL_DEALS,
        updates_limit=config.PORTFOLIO_UPDATES_PER_DEAL)
    snapshot, input_sha = build_snapshot(rows)
    minimum = config.PORTFOLIO_MIN_COHORT_SIZE
    if (snapshot["metrics"]["won_count"] < minimum or
            snapshot["metrics"]["lost_count"] < minimum):
        raise RuntimeError(
            f"portfolio pattern learning requires at least {minimum} Won and "
            f"{minimum} Lost Deals")
    prior = crm.deal_pattern_brief_state()
    reasoning_calls = 0
    if (prior.get("input_sha256") == input_sha and
            isinstance(prior.get("brief"), dict)):
        brief = prior["brief"]
    else:
        result = reasoner(snapshot)
        reasoning_calls = 1
        brief = validate_brief(result, metrics=snapshot["metrics"], now=now)
    state = {
        "generated_at": now.isoformat(), "input_sha256": input_sha,
        "brief": brief,
        "reasoning_audit": (result.get("_audit") if reasoning_calls else
                            prior.get("reasoning_audit") or {}),
    }
    state["reasoning_audit"] = _bounded_audit(state["reasoning_audit"])
    state_id = ""
    if live and reasoning_calls:
        state_id = crm.save_deal_pattern_brief_state(state)
    return {
        "mode": "live" if live else "shadow", "terminal_deals": len(rows),
        "won_deals": snapshot["metrics"]["won_count"],
        "lost_deals": snapshot["metrics"]["lost_count"],
        "input_sha256": input_sha, "reasoning_calls": reasoning_calls,
        "state_changed": bool(live and reasoning_calls),
        "state_item_id": state_id, "deal_writes": 0, "brief": brief,
    }


def _metrics(deals: list[dict]) -> dict:
    won = [row for row in deals if row["outcome"] == "won"]
    lost = [row for row in deals if row["outcome"] == "lost"]
    services = collections.Counter(
        service for row in deals for service in row["service_type"])
    billing = collections.Counter(
        row["billing_model"] for row in deals if row["billing_model"])
    return {
        "terminal_count": len(deals), "won_count": len(won),
        "lost_count": len(lost),
        "won_rate": round(len(won) / len(deals), 4) if deals else None,
        "deal_value": _numeric_summary(row["deal_value"] for row in deals),
        "fixed_fee": _numeric_summary(row["fixed_fee"] for row in deals),
        "service_counts": dict(services.most_common(20)),
        "billing_model_counts": dict(billing.most_common(20)),
        "deals_with_champion": sum(bool(row["champion_count"]) for row in deals),
        "deals_with_updates": sum(bool(row["recent_updates"]) for row in deals),
        "terminal_record_duration_proxy_days": {
            "won": _numeric_summary(
                row["terminal_record_duration_proxy_days"] for row in won),
            "lost": _numeric_summary(
                row["terminal_record_duration_proxy_days"] for row in lost),
        },
    }


def _bounded_snapshot(deals: list[dict], metrics: dict) -> dict:
    """Select deterministic, outcome-balanced evidence under the model cap."""
    won = [row for row in deals if row["outcome"] == "won"]
    lost = [row for row in deals if row["outcome"] == "lost"]
    selected: list[dict] = []
    for index in range(min(len(won), len(lost))):
        candidate = sorted(
            selected + [won[index], lost[index]],
            key=lambda value: value["cohort_key"])
        snapshot = _snapshot_payload(candidate, metrics, len(won), len(lost))
        rendered = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        if len(rendered) > MAX_SNAPSHOT_CHARS:
            break
        selected = candidate
    snapshot = _snapshot_payload(selected, metrics, len(won), len(lost))
    rendered = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    if len(rendered) > MAX_SNAPSHOT_CHARS:
        raise RuntimeError("portfolio reasoning sample exceeds the input cap")
    return snapshot


def _snapshot_payload(selected: list[dict], metrics: dict,
                      won_count: int, lost_count: int) -> dict:
    selected_won = sum(row["outcome"] == "won" for row in selected)
    selected_lost = sum(row["outcome"] == "lost" for row in selected)
    return {
        "metrics": metrics,
        "sample": {
            "method": "deterministic_balanced_by_outcome",
            "population_won_count": won_count,
            "population_lost_count": lost_count,
            "selected_won_count": selected_won,
            "selected_lost_count": selected_lost,
        },
        "deals": selected,
    }


def _numeric_summary(values) -> dict:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {"count": len(numbers), "min": min(numbers),
            "median": statistics.median(numbers), "max": max(numbers)}


def _number(value):
    raw = str(value or "").strip().casefold()
    multiplier = 1_000_000 if raw.endswith("m") else (
        1_000 if raw.endswith("k") else 1)
    text = re.sub(r"[^0-9.\-]", "", raw)
    try:
        return float(text) * multiplier if text else None
    except ValueError:
        return None


def _split(value) -> list[str]:
    return [part.strip()[:200] for part in re.split(r"[,;\n]", str(value or ""))
            if part.strip()]


def _duration_days(start: str, end: str):
    try:
        first = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        if first.tzinfo is None:
            first = first.replace(tzinfo=dt.timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        return max(0, round((last - first).total_seconds() / 86_400, 2))
    except ValueError:
        return None


def _stage(value: str) -> str:
    value = value.strip().casefold()
    if value in {"won", "closed won"}:
        return "won"
    if value in {"lost", "closed lost"}:
        return "lost"
    return ""


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = EMAIL.sub("[email removed]", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_brief_text(value: str) -> str:
    return COHORT_KEY.sub("[cohort removed]", _clean_text(value))


def _bounded_audit(value) -> dict:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "provider", "generation_id", "requested_model", "response_model",
        "created_at", "system_prompt_sha256", "user_prompt_sha256",
        "input_chars", "output_chars", "finish_reason", "usage",
    }
    return {key: item for key, item in value.items() if key in allowed}
