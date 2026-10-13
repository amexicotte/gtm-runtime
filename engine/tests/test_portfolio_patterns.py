"""Phase 5 advisory portfolio aggregation and retry tests."""
import datetime as dt
import copy
import hashlib
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.portfolio_patterns import (  # noqa: E402
    build_snapshot, run_portfolio_patterns, validate_brief,
)
from runtime.config import config  # noqa: E402


def rows():
    return [{
        "id": "deal-1", "name": "Secret Client",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
        "facts": {"Stage": "Won", "Deal Value": "$20,000", "Fixed Fee": "5000",
                  "Service Type": "RevOps", "Billing Model": "Retainer",
                  "Contacts": 2, "Champion": 1,
                  "Notes": "Contact person@example.com liked the plan."},
        "updates": [{"body": "<b>Decision</b> Client approved the project."}],
    }, {
        "id": "deal-2", "name": "Other Secret",
        "created_at": "2026-01-05T00:00:00Z",
        "updated_at": "2026-02-05T00:00:00Z",
        "facts": {"Stage": "Lost", "Deal Value": "10000", "Fixed Fee": "",
                  "Service Type": "GTM Strategy", "Billing Model": "Project",
                  "Contacts": 1, "Champion": 0, "Notes": "Budget concern."},
        "updates": [],
    }]


def reasoning():
    return {"executive_summary": "Won and lost cohorts show different signals.",
            "won_patterns": ["Won work had a documented champion."],
            "lost_patterns": ["The lost cohort recorded a budget concern."],
            "stage_velocity": ["Record-update duration is only a proxy."],
            "stakeholders": ["Won work had more recorded stakeholders."],
            "pricing": ["Observed values span the supplied deterministic range."],
            "services": ["Service mix is sparse."],
            "objections": ["Budget appeared in the lost cohort."],
            "loss_reasons": ["Loss evidence is incomplete."],
            "questions": ["Who is the economic buyer?"],
            "limitations": ["Two records are not enough for strong conclusions."],
            "_audit": {"provider": "OpenRouter", "requested_model": "deepseek"}}


def test_snapshot_is_anonymized_and_metrics_are_deterministic():
    snapshot, digest = build_snapshot(rows())
    rendered = json.dumps(snapshot)
    assert "Secret Client" not in rendered and "deal-1" not in rendered
    assert "person@example.com" not in rendered
    assert snapshot["metrics"]["won_count"] == 1
    assert snapshot["metrics"]["deal_value"]["median"] == 15000
    assert snapshot["metrics"]["terminal_record_duration_proxy_days"][
        "won"]["median"] == 31
    assert len(digest) == 64


def test_brief_uses_deterministic_metrics_and_scrubs_addresses():
    snapshot, _digest = build_snapshot(rows())
    brief = validate_brief(reasoning(), metrics=snapshot["metrics"],
                           now=dt.datetime(2026, 8, 19, 12, tzinfo=dt.timezone.utc))
    assert brief["as_of_date"] == "2026-08-19"
    assert brief["metrics"]["terminal_count"] == 2
    scrubbed = reasoning(); scrubbed["questions"] = ["Email owner@example.com"]
    cleaned = validate_brief(scrubbed, metrics=snapshot["metrics"])
    assert cleaned["questions"] == ["Email [email removed]"]
    bad = reasoning(); bad["questions"] = ["Review cohort aabbccddeeff"]
    cleaned = validate_brief(bad, metrics=snapshot["metrics"])
    assert cleaned["questions"] == ["Review cohort [cohort removed]"]


def test_brief_normalizes_sparse_and_verbose_advisory_output():
    snapshot, _digest = build_snapshot(rows())
    result = reasoning()
    result.pop("limitations")
    result["questions"] = "What evidence is still missing?"
    result["won_patterns"] = ["x" * 1_000 for _ in range(20)]
    brief = validate_brief(result, metrics=snapshot["metrics"])
    assert brief["limitations"] == []
    assert brief["questions"] == ["What evidence is still missing?"]
    assert len(brief["won_patterns"]) == 5
    assert all(len(value) == 300 for value in brief["won_patterns"])
    assert len(json.dumps(brief, sort_keys=True)) <= 20_000


def test_large_population_uses_balanced_bounded_evidence_and_full_digest():
    population = []
    for index in range(80):
        row = copy.deepcopy(rows()[index % 2])
        row["id"] = f"deal-{index}"
        row["facts"]["Stage"] = "Won" if index < 40 else "Lost"
        row["facts"]["Notes"] = f"note-{index}-" + ("x" * 2_000)
        row["updates"] = [{"body": "y" * 2_000} for _ in range(10)]
        population.append(row)
    snapshot, digest = build_snapshot(population)
    rendered = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    assert len(rendered) <= 120_000
    assert snapshot["metrics"]["terminal_count"] == 80
    assert snapshot["sample"]["selected_won_count"] == snapshot[
        "sample"]["selected_lost_count"]
    assert len(snapshot["deals"]) < 80
    sampled = {row["cohort_key"] for row in snapshot["deals"]}
    unsampled = next(row for row in population if hashlib.sha256(
        row["id"].encode()).hexdigest()[:12] not in sampled)
    changed = copy.deepcopy(population)
    changed[population.index(unsampled)]["facts"]["Notes"] = "changed"
    _snapshot, changed_digest = build_snapshot(changed)
    assert changed_digest != digest


def test_unchanged_portfolio_reuses_cached_brief_without_reasoning_or_write():
    snapshot, digest = build_snapshot(rows())
    cached = validate_brief(reasoning(), metrics=snapshot["metrics"])
    class CRM:
        def terminal_deal_portfolio(self, **kwargs): return rows()
        def deal_pattern_brief_state(self):
            return {"input_sha256": digest, "brief": cached,
                    "reasoning_audit": {"provider": "OpenRouter"}}
        def save_deal_pattern_brief_state(self, state):
            raise AssertionError("unchanged state must not be written")
    with patch.object(config, "PORTFOLIO_MIN_COHORT_SIZE", 1):
        result = run_portfolio_patterns(
            crm=CRM(), live=True,
            reasoner=lambda _snapshot: (_ for _ in ()).throw(
                AssertionError("unchanged input must not reason")))
    assert result["reasoning_calls"] == 0
    assert result["deal_writes"] == 0 and not result["state_changed"]


def test_changed_portfolio_writes_only_aggregate_state():
    saved = []
    class CRM:
        def terminal_deal_portfolio(self, **kwargs): return rows()
        def deal_pattern_brief_state(self): return {}
        def save_deal_pattern_brief_state(self, state):
            saved.append(state); return "state-1"
    with patch.object(config, "PORTFOLIO_MIN_COHORT_SIZE", 1):
        result = run_portfolio_patterns(
            crm=CRM(), live=True, reasoner=lambda _snapshot: reasoning(),
            now=dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc))
    assert result["reasoning_calls"] == 1 and result["state_changed"]
    assert result["state_item_id"] == "state-1" and result["deal_writes"] == 0
    assert saved[0]["brief"]["metrics"]["won_count"] == 1


def test_sparse_cohorts_fail_before_reasoning():
    class CRM:
        def terminal_deal_portfolio(self, **kwargs): return rows()
        def deal_pattern_brief_state(self): return {}
    try:
        run_portfolio_patterns(
            crm=CRM(), reasoner=lambda _snapshot: (_ for _ in ()).throw(
                AssertionError("sparse cohort must not reason")))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "at least 3 Won" in str(error)


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
