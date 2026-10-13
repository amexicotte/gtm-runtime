"""Install Harbor's native monday commander dashboard.

The installer is additive and idempotent. It creates reporting-only formula
columns on Deals, one monthly revenue-goal board, filtered table views, and a
public dashboard. It never edits Deal, Client Project, or collected-revenue
values.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .monday import BOARDS, Monday
from .lifecycle import OPEN_DEAL_STAGES


WORKSPACE_ID = "10000012"
DASHBOARD_NAME = "Harbor Business Command Center"
DASHBOARD_OBJECT_TYPE = "solutionsv2_monday-dashboards::dashboard_object"
OPEN_STAGES = OPEN_DEAL_STAGES
OPEN_PIPELINE_COLUMN = "Commander | Open Pipeline"
ESTIMATED_CLOSE_COLUMN = "Commander | Estimated Close (33%)"
ACTIVE_DEALS_VIEW = "Commander | Active Deals by Value"
ACTIVE_CLIENTS_VIEW = "Commander | Active Clients"


@dataclass(frozen=True)
class WidgetSpec:
    name: str
    kind: str
    settings: dict[str, Any]


def monthly_goal_rows(year: int, annual_goal: float) -> list[dict[str, Any]]:
    """Return twelve equal monthly targets with an exact annual total."""
    cents = round(float(annual_goal) * 100)
    base, remainder = divmod(cents, 12)
    cumulative = 0
    rows = []
    for month in range(1, 13):
        value_cents = base + (1 if month <= remainder else 0)
        cumulative += value_cents
        rows.append({
            "name": calendar.month_name[month],
            "month_start": dt.date(year, month, 1).isoformat(),
            "monthly_goal": value_cents / 100,
            "cumulative_goal": cumulative / 100,
        })
    return rows


def deal_formulas(stage_id: str, value_id: str) -> dict[str, str]:
    open_test = ",".join(f'{{{stage_id}}}="{stage}"' for stage in OPEN_STAGES)
    return {
        OPEN_PIPELINE_COLUMN: f"IF(OR({open_test}),{{{value_id}}},0)",
        ESTIMATED_CLOSE_COLUMN: f"IF(OR({open_test}),{{{value_id}}}/3,0)",
    }


def revenue_formulas(columns: dict[str, str]) -> dict[str, str]:
    month = columns["Month Start"]
    goal = columns["Monthly Revenue Goal"]
    collected = columns["Revenue Collected"]
    current = (
        f"AND(YEAR({{{month}}})=YEAR(TODAY()),"
        f"MONTH({{{month}}})=MONTH(TODAY()))"
    )
    return {
        "Current Month Revenue": f"IF({current},{{{collected}}},0)",
        "Current Month Pipeline Coverage Needed": (
            f"IF({current},IF({{{goal}}}>{{{collected}}},"
            f"({{{goal}}}-{{{collected}}})*3,0),0)"
        ),
    }


def _currency_counter(board_id: str, column_id: str, *, counter_type: str = "sum",
                      prefix: str = "") -> dict[str, Any]:
    settings: dict[str, Any] = {
        "counter_data": {
            "calculation_type": "columns",
            "column_ids_per_board": {str(board_id): [column_id]},
            "counter_type": counter_type,
            "counter_unit": {"symbol": "$", "direction": "left"},
        },
        "number_format": "currency",
    }
    if prefix:
        settings["prefix"] = prefix
    return settings


def widget_specs(*, deals_board_id: str, clients_board_id: str,
                 revenue_board_id: str, deal_columns: dict[str, str],
                 client_columns: dict[str, str], revenue_columns: dict[str, str],
                 active_clients_group_id: str) -> list[WidgetSpec]:
    """Build settings that conform to monday's live widget schemas."""
    return [
        WidgetSpec("Current Pipeline (Open Deals)", "NUMBER", _currency_counter(
            deals_board_id, deal_columns[OPEN_PIPELINE_COLUMN])),
        WidgetSpec("Estimated Closes (33%)", "NUMBER", _currency_counter(
            deals_board_id, deal_columns[ESTIMATED_CLOSE_COLUMN])),
        WidgetSpec("Revenue Collected YTD", "NUMBER", _currency_counter(
            revenue_board_id, revenue_columns["Revenue Collected"])),
        WidgetSpec("Revenue Collected This Month", "NUMBER", _currency_counter(
            revenue_board_id, revenue_columns["Current Month Revenue"])),
        WidgetSpec("2026 Revenue Goal", "NUMBER", _currency_counter(
            revenue_board_id, revenue_columns["Monthly Revenue Goal"])),
        WidgetSpec("Pipeline Coverage Needed This Month", "NUMBER", _currency_counter(
            revenue_board_id,
            revenue_columns["Current Month Pipeline Coverage Needed"])),
        WidgetSpec("Revenue vs Monthly Goal", "CHART", {
            "graph_type": "column",
            "x_axis_columns": {revenue_board_id: [revenue_columns["Month Start"]]},
            "y_axis_columns": {revenue_board_id: [
                revenue_columns["Revenue Collected"],
                revenue_columns["Monthly Revenue Goal"],
            ]},
            "x_axis_group_by": "date",
            "y_axis_group_by": "numeric",
            "group_by": "month",
        }),
        WidgetSpec("Pipeline vs Coverage Needed", "CHART", {
            "graph_type": "column",
            "x_axis_columns": {
                deals_board_id: ["board"], revenue_board_id: ["board"],
            },
            "y_axis_columns": {
                deals_board_id: [deal_columns[OPEN_PIPELINE_COLUMN]],
                revenue_board_id: [
                    revenue_columns["Current Month Pipeline Coverage Needed"]],
            },
            "x_axis_group_by": "board",
            "y_axis_group_by": "numeric",
        }),
        WidgetSpec("Open Pipeline by Stage", "CHART", {
            "graph_type": "bar",
            "x_axis_columns": {deals_board_id: [deal_columns["Stage"]]},
            "y_axis_columns": {deals_board_id: [deal_columns[OPEN_PIPELINE_COLUMN]]},
            "x_axis_group_by": "color",
            "y_axis_group_by": "numeric",
            "sort_by": "Y_AXIS_DESCENDING",
        }),
        WidgetSpec("Top Active Deals by Value", "CHART", {
            "graph_type": "bar",
            "x_axis_columns": {deals_board_id: ["name"]},
            "y_axis_columns": {deals_board_id: [deal_columns[OPEN_PIPELINE_COLUMN]]},
            "x_axis_group_by": "name",
            "y_axis_group_by": "numeric",
            "sort_by": "Y_AXIS_DESCENDING",
        }),
        WidgetSpec("Active Clients by Status", "CHART", {
            "graph_type": "donut",
            "x_axis_columns": {clients_board_id: [client_columns["Status"]]},
            "y_axis_columns": {clients_board_id: ["default-label-count"]},
            "x_axis_group_by": "color",
            "y_axis_group_by": "default-label-count",
        }),
        WidgetSpec("Active Client Delivery", "BATTERY", {
            "done_text": "Done",
            "battery_data": {
                "status_column_ids_per_board": {
                    clients_board_id: [client_columns["Status"]],
                },
            },
            "groups_data": {
                "group_ids_per_board": {
                    clients_board_id: [active_clients_group_id],
                },
            },
        }),
    ]


