"""Write-discipline tests — pure, no network. Run: python3 tests/test_writes.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.writes import plan_opportunity, new_tasks  # noqa: E402


def _ans(value):
    return {"status": "ANSWERED", "value": value, "quote": "q"}


def test_blank_field_is_set():
    opp = {"expected_revenue": _ans(50000), "identified_pain": _ans("slow pipeline")}
    plan = plan_opportunity({}, opp, verified=False, meeting_date="2026-07-29")
    assert plan.set["Expected revenue"] == 50000
    assert plan.set["Identified Pain"] == "slow pipeline"
    assert plan.propose == []


def test_filled_field_is_proposed_not_overwritten():
    opp = {"expected_revenue": _ans(20000)}
    current = {"Expected revenue": 15000}
    plan = plan_opportunity(current, opp, verified=False, meeting_date="2026-07-29")
    assert "Expected revenue" not in plan.set         # not overwritten
    assert plan.propose and 'was "15000"' in plan.propose[0] and '20000' in plan.propose[0]


def test_equal_value_is_noop():
    opp = {"champion": _ans("Sarah")}
    plan = plan_opportunity({"Champion": "Sarah"}, opp, verified=False, meeting_date="d")
    assert plan.set == {} and plan.propose == []


def test_verified_record_is_untouched():
    opp = {"expected_revenue": _ans(999), "identified_pain": _ans("x")}
    plan = plan_opportunity({}, opp, verified=True, meeting_date="d")
    assert plan.skipped_verified is True
    assert plan.set == {} and plan.propose == []


def test_stage_allows_open_pipeline_and_explicit_lost_but_never_won():
    plan = plan_opportunity({}, {"stage_signal": _ans("Won")},
                            verified=False, meeting_date="d")
    assert "Stage" not in plan.set
    plan2 = plan_opportunity({}, {"stage_signal": _ans("Negotiation")},
                             verified=False, meeting_date="d")
    assert plan2.set["Stage"] == "Negotiation"
    plan3 = plan_opportunity({}, {"stage_signal": _ans("Lost")},
                             verified=False, meeting_date="d")
    assert plan3.set["Stage"] == "Lost"


def test_number_coercion_from_dollar_string():
    plan = plan_opportunity({}, {"expected_revenue": _ans("$50,000")},
                            verified=False, meeting_date="d")
    assert plan.set["Expected revenue"] == 50000.0


def test_gaps_derived_from_not_discussed():
    opp = {
        "metrics": {"status": "NOT_DISCUSSED", "value": None},
        "competition": {"status": "NOT_DISCUSSED", "value": None},
        "identified_pain": _ans("x"),
    }
    plan = plan_opportunity({}, opp, verified=False, meeting_date="d")
    joined = " | ".join(plan.gaps)
    assert "Metrics" in joined and "Competition" in joined
    assert "Identified Pain" not in joined


def test_explicit_gaps_passed_through():
    plan = plan_opportunity({}, {}, verified=False, meeting_date="d",
                            qualification_gaps=["Decision Process — not discussed"])
    assert plan.gaps == ["Decision Process — not discussed"]


def test_task_dedupe():
    tasks = [{"title": "Send the SOW"}, {"title": "send the sow"}, {"title": "Book demo"}]
    out = new_tasks(tasks, existing_titles={"send the sow"})
    titles = [t["title"] for t in out]
    assert titles == ["Book demo"]  # both SOW variants dropped as dupes


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
