"""Deterministic tests for commander-dashboard calculations and settings."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.commander_dashboard import (  # noqa: E402
    CommanderDashboardInstaller, ESTIMATED_CLOSE_COLUMN, OPEN_PIPELINE_COLUMN,
    WidgetSpec, deal_formulas, monthly_goal_rows, revenue_formulas, widget_specs,
)


def test_monthly_targets_sum_exactly_to_annual_goal():
    rows = monthly_goal_rows(2026, 1_500_000)
    assert len(rows) == 12
    assert rows[0] == {
        "name": "January", "month_start": "2026-01-01",
        "monthly_goal": 125_000, "cumulative_goal": 125_000,
    }
    assert rows[-1]["cumulative_goal"] == 1_500_000
    assert sum(row["monthly_goal"] for row in rows) == 1_500_000


def test_monthly_targets_distribute_rounding_without_losing_cents():
    rows = monthly_goal_rows(2026, 100)
    assert sum(round(row["monthly_goal"] * 100) for row in rows) == 10_000
    assert rows[-1]["cumulative_goal"] == 100


def test_deal_formulas_only_include_explicit_open_stages():
    formulas = deal_formulas("stage", "value")
    assert formulas[OPEN_PIPELINE_COLUMN] == (
        'IF(OR({stage}="New",{stage}="Discovery",{stage}="Proposal",'
        '{stage}="Negotiation"),{value},0)')
    assert formulas[ESTIMATED_CLOSE_COLUMN].endswith(",{value}/3,0)")
    assert "Won" not in formulas[OPEN_PIPELINE_COLUMN]
    assert "Lost" not in formulas[OPEN_PIPELINE_COLUMN]


def test_revenue_formulas_are_current_month_and_gap_scoped():
    formulas = revenue_formulas({
        "Month Start": "month", "Monthly Revenue Goal": "goal",
        "Revenue Collected": "actual",
    })
    assert "TODAY()" in formulas["Current Month Revenue"]
    coverage = formulas["Current Month Pipeline Coverage Needed"]
    assert "({goal}-{actual})*3" in coverage
    assert coverage.endswith(",0)")


def test_widget_contract_contains_requested_command_metrics():
    specs = widget_specs(
        deals_board_id="deals", clients_board_id="clients",
        revenue_board_id="revenue",
        deal_columns={
            "Stage": "stage", OPEN_PIPELINE_COLUMN: "pipeline",
            ESTIMATED_CLOSE_COLUMN: "estimated",
        },
        client_columns={"Status": "status"},
        revenue_columns={
            "Month Start": "month", "Monthly Revenue Goal": "goal",
            "Revenue Collected": "actual", "Current Month Revenue": "current",
            "Current Month Pipeline Coverage Needed": "coverage",
        },
        active_clients_group_id="active",
    )
    names = {spec.name for spec in specs}
    assert {
        "Current Pipeline (Open Deals)", "Estimated Closes (33%)",
        "Revenue Collected YTD", "Revenue Collected This Month",
        "2026 Revenue Goal", "Pipeline vs Coverage Needed",
        "Top Active Deals by Value", "Active Client Delivery",
    } <= names
    assert all(spec.kind in {"NUMBER", "CHART", "BATTERY"} for spec in specs)
    revenue_chart = next(spec for spec in specs if spec.name == "Revenue vs Monthly Goal")
    assert revenue_chart.settings["x_axis_group_by"] == "date"


def test_widget_api_receives_settings_as_json_object():
    class FakeCRM:
        def __init__(self):
            self.variables = None

        def _post(self, query, variables=None):
            self.variables = variables
            return {"create_widget": {"id": "w1", "name": "Metric", "kind": "NUMBER"}}

    crm = FakeCRM()
    installer = CommanderDashboardInstaller.__new__(CommanderDashboardInstaller)
    installer.crm = crm
    installer.state = {"widgets": {}}
    installer.actions = []
    installer._save_state = lambda: None
    installer._ensure_widgets("d1", [WidgetSpec(
        "Metric", "NUMBER", {"counter_data": {
            "calculation_type": "count", "column_ids_per_board": {"1": []},
            "counter_type": "sum",
        }})])
    assert isinstance(crm.variables["settings"], dict)


def test_saved_dashboard_id_is_authoritative_without_read_index():
    class NoReadCRM:
        def _post(self, *args, **kwargs):
            raise AssertionError("saved dashboard should not require a read lookup")

    installer = CommanderDashboardInstaller.__new__(CommanderDashboardInstaller)
    installer.crm = NoReadCRM()
    installer.state = {"dashboard_id": "10000017", "widgets": {}}
    assert installer._ensure_dashboard("revenue") == "10000017"


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"{len(tests)}/{len(tests)} tests passed")