class CommanderDashboardInstaller:
    def __init__(self, crm: Monday, *, year: int, annual_goal: float,
                 state_file: Path):
        self.crm = crm
        self.year = int(year)
        self.annual_goal = float(annual_goal)
        self.state_file = state_file
        self.state = self._load_state()
        self.actions: list[dict[str, Any]] = []

    def preview(self) -> dict[str, Any]:
        live = self._live_summary()
        return {
            "mode": "dry-run",
            "dashboard": DASHBOARD_NAME,
            "workspace_id": WORKSPACE_ID,
            "revenue_board": f"Revenue Goals {self.year}",
            "annual_goal": self.annual_goal,
            "monthly_targets": monthly_goal_rows(self.year, self.annual_goal),
            "planned_widgets": [spec.name for spec in self._preview_widgets()],
            "live_data": live,
            "mutations": [
                "add two reporting-only formula columns to Deals",
                "create or reuse the annual Revenue Goals board and seed 12 months",
                "create sorted active-deal and active-client table views",
                "create or resume the public dashboard and missing widgets",
            ],
        }

    def apply(self) -> dict[str, Any]:
        deal_columns = self._ensure_deal_formula_columns()
        revenue_board_id = self._ensure_revenue_board()
        revenue_columns = self._ensure_revenue_columns(revenue_board_id)
        self._ensure_revenue_rows(revenue_board_id, revenue_columns)
        self._ensure_views()
        dashboard_id = self._ensure_dashboard(revenue_board_id)
        client_columns = self._columns(str(BOARDS["client_project"]))
        client_groups = self._groups(str(BOARDS["client_project"]))
        active_group = next((row["id"] for row in client_groups
                             if row["title"] == "New Projects"), None)
        if not active_group:
            raise RuntimeError("Client Projects New Projects group is missing")
        specs = widget_specs(
            deals_board_id=str(BOARDS["deal"]),
            clients_board_id=str(BOARDS["client_project"]),
            revenue_board_id=revenue_board_id,
            deal_columns={title: row["id"] for title, row in deal_columns.items()},
            client_columns={title: row["id"] for title, row in client_columns.items()},
            revenue_columns={title: row["id"] for title, row in revenue_columns.items()},
            active_clients_group_id=str(active_group),
        )
        self._validate_widget_kinds(specs)
        self._ensure_widgets(dashboard_id, specs)
        verified = self._verify_formula_values(revenue_board_id, deal_columns,
                                               revenue_columns)
        return {
            "mode": "apply",
            "dashboard_id": dashboard_id,
            "dashboard_url": f"https://harboradvisors-company.monday.com/overviews/{dashboard_id}",
            "revenue_board_id": revenue_board_id,
            "actions": self.actions,
            "formula_verification": verified,
            "live_data": self._live_summary(),
            "state_file": str(self.state_file),
        }

    def _preview_widgets(self) -> list[WidgetSpec]:
        fake = {
            "Stage": "stage", OPEN_PIPELINE_COLUMN: "open",
            ESTIMATED_CLOSE_COLUMN: "estimated",
        }
        return widget_specs(
            deals_board_id=str(BOARDS["deal"]),
            clients_board_id=str(BOARDS["client_project"]),
            revenue_board_id="revenue-board", deal_columns=fake,
            client_columns={"Status": "status"},
            revenue_columns={
                "Revenue Collected": "collected", "Current Month Revenue": "month",
                "Monthly Revenue Goal": "goal",
                "Current Month Pipeline Coverage Needed": "coverage",
                "Month Start": "date",
            }, active_clients_group_id="active-clients",
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"version": 1, "widgets": {}}
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("widgets"), dict):
            raise RuntimeError(f"invalid commander dashboard state: {self.state_file}")
        return data

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        staged = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        staged.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        staged.replace(self.state_file)

    def _columns(self, board_id: str) -> dict[str, dict]:
        query = "query($ids:[ID!]){boards(ids:$ids){columns{id title type settings}}}"
        rows = self.crm._post(query, {"ids": [board_id]}).get("boards") or []
        if not rows:
            raise RuntimeError(f"monday board not found: {board_id}")
        return {row["title"]: row for row in rows[0]["columns"]}

    def _groups(self, board_id: str) -> list[dict]:
        query = "query($ids:[ID!]){boards(ids:$ids){groups{id title}}}"
        rows = self.crm._post(query, {"ids": [board_id]}).get("boards") or []
        return rows[0]["groups"] if rows else []

    def _ensure_formula(self, board_id: str, title: str, formula: str) -> dict:
        existing = self._columns(board_id).get(title)
        if existing:
            if existing["type"] != "formula":
                raise RuntimeError(f"{title!r} exists but is not a formula column")
            settings = existing.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            if settings.get("formula") != formula:
                raise RuntimeError(f"{title!r} exists with a different formula")
            return existing
        query = ("mutation($board:ID!,$title:String!,$defaults:JSON!){create_column("
                 "board_id:$board,title:$title,column_type:formula,defaults:$defaults)"
                 "{id title type settings}}")
        row = self.crm._post(query, {
            "board": board_id, "title": title,
            "defaults": json.dumps({"settings": {"formula": formula}}),
        })["create_column"]
        self.actions.append({"created_column": f"{board_id}.{title}", "id": row["id"]})
        return row

    def _ensure_column(self, board_id: str, title: str, column_type: str,
                       *, currency: bool = False) -> dict:
        existing = self._columns(board_id).get(title)
        if existing:
            if existing["type"] != column_type:
                raise RuntimeError(
                    f"{title!r} exists as {existing['type']}, expected {column_type}")
            return existing
        defaults = None
        if currency:
            defaults = json.dumps({
                "unit": {"symbol": "$", "direction": "left", "precision": 0},
            })
        query = ("mutation($board:ID!,$title:String!,$defaults:JSON){create_column("
                 f"board_id:$board,title:$title,column_type:{column_type},"
                 "defaults:$defaults){id title type settings}}")
        row = self.crm._post(query, {
            "board": board_id, "title": title, "defaults": defaults,
        })["create_column"]
        self.actions.append({"created_column": f"{board_id}.{title}", "id": row["id"]})
        return row

    def _ensure_deal_formula_columns(self) -> dict[str, dict]:
        columns = self._columns(str(BOARDS["deal"]))
        formulas = deal_formulas(columns["Stage"]["id"], columns["Deal Value"]["id"])
        for title, formula in formulas.items():
            self._ensure_formula(str(BOARDS["deal"]), title, formula)
        return self._columns(str(BOARDS["deal"]))

    def _find_board(self, name: str) -> dict | None:
        query = ("query($workspaces:[ID!]){boards(limit:500,workspace_ids:$workspaces,"
                 "state:active){id name workspace_id}}")
        rows = self.crm._post(query, {"workspaces": [WORKSPACE_ID]}).get("boards") or []
        matches = [row for row in rows if row["name"].strip().casefold() == name.casefold()]
        if len(matches) > 1:
            raise RuntimeError(f"multiple monday boards named {name!r}")
        return matches[0] if matches else None

    def _ensure_revenue_board(self) -> str:
        name = f"Revenue Goals {self.year}"
        existing = self._find_board(name)
        if existing:
            return str(existing["id"])
        query = ("mutation($name:String!,$workspace:ID!){create_board("
                 "board_name:$name,board_kind:public,workspace_id:$workspace)"
                 "{id name}}")
        row = self.crm._post(query, {"name": name, "workspace": WORKSPACE_ID})["create_board"]
        self.actions.append({"created_board": row["name"], "id": str(row["id"])})
        return str(row["id"])

    def _ensure_revenue_columns(self, board_id: str) -> dict[str, dict]:
        self._ensure_column(board_id, "Month Start", "date")
        self._ensure_column(board_id, "Monthly Revenue Goal", "numbers", currency=True)
        self._ensure_column(board_id, "Revenue Collected", "numbers", currency=True)
        self._ensure_column(board_id, "Cumulative Revenue Goal", "numbers", currency=True)
        columns = self._columns(board_id)
        formulas = revenue_formulas({title: row["id"] for title, row in columns.items()})
        for title, formula in formulas.items():
            self._ensure_formula(board_id, title, formula)
        return self._columns(board_id)

    def _board_items(self, board_id: str, column_ids: list[str] | None = None) -> list[dict]:
        query = ("query($ids:[ID!],$columns:[String!]){boards(ids:$ids){"
                 "items_page(limit:100){items{id name column_values(ids:$columns){id text}}}}}")
        boards = self.crm._post(query, {
            "ids": [board_id], "columns": column_ids or [],
        }).get("boards") or []
        return boards[0]["items_page"]["items"] if boards else []

    def _ensure_revenue_rows(self, board_id: str, columns: dict[str, dict]) -> None:
        existing = {row["name"].strip().casefold(): row
                    for row in self._board_items(board_id)}
        for row in monthly_goal_rows(self.year, self.annual_goal):
            if row["name"].casefold() in existing:
                continue
            payload = {
                columns["Month Start"]["id"]: {"date": row["month_start"]},
                columns["Monthly Revenue Goal"]["id"]: str(row["monthly_goal"]),
                columns["Cumulative Revenue Goal"]["id"]: str(row["cumulative_goal"]),
            }
            query = ("mutation($board:ID!,$name:String!,$values:JSON!){create_item("
                     "board_id:$board,item_name:$name,column_values:$values){id}}")
            item = self.crm._post(query, {
                "board": board_id, "name": row["name"],
                "values": json.dumps(payload),
            })["create_item"]
            self.actions.append({"created_revenue_month": row["name"], "id": item["id"]})

    def _status_label_ids(self, board_key: str, title: str,
                          labels: tuple[str, ...]) -> list[int]:
        column = self.crm.column(board_key, title)
        query = ("query($boards:[ID!],$columns:[String!]){boards(ids:$boards){"
                 "columns(ids:$columns){settings}}}")
        settings = self.crm._post(query, {
            "boards": [str(BOARDS[board_key])], "columns": [column["id"]],
        })["boards"][0]["columns"][0]["settings"]
        if isinstance(settings, str):
            settings = json.loads(settings)
        by_label = {str(row.get("label") or "").casefold(): int(row["id"])
                    for row in settings.get("labels") or []}
        missing = [label for label in labels if label.casefold() not in by_label]
        if missing:
            raise RuntimeError(f"missing status labels on {board_key}.{title}: {missing}")
        return [by_label[label.casefold()] for label in labels]

    def _ensure_table_view(self, board_key: str, name: str, *,
                           filter_column: str, excluded_labels: tuple[str, ...],
                           sort_column: str) -> dict:
        for view in self.crm.views(board_key):
            if view["name"].strip().casefold() == name.casefold():
                return view
        excluded = self._status_label_ids(board_key, filter_column, excluded_labels)
        query = ("mutation($board:ID!,$name:String!,$filter:ItemsQueryGroup,"
                 "$sort:[ItemsQueryOrderBy!]){create_view_table("
                 "board_id:$board,name:$name,filter:$filter,sort:$sort){id name type}}")
        row = self.crm._post(query, {
            "board": str(BOARDS[board_key]), "name": name,
            "filter": {"operator": "and", "rules": [{
                "column_id": self.crm.column(board_key, filter_column)["id"],
                "compare_value": excluded, "operator": "not_any_of",
            }]},
            "sort": [{
                "column_id": self.crm.column(board_key, sort_column)["id"],
                "direction": "desc",
            }],
        })["create_view_table"]
        self.actions.append({"created_view": name, "id": row["id"]})
        return row

    def _ensure_views(self) -> None:
        self._ensure_table_view(
            "deal", ACTIVE_DEALS_VIEW, filter_column="Stage",
            excluded_labels=("Paused", "Won", "Lost"), sort_column="Deal Value")
        self._ensure_table_view(
            "client_project", ACTIVE_CLIENTS_VIEW, filter_column="Status",
            excluded_labels=("Done",), sort_column="Priority")

    def _find_dashboard(self) -> dict | None:
        query = ("query($workspace:[ID!],$types:[String!]){objects(limit:100,"
                 "workspace_ids:$workspace,object_type_unique_keys:$types,state:ACTIVE)"
                 "{id name workspace_id privacy_kind}}")
        rows = self.crm._post(query, {
            "workspace": [WORKSPACE_ID], "types": [DASHBOARD_OBJECT_TYPE],
        }).get("objects") or []
        matches = [row for row in rows
                   if str(row.get("name") or "").strip().casefold() ==
                   DASHBOARD_NAME.casefold()]
        if len(matches) > 1:
            raise RuntimeError(f"multiple dashboards named {DASHBOARD_NAME!r}")
        return matches[0] if matches else None

    def _ensure_dashboard(self, revenue_board_id: str) -> str:
        saved_id = str(self.state.get("dashboard_id") or "")
        # monday's dashboard mutation returns the canonical parent ID, but the
        # read-side objects/boards indexes do not consistently expose newly
        # created dashboards. A saved ID is therefore authoritative; widget
        # creation will fail closed if the parent is genuinely unavailable.
        if saved_id:
            return saved_id
        existing = self._find_dashboard()
        if existing:
            raise RuntimeError(
                "a dashboard with the target name exists but is not managed by this "
                "installer; rename it or provide the saved state file")
        query = ("mutation($boards:[ID!]!,$name:String!,$workspace:ID!){"
                 "create_dashboard(board_ids:$boards,kind:PUBLIC,name:$name,"
                 "workspace_id:$workspace){id name kind workspace_id}}")
        row = self.crm._post(query, {
            "boards": [str(BOARDS["deal"]), str(BOARDS["client_project"]),
                       revenue_board_id],
            "name": DASHBOARD_NAME, "workspace": WORKSPACE_ID,
        })["create_dashboard"]
        dashboard_id = str(row["id"])
        self.state.update({
            "dashboard_id": dashboard_id, "dashboard_name": DASHBOARD_NAME,
            "revenue_board_id": revenue_board_id, "year": self.year,
            "annual_goal": self.annual_goal,
        })
        self._save_state()
        self.actions.append({"created_dashboard": DASHBOARD_NAME, "id": dashboard_id})
        return dashboard_id

    def _validate_widget_kinds(self, specs: list[WidgetSpec]) -> None:
        rows = self.crm._post(
            "query{all_widgets_schema{widget_type schema}}"
        ).get("all_widgets_schema") or []
        available = {str(row["widget_type"]) for row in rows}
        missing = sorted({spec.kind for spec in specs} - available)
        if missing:
            raise RuntimeError(f"monday widget kinds are unavailable: {missing}")

    def _ensure_widgets(self, dashboard_id: str, specs: list[WidgetSpec]) -> None:
        saved = self.state.setdefault("widgets", {})
        for spec in specs:
            if saved.get(spec.name):
                continue
            query = ("mutation($parent:WidgetParentInput!,$kind:ExternalWidget!,"
                     "$name:String!,$settings:JSON!){create_widget(parent:$parent,"
                     "kind:$kind,name:$name,settings:$settings){id name kind}}")
            row = self.crm._post(query, {
                "parent": {"kind": "DASHBOARD", "id": dashboard_id},
                "kind": spec.kind, "name": spec.name,
                "settings": spec.settings,
            })["create_widget"]
            saved[spec.name] = str(row["id"])
            self._save_state()
            self.actions.append({"created_widget": spec.name, "id": str(row["id"])})

    def _live_summary(self) -> dict[str, Any]:
        columns = self._columns(str(BOARDS["deal"]))
        selected = {title: columns[title]["id"] for title in (
            "Stage", "Deal Value", "Collected Revenue")}
        items = self._board_items(str(BOARDS["deal"]), list(selected.values()))
        reverse = {value: key for key, value in selected.items()}
        pipeline = 0.0
        open_count = missing_values = revenue_rows = 0
        for item in items:
            values = {reverse[row["id"]]: str(row.get("text") or "")
                      for row in item.get("column_values") or []}
            if values.get("Stage") in OPEN_STAGES:
                open_count += 1
                if values.get("Deal Value").strip():
                    pipeline += _number(values["Deal Value"])
                else:
                    missing_values += 1
            if values.get("Collected Revenue").strip():
                revenue_rows += 1
        client_columns = self._columns(str(BOARDS["client_project"]))
        client_selected = [client_columns[title]["id"]
                           for title in ("Status", "Owner", "Timeline")]
        clients = self._board_items(str(BOARDS["client_project"]), client_selected)
        return {
            "open_deals": open_count,
            "open_deals_missing_value": missing_values,
            "current_pipeline": round(pipeline, 2),
            "estimated_close_at_33_percent": round(pipeline / 3, 2),
            "deal_rows_with_collected_revenue": revenue_rows,
            "active_client_projects": len(clients),
        }

    def _verify_formula_values(self, revenue_board_id: str,
                               deal_columns: dict[str, dict],
                               revenue_columns: dict[str, dict]) -> dict[str, Any]:
        deal_ids = [deal_columns[OPEN_PIPELINE_COLUMN]["id"],
                    deal_columns[ESTIMATED_CLOSE_COLUMN]["id"]]
        revenue_ids = [revenue_columns["Current Month Revenue"]["id"],
                       revenue_columns["Current Month Pipeline Coverage Needed"]["id"]]
        query = ("query($deal:[ID!],$revenue:[ID!],$deal_columns:[String!],"
                 "$revenue_columns:[String!]){deals:boards(ids:$deal){items_page(limit:100){"
                 "items{column_values(ids:$deal_columns){id ... on FormulaValue{display_value}}}}}"
                 "revenue:boards(ids:$revenue){items_page(limit:100){items{name column_values("
                 "ids:$revenue_columns){id ... on FormulaValue{display_value}}}}}}")
        data = self.crm._post(query, {
            "deal": [str(BOARDS["deal"])], "revenue": [revenue_board_id],
            "deal_columns": deal_ids, "revenue_columns": revenue_ids,
        })
        deal_totals = {column_id: 0.0 for column_id in deal_ids}
        for item in data["deals"][0]["items_page"]["items"]:
            for value in item["column_values"]:
                deal_totals[value["id"]] += _number(value.get("display_value") or "0")
        current_name = calendar.month_name[
            dt.datetime.now(ZoneInfo("America/New_York")).month]
        current = next((item for item in data["revenue"][0]["items_page"]["items"]
                        if item["name"] == current_name), None)
        current_values = {value["id"]: _number(value.get("display_value") or "0")
                          for value in (current or {}).get("column_values", [])}
        return {
            "pipeline": round(deal_totals[deal_ids[0]], 2),
            "estimated_close": round(deal_totals[deal_ids[1]], 2),
            "current_month": current_name,
            "current_month_revenue": round(current_values.get(revenue_ids[0], 0), 2),
            "current_month_pipeline_coverage_needed": round(
                current_values.get(revenue_ids[1], 0), 2),
        }


def _number(value: str) -> float:
    raw = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0
