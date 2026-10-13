"""Small monday GraphQL client used by the deterministic matcher/gate.

Board IDs are stable configuration; column IDs are deliberately resolved live
from column titles.  This module contains no matching or reasoning logic.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

import requests

from .config import config
from .lifecycle import (
    ACTIVE_CLIENT_TYPE,
    AI_INVESTMENT_TYPE,
    COMPLETED_PAST_CLIENT_TYPE,
    is_active_client_type,
    is_closed_deal_stage,
    is_open_deal_stage,
    is_won_deal_stage,
)


API = "https://api.monday.com/v2"
BOARDS = {
    "account": 10000000001,
    "contact": 10000000002,
    "deal": 10000000003,
    "meeting": 10000000004,
    "activity": 10000000005,
    "client_project": 10000000006,
}
USER_IDS_BY_EMAIL = {
    "alex@harboradvisors.example": "100000007",
    "sam@harboradvisors.example": "100000008",
    "jordan@harboradvisors.example": "100000014",
}
STATUS_COLORS = {
    0: "working_orange", 1: "done_green", 2: "stuck_red", 3: "dark_blue",
    4: "purple", 5: "explosive", 6: "grass_green", 7: "bright_blue",
    8: "saladish", 9: "egg_yolk", 10: "blackish", 11: "dark_red",
    12: "sofia_pink", 13: "lipstick", 14: "dark_purple",
    15: "bright_green", 16: "chili_blue", 17: "american_gray",
    18: "brown", 19: "dark_orange", 101: "sunset", 102: "bubble",
    103: "peach", 104: "berry", 105: "winter", 106: "river",
    107: "navy", 108: "aquamarine", 109: "indigo", 110: "dark_indigo",
    151: "pecan", 152: "lavender", 153: "royal", 154: "steel",
    155: "orchid", 156: "lilac", 157: "tan", 158: "sky",
    159: "coffee", 160: "teal",
}

RETAINER_PROJECTION_MONTHS = 3


class Monday:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("MONDAY_API_TOKEN", "")
        if not self.token:
            raise RuntimeError("missing required env var: MONDAY_API_TOKEN")
        self._columns: dict[str, dict[str, dict]] = {}
        self._agent_events_ready = False
        if config.MONDAY_AGENT_EVENTS_BOARD_ID:
            try:
                BOARDS["agent_event"] = int(config.MONDAY_AGENT_EVENTS_BOARD_ID)
            except ValueError as error:
                raise RuntimeError(
                    "MONDAY_AGENT_EVENTS_BOARD_ID must be a numeric board ID"
                ) from error
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": self.token,
            "API-Version": config.MONDAY_API_VERSION,
            "Content-Type": "application/json",
        })

    def _post(self, query: str, variables: dict | None = None) -> dict:
        mutation = "mutation" in str(query).casefold()
        if mutation:
            config.require_business_writes()
        last_error = None
        for attempt in range(4):
            try:
                response = self.session.post(
                    API, json={"query": query, "variables": variables or {}}, timeout=60)
            except requests.RequestException as error:
                last_error = error
                if mutation:
                    raise RuntimeError(
                        f"monday mutation outcome is unknown: {error}") from error
                if attempt == 3:
                    break
                time.sleep(1 + attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = RuntimeError(
                    f"monday HTTP {response.status_code}: {response.text[:800]}")
                if mutation:
                    raise RuntimeError(
                        f"monday mutation outcome is unknown: {last_error}")
                if attempt == 3:
                    break
                time.sleep(float(response.headers.get("Retry-After", 1 + attempt)))
                continue
            if not response.ok:
                raise RuntimeError(
                    f"monday HTTP {response.status_code}: {response.text[:800]}")
            try:
                result = response.json()
            except ValueError as error:
                if mutation:
                    raise RuntimeError(
                        "monday mutation outcome is unknown: invalid JSON response") from error
                last_error = error
                if attempt == 3:
                    break
                time.sleep(1 + attempt)
                continue
            if result.get("errors"):
                errors = result["errors"]
                transient_codes = {
                    str((row.get("extensions") or {}).get("code") or "")
                    for row in errors if isinstance(row, dict)
                }
                if (not mutation and transient_codes and
                        transient_codes <= {
                            "INTERNAL_SERVER_ERROR", "SERVICE_UNAVAILABLE",
                            "GATEWAY_TIMEOUT",
                        } and attempt < 3):
                    last_error = RuntimeError(
                        "monday GraphQL transient server error")
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError(f"monday GraphQL: {errors}")
            return result["data"]
        raise RuntimeError(f"monday request failed: {last_error}")

    def columns(self, board_key: str) -> dict[str, dict]:
        if board_key not in self._columns:
            query = "query($ids:[ID!]){boards(ids:$ids){columns{id title type}}}"
            rows = self._post(query, {"ids": [str(BOARDS[board_key])]})["boards"]
            if not rows:
                raise RuntimeError(f"monday board not found: {board_key}")
            self._columns[board_key] = {
                col["title"]: {"id": col["id"], "type": col["type"]}
                for col in rows[0]["columns"]
            }
        return self._columns[board_key]

    def column(self, board_key: str, title: str) -> dict:
        col = self.columns(board_key).get(title)
        if not col:
            raise RuntimeError(f"column {title!r} not found on {board_key} board")
        return col

    def optional_column(self, board_key: str, title: str) -> dict | None:
        return self.columns(board_key).get(title)

    def ensure_text_column(self, board_key: str, title: str) -> dict:
        return self.ensure_column(board_key, title, "text")

    def ensure_creation_log_column(self, board_key: str,
                                   title: str = "Created Date") -> dict:
        """Expose monday's immutable item creation timestamp as a date column."""
        existing = self.optional_column(board_key, title)
        if existing:
            if existing.get("type") != "creation_log":
                raise RuntimeError(
                    f"column {title!r} on {board_key} is {existing.get('type')}, "
                    "expected creation_log")
            return existing
        query = ("mutation($board:ID!,$title:String!,$defaults:JSON!){create_column("
                 "board_id:$board,title:$title,column_type:creation_log,"
                 "defaults:$defaults){id title type settings}}")
        column = self._post(query, {
            "board": str(BOARDS[board_key]),
            "title": title,
            "defaults": json.dumps({"mode": "dateOnly"}),
        })["create_column"]
        self._columns.pop(board_key, None)
        return column

    def ensure_board_relation_column(
            self, board_key: str, title: str, target_board_key: str, *,
            allow_multiple_items: bool = False,
            create_reflection: bool = True) -> dict:
        """Create an editable connect-boards column with a fixed target board."""
        existing = self.optional_column(board_key, title)
        if existing:
            if existing["type"] != "board_relation":
                raise RuntimeError(
                    f"column {title!r} on {board_key} is {existing['type']}, "
                    "not an editable board relation")
            return existing
        target = int(BOARDS[target_board_key])
        multiple = "true" if allow_multiple_items else "false"
        reflection = "true" if create_reflection else "false"
        query = ("mutation($board:ID!,$title:String!){create_column("
                 "board_id:$board,title:$title,column_type:board_relation,"
                 f"defaults:{{boardIds:[{target}],allowMultipleItems:{multiple},"
                 f"allowCreateReflectionColumn:{reflection}}})"
                 "{id title type}}")
        column = self._post(query, {
            "board": str(BOARDS[board_key]), "title": title,
        })["create_column"]
        self._columns.pop(board_key, None)
        self._columns.pop(target_board_key, None)
        return column

    def ensure_column(self, board_key: str, title: str, column_type: str) -> dict:
        existing = self.optional_column(board_key, title)
        if existing:
            if existing.get("type") != column_type:
                raise RuntimeError(
                    f"column {title!r} on {board_key} is {existing.get('type')}, "
                    f"expected {column_type}")
            return existing
        if column_type not in (
                "text", "long_text", "link", "status", "date", "numbers", "checkbox"):
            raise ValueError(f"unsupported managed column type: {column_type}")
        query = ("mutation($board:ID!,$title:String!){create_column("
                 f"board_id:$board,title:$title,column_type:{column_type})"
                 "{id title type}}")
        column = self._post(query, {
            "board": str(BOARDS[board_key]), "title": title,
        })["create_column"]
        self._columns.pop(board_key, None)
        return column

    def prepare_deal_fee_schema(self, *, update_formula: bool = False) -> dict:
        """Install the human-managed Deal fee inputs and calculated total.

        A month-to-month retainer contributes the configured three-month
        planning horizon. Hourly work contributes the contracted rate
        multiplied by the team's estimated billable hours. Formula drift fails
        closed unless the caller explicitly authorizes an in-place update.
        """
        board_key = "deal"
        board_id = str(BOARDS[board_key])
        rows = self._post(
            "query($ids:[ID!]){boards(ids:$ids){columns{"
            "id title type description revision settings}}}",
            {"ids": [board_id]},
        )["boards"]
        if not rows:
            raise RuntimeError("monday Deal board not found")
        columns = rows[0]["columns"]
        by_title = {column["title"]: column for column in columns}
        by_id = {column["id"]: column for column in columns}
        created: list[str] = []

        def ensure(*, title: str, column_id: str, column_type: str,
                   description: str, after_column_id: str,
                   defaults: dict | None = None) -> dict:
            existing = by_title.get(title)
            if existing:
                if (existing["id"] != column_id or
                        existing["type"] != column_type):
                    raise RuntimeError(
                        f"Deal column {title!r} exists as {existing['id']!r}/"
                        f"{existing['type']!r}; expected {column_id!r}/"
                        f"{column_type!r}")
                return existing
            collision = by_id.get(column_id)
            if collision:
                raise RuntimeError(
                    f"Deal column ID {column_id!r} is already used by "
                    f"{collision['title']!r}")
            query = (
                "mutation($board:ID!,$title:String!,$description:String!,"
                "$id:String!,$after:ID!,$defaults:JSON){create_column("
                "board_id:$board,title:$title,description:$description,"
                "column_type:" + column_type + ",id:$id,after_column_id:$after,"
                "defaults:$defaults){id title type settings}}"
            )
            column = self._post(query, {
                "board": board_id,
                "title": title,
                "description": description,
                "id": column_id,
                "after": after_column_id,
                "defaults": json.dumps(defaults) if defaults else None,
            })["create_column"]
            by_title[title] = column
            by_id[column_id] = column
            created.append(title)
            return column

        currency = {
            "unit": {"symbol": "$", "direction": "left", "precision": 2},
        }
        fixed = ensure(
            title="Fixed Fee", column_id="fixed_fee", column_type="numbers",
            description=(
                "One-time fixed amount for defined deliverables. Human-entered; "
                "not updated by the listening agent."),
            after_column_id="deal_owner", defaults=currency)
        monthly_description = (
            "Current month-to-month retainer billed at the start of the month. "
            f"{RETAINER_PROJECTION_MONTHS} months contribute to Calculated "
            "Deal Value."
        )
        monthly = ensure(
            title="Monthly Retainer", column_id="monthly_retainer",
            column_type="numbers",
            description=monthly_description,
            after_column_id=fixed["id"], defaults=currency)
        hourly = ensure(
            title="Hourly Rate", column_id="hourly_rate", column_type="numbers",
            description="Contracted hourly billing rate in USD.",
            after_column_id=monthly["id"], defaults=currency)
        hours = ensure(
            title="Estimated Hours", column_id="estimated_hours",
            column_type="numbers",
            description=(
                "Expected billable hours for this deal; used to calculate the "
                "hourly component of deal value."),
            after_column_id=hourly["id"],
            defaults={
                "unit": {"symbol": "custom", "custom_unit": "hrs",
                         "direction": "right", "precision": 1},
            })
        formula = (
            "SUM({fixed_fee},"
            f"MULTIPLY({{monthly_retainer}},{RETAINER_PROJECTION_MONTHS}),"
            "MULTIPLY({hourly_rate},{estimated_hours}))"
        )
        formula_description = (
            f"Fixed Fee + {RETAINER_PROJECTION_MONTHS} months of Monthly "
            "Retainer + (Hourly Rate x Estimated Hours)."
        )
        formula_column = ensure(
            title="Calculated Deal Value", column_id="calc_deal_value",
            column_type="formula",
            description=formula_description,
            after_column_id=hours["id"],
            defaults={"settings": {"formula": formula}})
        settings = formula_column.get("settings") or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        formula_updated = False
        if settings.get("formula") != formula:
            if not update_formula:
                raise RuntimeError(
                    "Calculated Deal Value exists with a different formula; "
                    "rerun the schema migration with update_formula=True")
            revision = formula_column.get("revision")
            if not revision:
                raise RuntimeError(
                    "Calculated Deal Value is missing its monday revision; "
                    "refusing an unsafe formula update")
            query = (
                "mutation($board:ID!,$id:String!,$revision:String!,"
                "$description:String!,$settings:JSON!){update_column("
                "board_id:$board,id:$id,revision:$revision,column_type:formula,"
                "description:$description,settings:$settings)"
                "{id title type description revision settings}}"
            )
            updated = self._post(query, {
                "board": board_id,
                "id": formula_column["id"],
                "revision": str(revision),
                "description": formula_description,
                "settings": {"formula": formula},
            })["update_column"]
            updated_settings = updated.get("settings") or {}
            if isinstance(updated_settings, str):
                updated_settings = json.loads(updated_settings)
            if updated_settings.get("formula") != formula:
                raise RuntimeError(
                    "monday did not persist the three-month retainer formula")
            formula_updated = True

        description_updated = False
        if update_formula and monthly.get("description") != monthly_description:
            query = (
                "mutation($board:ID!,$column:String!,$description:String!){"
                "change_column_metadata(board_id:$board,column_id:$column,"
                "column_property:description,value:$description)"
                "{id title description}}"
            )
            self._post(query, {
                "board": board_id,
                "column": monthly["id"],
                "description": monthly_description,
            })
            description_updated = True
        self._columns.pop(board_key, None)
        return {
            "columns": created,
            "formula": formula,
            "formula_updated": formula_updated,
            "description_updated": description_updated,
            "retainer_projection_months": RETAINER_PROJECTION_MONTHS,
        }

    def views(self, board_key: str) -> list[dict]:
        rows = self._post(
            "query($ids:[ID!]){boards(ids:$ids){views{id name type}}}",
            {"ids": [str(BOARDS[board_key])]},
        )["boards"]
        return rows[0]["views"] if rows else []

    def ensure_table_view(self, board_key: str, name: str,
                          rules: list[dict]) -> dict:
        """Create an idempotent filtered table view using live column IDs."""
        for view in self.views(board_key):
            if view["name"].strip().casefold() == name.strip().casefold():
                return view
        resolved_rules = []
        for rule in rules:
            column = self.column(board_key, rule["column"])
            operator = rule["operator"]
            compare_value = rule.get("compare_value", [])
            if column["type"] in ("status", "color") and isinstance(compare_value, str):
                # View filters are stricter than items_page filters: monday's
                # create_view_table mutation requires numeric status label IDs.
                compare_value = [self.ensure_status_label(
                    board_key, rule["column"], compare_value)]
                operator = "any_of"
            resolved_rules.append({
                "column_id": column["id"],
                "compare_value": compare_value,
                "operator": operator,
            })
        query = ("mutation($board:ID!,$name:String!,$filter:ItemsQueryGroup){"
                 "create_view_table(board_id:$board,name:$name,filter:$filter)"
                 "{id name type}}")
        return self._post(query, {
            "board": str(BOARDS[board_key]), "name": name,
            "filter": {"operator": "and", "rules": resolved_rules},
        })["create_view_table"]

    def ensure_status_label(self, board_key: str, title: str, label: str,
                            *, color: str = "bright_blue",
                            is_done: bool = False) -> int:
        """Return a live status-label ID, creating the label when necessary."""
        column_id = self.column(board_key, title)["id"]
        query = ("query($boards:[ID!],$columns:[String!]){boards(ids:$boards){"
                 "columns(ids:$columns){id type revision settings}}}")
        rows = self._post(query, {
            "boards": [str(BOARDS[board_key])], "columns": [column_id],
        })["boards"]
        if not rows or not rows[0]["columns"]:
            raise RuntimeError(f"status column {title!r} not found on {board_key}")
        column = rows[0]["columns"][0]
        settings = column.get("settings") or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        labels = settings.get("labels") or []
        wanted = label.strip().casefold()
        for item in labels:
            if str(item.get("label") or "").strip().casefold() == wanted:
                return int(item["id"])

        allowed = ("id", "label", "color", "index", "description",
                   "is_deactivated", "is_done")
        updated_labels = []
        for item in labels:
            prepared = {key: item[key] for key in allowed if item.get(key) is not None}
            prepared["color"] = _status_color_name(prepared["color"])
            updated_labels.append(prepared)
        next_index = max((int(item.get("index", -1)) for item in labels), default=-1) + 1
        used_colors = {item["color"] for item in updated_labels}
        requested_color = _status_color_name(color)
        available_colors = [requested_color, *STATUS_COLORS.values()]
        selected_color = next(
            (candidate for candidate in available_colors if candidate not in used_colors), None)
        if not selected_color:
            raise RuntimeError(f"status column {title!r} has no unused label colors")
        updated_labels.append({
            "label": label, "color": selected_color, "index": next_index,
            "is_done": is_done,
        })
        mutation = ("mutation($board:ID!,$column:String!,$revision:String!,"
                    "$settings:UpdateStatusColumnSettingsInput!){"
                    "update_status_column(board_id:$board,id:$column,"
                    "revision:$revision,settings:$settings){id revision settings}}")
        updated = self._post(mutation, {
            "board": str(BOARDS[board_key]), "column": column_id,
            "revision": str(column["revision"]),
            "settings": {"labels": updated_labels},
        })["update_status_column"]
        new_settings = updated.get("settings") or {}
        if isinstance(new_settings, str):
            new_settings = json.loads(new_settings)
        for item in new_settings.get("labels") or []:
            if str(item.get("label") or "").strip().casefold() == wanted:
                return int(item["id"])
        raise RuntimeError(f"monday did not create status label {label!r}")

    def prepare_runtime_schema(self) -> dict:
        """Install managed runtime columns for final writes and Slack feedback."""
        created = {"columns": [], "views": []}
        self.ensure_status_label(
            "account", "Type", AI_INVESTMENT_TYPE, color="navy")
        for board_key in ("account", "contact", "deal", "meeting"):
            existed = bool(self.optional_column(board_key, "Agent Field Provenance"))
            self.ensure_column(board_key, "Agent Field Provenance", "long_text")
            if not existed:
                created["columns"].append(f"{board_key}.Agent Field Provenance")
        for board_key in ("account", "contact", "deal"):
            existed = bool(self.optional_column(board_key, "Review Status"))
            self.ensure_column(board_key, "Review Status", "status")
            if not existed:
                created["columns"].append(f"{board_key}.Review Status")
            self.ensure_status_label(
                board_key, "Review Status", "Verified",
                color="grass_green", is_done=True)

        had_deal_account = bool(self.optional_column("deal", "Account"))
        self.ensure_board_relation_column(
            "deal", "Account", "account", allow_multiple_items=False,
            create_reflection=True)
        if not had_deal_account:
            created["columns"].append("deal.Account")

        had_created_date = bool(self.optional_column("deal", "Created Date"))
        self.ensure_creation_log_column("deal", "Created Date")
        if not had_created_date:
            created["columns"].append("deal.Created Date")

        fee_schema = self.prepare_deal_fee_schema()
        created["columns"].extend(
            f"deal.{title}" for title in fee_schema["columns"])

        for title, kind in (
            ("Attendee Details", "long_text"),
            ("Contact Email IDs", "long_text"),
            ("Slack Intake TS", "text"),
            ("Slack Intake User", "text"),
            # ``long_text`` is capped at 2,000 characters. Keep that legacy
            # field as a card-friendly preview and put the lossless Slack
            # payload in a regular text column.
            ("Manual Notes Cache", "text"),
            # Base64 prevents monday from sanitizing Slack's <url|label>
            # markup or any other source characters in the model payload.
            ("Manual Notes Payload", "text"),
            ("Manual Notes Chars", "text"),
            ("Manual Notes SHA256", "text"),
            # monday long-text values are capped at 2,000 characters. Model
            # output regularly exceeds that, while a text column can use the
            # item's shared ~64 KB value capacity.
            ("Reasoning Cache", "text"),
            ("Reasoning Audit", "text"),
            ("Reasoning Feedback", "text"),
            ("Reasoning Score", "text"),
            ("Feedback Last TS", "text"),
            ("Reasoning Review TS", "text"),
            ("Processing Started At", "text"),
            ("Follow-up Date", "date"),
        ):
            existed = bool(self.optional_column("meeting", title))
            self.ensure_column("meeting", title, kind)
            if not existed:
                created["columns"].append(f"meeting.{title}")

        self.prepare_activity_alert_columns()
        phase2 = self.prepare_phase2_schema()
        created["columns"].extend(phase2["columns"])
        had_slack_channel = bool(
            self.optional_column("client_project", "Slack Channel"))
        self.ensure_column("client_project", "Slack Channel", "link")
        if not had_slack_channel:
            created["columns"].append("client_project.Slack Channel")
        phase4 = self.prepare_docusign_schema()
        created["columns"].extend(phase4["columns"])
        event_schema = self.prepare_agent_events_schema()
        created["columns"].extend(event_schema["columns"])
        created["agent_events_board_id"] = event_schema["board_id"]
        return created

    def prepare_phase2_schema(self) -> dict:
        """Install only deterministic Phase 2 audit and nudge state columns."""
        created = {"columns": []}
        additions = {
            "deal": (
                ("Last Interaction Source Event", "text"),
                ("Last Inbound Email At", "text"),
                ("Last Inbound Source Event", "text"),
                ("Nudge Slack TS", "text"),
                ("Nudge Slack Link", "link"),
                ("Nudge Condition Key", "text"),
            ),
            "activity": (
                ("Source Event Key", "text"),
                ("Completed At", "text"),
                ("Completion Source Type", "text"),
                ("Completion Source Event Key", "text"),
                ("Completion Source Timeline ID", "text"),
                ("Completion Evidence", "long_text"),
                ("Completion Confidence", "numbers"),
                ("Awaiting Client Reply", "checkbox"),
                ("Follow-up Nudge Slack TS", "text"),
                ("Follow-up Nudge Slack Link", "link"),
                ("Follow-up Nudge Condition Key", "text"),
            ),
        }
        for board_key, columns in additions.items():
            for title, kind in columns:
                existed = bool(self.optional_column(board_key, title))
                self.ensure_column(board_key, title, kind)
                if not existed:
                    created["columns"].append(f"{board_key}.{title}")
        return created

    def prepare_docusign_schema(
            self, *, update_deal_fee_formula: bool = False) -> dict:
        """Install executed-contract and client-onboarding columns."""
        created = {"columns": []}
        fee_schema = self.prepare_deal_fee_schema(
            update_formula=update_deal_fee_formula)
        created["columns"].extend(
            f"deal.{title}" for title in fee_schema["columns"])
        created["deal_fee_schema"] = fee_schema
        additions = {
            "deal": (
                ("Agent Field Provenance", "long_text"),
                ("Contracted Scope", "long_text"),
                ("Renewal / Termination Terms", "long_text"),
                ("Contract Signers", "long_text"),
                ("Contract Signed At", "date"),
                ("DocuSign Envelope ID", "text"),
                ("Billing Cadence", "text"),
                ("Payment Terms", "long_text"),
                ("Contract Effective Date", "date"),
                ("Signed Contract Drive Link", "link"),
                ("Client Onboarding Slack TS", "text"),
                ("Client Onboarding Slack Link", "link"),
                ("Client Onboarding Slack Last TS", "text"),
                ("Client Collaboration Connected At", "text"),
            ),
            "account": (
                ("Collaboration Platform", "text"),
                ("Client Collaboration Channel", "link"),
            ),
            "client_project": (
                ("Collaboration Platform", "text"),
                ("Client Collaboration Channel", "link"),
                ("Slack Channel", "link"),
            ),
        }
        for board_key, columns in additions.items():
            for title, kind in columns:
                existed = bool(self.optional_column(board_key, title))
                self.ensure_column(board_key, title, kind)
                if not existed:
                    created["columns"].append(f"{board_key}.{title}")
        return created

    def verify_docusign_schema(self) -> None:
        expected = {
            "deal": {
                "Fixed Fee": "numbers", "Monthly Retainer": "numbers",
                "Hourly Rate": "numbers", "Estimated Hours": "numbers",
                "Calculated Deal Value": "formula",
                "Contracted Scope": "long_text",
                "Renewal / Termination Terms": "long_text",
                "Contract Signers": "long_text", "Contract Signed At": "date",
                "DocuSign Envelope ID": "text",
                "Agent Field Provenance": "long_text",
                "Billing Cadence": "text", "Payment Terms": "long_text",
                "Contract Effective Date": "date",
                "Signed Contract Drive Link": "link",
                "Client Onboarding Slack TS": "text",
                "Client Onboarding Slack Link": "link",
                "Client Onboarding Slack Last TS": "text",
                "Client Collaboration Connected At": "text",
            },
            "account": {
                "Collaboration Platform": "text",
                "Client Collaboration Channel": "link",
            },
            "client_project": {
                "Collaboration Platform": "text",
                "Client Collaboration Channel": "link",
                "Slack Channel": "link",
            },
        }
        problems = []
        for board_key, columns in expected.items():
            for title, kind in columns.items():
                existing = self.optional_column(board_key, title)
                if not existing:
                    problems.append(f"{board_key}.{title} missing")
                elif existing.get("type") != kind:
                    problems.append(
                        f"{board_key}.{title} is {existing.get('type')}, "
                        f"expected {kind}")
        if problems:
            raise RuntimeError(
                "DocuSign schema preflight failed: " + "; ".join(problems))

    def verify_phase2_schema(self) -> None:
        expected = {
            "deal": {
                "Last Interaction Source Event": "text",
                "Last Inbound Email At": "text",
                "Last Inbound Source Event": "text",
                "Nudge Slack TS": "text", "Nudge Slack Link": "link",
                "Nudge Condition Key": "text", "Agent Field Provenance": "long_text",
            },
            "contact": {"Agent Field Provenance": "long_text"},
            "account": {"Agent Field Provenance": "long_text"},
            "activity": {
                "Source Event Key": "text", "Completed At": "text",
                "Completion Source Type": "text",
                "Completion Source Event Key": "text",
                "Completion Source Timeline ID": "text",
                "Completion Evidence": "long_text",
                "Completion Confidence": "numbers",
                "Awaiting Client Reply": "checkbox",
                "Follow-up Nudge Slack TS": "text",
                "Follow-up Nudge Slack Link": "link",
                "Follow-up Nudge Condition Key": "text",
            },
        }
        problems = []
        for board_key, columns in expected.items():
            for title, kind in columns.items():
                existing = self.optional_column(board_key, title)
                if not existing:
                    problems.append(f"{board_key}.{title} missing")
                elif existing.get("type") != kind:
                    problems.append(
                        f"{board_key}.{title} is {existing.get('type')}, expected {kind}")
        if problems:
            raise RuntimeError("Phase 2 schema preflight failed: " + "; ".join(problems))

    def prepare_agent_events_schema(self) -> dict:
        """Create/find the private cross-source ledger and managed columns."""
        created = {"columns": []}
        if "agent_event" not in BOARDS:
            matches = [
                row for row in self._post(
                    "query{boards(limit:100){id name board_kind}}"
                ).get("boards") or []
                if _norm_title(row.get("name") or "") == _norm_title("Agent Events")
            ]
            if len(matches) > 1:
                raise RuntimeError("more than one monday board is named Agent Events")
            if matches:
                if matches[0].get("board_kind") != "private":
                    raise RuntimeError("existing Agent Events board is not private")
                BOARDS["agent_event"] = int(matches[0]["id"])
            else:
                board = self._post(
                    "mutation{create_board(board_name:\"Agent Events\","
                    "board_kind:private){id}}"
                )["create_board"]
                BOARDS["agent_event"] = int(board["id"])
                created["columns"].append("agent_event.Name")
        board_rows = self._post(
            "query($ids:[ID!]){boards(ids:$ids){id name board_kind}}",
            {"ids": [str(BOARDS["agent_event"])]},
        ).get("boards") or []
        if (len(board_rows) != 1 or
                board_rows[0].get("board_kind") != "private"):
            raise RuntimeError("configured Agent Events board must exist and be private")
        for title, kind in (
            ("Source Type", "status"),
            ("Source ID", "text"),
            ("Occurred At", "text"),
            ("Content SHA256", "text"),
            ("Processing Status", "status"),
            ("Processing Started At", "text"),
            ("Matched Account IDs", "text"),
            ("Matched Contact IDs", "text"),
            ("Matched Deal IDs", "text"),
            ("Reasoning Cache", "text"),
            ("Effects/Audit JSON", "text"),
            ("Source URL", "link"),
            ("Error/Ambiguity", "text"),
        ):
            existed = bool(self.optional_column("agent_event", title))
            self.ensure_column("agent_event", title, kind)
            if not existed:
                created["columns"].append(f"agent_event.{title}")
        for label, color, is_done in (
            ("Ready", "bright_blue", False),
            ("Processing", "working_orange", False),
            ("Processed", "grass_green", True),
            ("Ambiguous", "dark_orange", False),
            ("Failed", "stuck_red", False),
        ):
            self.ensure_status_label(
                "agent_event", "Processing Status", label,
                color=color, is_done=is_done)
        self._agent_events_ready = True
        created["board_id"] = str(BOARDS["agent_event"])
        return created

    def claim_agent_event(self, event, result: dict) -> dict:
        """Claim one durable event, resuming only a stale/incomplete record."""
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", event.event_key)
        if len(matches) > 1:
            raise RuntimeError(
                f"duplicate Agent Events ledger rows for {event.event_key!r}")
        reasoning_cache = json.dumps(
            result, sort_keys=True, separators=(",", ":"), default=str)
        now = dt.datetime.now(dt.timezone.utc)
        if not matches:
            values: dict[str, object] = {
                "Source Type": event.source,
                "Source ID": event.source_id,
                "Occurred At": event.occurred_at,
                "Content SHA256": event.content_sha256,
                "Processing Status": "Processing",
                "Processing Started At": now.isoformat(),
                "Matched Account IDs": json.dumps(list(event.account_ids)),
                "Matched Contact IDs": json.dumps(list(event.contact_ids)),
                "Matched Deal IDs": json.dumps(list(event.deal_ids)),
                "Reasoning Cache": reasoning_cache,
                "Effects/Audit JSON": "{}",
                "Error/Ambiguity": "",
            }
            if event.source_url:
                values["Source URL"] = event.source_url
            item_id = str(self.create_item("agent_event", event.event_key, values))
            return {"id": item_id, "claimed": True, "effects": {}}

        item_id = str(matches[0]["id"])
        state = self._agent_event_state(item_id)
        status = state["status"].strip().casefold()
        if status == "processed":
            return {"id": item_id, "claimed": False,
                    "completed": True, "effects": state["effects"],
                    "reasoning_cache": state["reasoning_cache"]}
        if status == "processing" and not _claim_is_stale(
                state["processing_started_at"],
                config.PROCESSING_STALE_MINUTES, now=now):
            return {"id": item_id, "claimed": False,
                    "busy": True, "effects": state["effects"],
                    "reasoning_cache": state["reasoning_cache"]}
        values = {
            "Processing Status": "Processing",
            "Processing Started At": now.isoformat(),
            "Error/Ambiguity": "",
        }
        # A pre-reasoning email claim passes an empty object. Never erase a
        # cached result while recovering a failed/stale processing claim.
        if result and not state["reasoning_cache"]:
            values["Reasoning Cache"] = reasoning_cache
        self.change_columns("agent_event", item_id, values)
        return {"id": item_id, "claimed": True, "effects": state["effects"],
                "reasoning_cache": state["reasoning_cache"]}

    def save_agent_event_reasoning(self, item_id: str, result: dict) -> None:
        self.change_columns("agent_event", item_id, {
            "Reasoning Cache": json.dumps(
                result, sort_keys=True, separators=(",", ":"), default=str),
        })

    def record_agent_event_effect(self, item_id: str, effect_key: str,
                                  details: dict) -> None:
        state = self._agent_event_state(item_id)
        effects = dict(state["effects"])
        if effect_key in effects:
            return
        effects[effect_key] = {
            **details,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.change_columns("agent_event", item_id, {
            "Effects/Audit JSON": json.dumps(
                effects, sort_keys=True, separators=(",", ":")),
        })

    def complete_agent_event(self, item_id: str) -> None:
        self.change_columns("agent_event", item_id, {
            "Processing Status": "Processed",
            "Processing Started At": "",
            "Error/Ambiguity": "",
        })

    def fail_agent_event(self, item_id: str, error: str) -> None:
        self.change_columns("agent_event", item_id, {
            "Processing Status": "Failed",
            "Processing Started At": "",
            "Error/Ambiguity": str(error)[:8_000],
        })

    def mark_agent_event_ambiguous(self, item_id: str, message: str) -> None:
        self.change_columns("agent_event", item_id, {
            "Processing Status": "Ambiguous",
            "Processing Started At": "",
            "Error/Ambiguity": str(message)[:8_000],
        })

    def record_agent_event_ambiguity(self, item_id: str, message: str) -> None:
        self.change_columns("agent_event", item_id, {
            "Error/Ambiguity": str(message)[:8_000],
        })

    def update_has_event_marker(self, item_id: str, effect_key: str) -> bool:
        query = "query($ids:[ID!]){items(ids:$ids){updates(limit:100){id body}}}"
        items = self._post(query, {"ids": [str(item_id)]}).get("items") or []
        return any(
            effect_key in str(update.get("body") or "")
            for item in items for update in item.get("updates") or []
        )

    def post_narrative_update(self, item_id: str, body: str) -> str:
        query = (
            "mutation($item:ID!,$body:String!){"
            "create_update(item_id:$item,body:$body){id}}"
        )
        return str(self._post(query, {
            "item": str(item_id), "body": body,
        })["create_update"]["id"])

    def _agent_event_state(self, item_id: str) -> dict:
        selected = [
            "Processing Status", "Processing Started At", "Effects/Audit JSON",
            "Reasoning Cache", "Error/Ambiguity",
        ]
        columns = {title: self.column("agent_event", title)["id"]
                   for title in selected}
        query = (
            "query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
            "column_values(ids:$columns){id text value}}}"
        )
        items = self._post(query, {
            "ids": [str(item_id)], "columns": list(columns.values()),
        }).get("items") or []
        values = {
            value["id"]: value for value in (
                items[0].get("column_values") if items else []) or []
        }
        effects_raw = _raw_text(values.get(columns["Effects/Audit JSON"], {}))
        effects = _json_object(effects_raw)
        return {
            "status": _raw_text(values.get(columns["Processing Status"], {})),
            "processing_started_at": _raw_text(
                values.get(columns["Processing Started At"], {})),
            "effects": effects if isinstance(effects, dict) else {},
            "reasoning_cache": _json_object(
                _raw_text(values.get(columns["Reasoning Cache"], {}))),
            "error": _raw_text(values.get(columns["Error/Ambiguity"], {})),
        }

    def email_reconciliation_state(self) -> dict:
        """Read the durable E&A high-water/dedupe state from Agent Events."""
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", "state:email-reconciliation")
        if len(matches) > 1:
            raise RuntimeError("duplicate email reconciliation state rows")
        if not matches:
            return {}
        return self._agent_event_state(str(matches[0]["id"]))["reasoning_cache"]

    def save_email_reconciliation_state(self, state: dict) -> None:
        """Persist high-water state only after effects have completed."""
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", "state:email-reconciliation")
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"))
        occurred = str(state.get("high_water_at") or "")
        if not matches:
            self.create_item("agent_event", "state:email-reconciliation", {
                "Source Type": "email",
                "Source ID": "reconciliation-state",
                "Occurred At": occurred,
                "Content SHA256": hashlib.sha256(payload.encode()).hexdigest(),
                "Processing Status": "Processed",
                "Processing Started At": "",
                "Matched Account IDs": "[]",
                "Matched Contact IDs": "[]",
                "Matched Deal IDs": "[]",
                "Reasoning Cache": payload,
                "Effects/Audit JSON": "{}",
                "Error/Ambiguity": "",
            })
            return
        if len(matches) > 1:
            raise RuntimeError("duplicate email reconciliation state rows")
        self.change_columns("agent_event", str(matches[0]["id"]), {
            "Occurred At": occurred,
            "Content SHA256": hashlib.sha256(payload.encode()).hexdigest(),
            "Reasoning Cache": payload,
            "Processing Status": "Processed",
            "Error/Ambiguity": "",
        })

    def docusign_reconciliation_state(self) -> dict:
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", "state:docusign-reconciliation")
        if len(matches) > 1:
            raise RuntimeError("duplicate DocuSign reconciliation state rows")
        if not matches:
            return {}
        return self._agent_event_state(str(matches[0]["id"]))["reasoning_cache"]

    def save_docusign_reconciliation_state(self, state: dict) -> None:
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", "state:docusign-reconciliation")
        if len(matches) > 1:
            raise RuntimeError("duplicate DocuSign reconciliation state rows")
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"))
        values = {
            "Occurred At": str(state.get("high_water_at") or ""),
            "Content SHA256": hashlib.sha256(payload.encode()).hexdigest(),
            "Reasoning Cache": payload, "Processing Status": "Processed",
            "Error/Ambiguity": "",
        }
        if not matches:
            self.create_item("agent_event", "state:docusign-reconciliation", {
                "Source Type": "DocuSign", "Source ID": "reconciliation-state",
                "Processing Started At": "", "Matched Account IDs": "[]",
                "Matched Contact IDs": "[]", "Matched Deal IDs": "[]",
                "Effects/Audit JSON": "{}", **values,
            })
            return
        self.change_columns("agent_event", str(matches[0]["id"]), values)

    def docusign_deal_context(self, deal_id: str) -> dict:
        titles = ["Stage", "Account", "Contacts", "DocuSign Envelope ID",
                  "Agent Field Provenance"]
        columns = {title: self.column("deal", title)["id"] for title in titles}
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "id name board{id} column_values(ids:$columns){id text value "
                 "... on BoardRelationValue{linked_item_ids}}}}")
        rows = self._post(query, {
            "ids": [str(deal_id)], "columns": list(columns.values()),
        }).get("items") or []
        if len(rows) != 1 or str((rows[0].get("board") or {}).get("id")) != str(
                BOARDS["deal"]):
            raise ValueError("monday_deal_id does not identify one Deal")
        item = rows[0]
        values = {row["id"]: row for row in item.get("column_values") or []}
        account_ids = [str(value) for value in values.get(
            columns["Account"], {}).get("linked_item_ids") or []]
        if len(account_ids) != 1:
            raise ValueError("signed contract Deal requires exactly one Account")
        return {
            "id": str(item["id"]), "name": str(item.get("name") or ""),
            "stage": str(values.get(columns["Stage"], {}).get("text") or ""),
            "account_id": account_ids[0],
            "account_name": self.item_name(account_ids[0]),
            "contact_ids": [str(value) for value in values.get(
                columns["Contacts"], {}).get("linked_item_ids") or []],
            "envelope_id": _raw_text(values.get(
                columns["DocuSign Envelope ID"], {})),
            "provenance": _json_object(_raw_text(values.get(
                columns["Agent Field Provenance"], {}))),
        }

    def resolve_docusign_deal(self, recipient_emails: tuple[str, ...]) -> dict:
        """Resolve one Account and one open Deal from exact recipient domains."""
        account_index = self.email_account_index()
        domains = {
            email.rsplit("@", 1)[-1].strip().casefold().removeprefix("www.")
            for email in recipient_emails if "@" in email
        } - {"harboradvisors.example"}
        matched = {
            str(account_index[domain]["id"]): {
                **account_index[domain], "domain": domain,
            }
            for domain in domains if domain in account_index
        }
        if len(matched) != 1:
            raise ValueError(
                "recipient domains did not resolve to exactly one existing Account")
        account = next(iter(matched.values()))
        deals = self.active_deals_for_account(str(account["id"]))
        if len(deals) != 1:
            raise ValueError(
                "recipient-domain Account does not have exactly one active Deal")
        deal = self.docusign_deal_context(str(deals[0]["id"]))
        if deal["account_id"] != str(account["id"]):
            raise ValueError("resolved Deal does not belong to recipient-domain Account")
        return {**deal, "account_name": str(account.get("name") or ""),
                "matched_domain": str(account["domain"]),
                "drive_folder_url": self.account_drive_folder(str(account["id"]))}

    def account_drive_folder(self, account_id: str) -> str:
        column = self.column("account", "Drive Folder")
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "column_values(ids:$columns){text value}}}")
        rows = self._post(query, {
            "ids": [str(account_id)], "columns": [column["id"]],
        }).get("items") or []
        if len(rows) != 1 or not rows[0].get("column_values"):
            raise ValueError("resolved Account has no Drive Folder")
        return _link_url(rows[0]["column_values"][0])

    def apply_docusign_contract(self, *, deal_id: str, envelope_id: str,
                                completed_at: str, terms: dict) -> bool:
        """Atomically finalize structured terms and set Won after exact checks."""
        context = self.docusign_deal_context(deal_id)
        stage = context["stage"].strip().casefold()
        if stage in {"lost", "closed lost"}:
            raise ValueError("a signed contract cannot automatically reopen a Lost Deal")
        existing_envelope = context["envelope_id"].strip()
        if existing_envelope and existing_envelope != envelope_id:
            raise ValueError(
                "Deal already has a different canonical DocuSign envelope")
        values: dict[str, object] = {
            "Stage": "Won", "DocuSign Envelope ID": envelope_id,
            "Contract Signed At": terms["contract_signed_at"],
            "Contract Signers": "\n".join(terms.get("contract_signers") or []),
        }
        mapping = {
            "fixed_fee": "Fixed Fee", "monthly_retainer": "Monthly Retainer",
            "hourly_rate": "Hourly Rate", "estimated_hours": "Estimated Hours",
            "deal_value": "Deal Value", "billing_model": "Billing Model",
            "service_type": "Service Type", "engagement_dates": "Engagement Dates",
            "contracted_scope": "Contracted Scope",
            "renewal_termination_terms": "Renewal / Termination Terms",
            "billing_cadence": "Billing Cadence",
            "payment_terms": "Payment Terms",
            "effective_date": "Contract Effective Date",
            "signed_contract_drive_link": "Signed Contract Drive Link",
        }
        for key, title in mapping.items():
            if key in terms:
                values[title] = terms[key]
        provenance = context["provenance"] if isinstance(
            context["provenance"], dict) else {}
        for key in (
                "deal_value", "fixed_fee", "monthly_retainer", "hourly_rate",
                "estimated_hours", "billing_model", "service_type",
                "engagement_dates", "billing_cadence", "payment_terms"):
            if key in terms:
                provenance[key] = {
                    "occurred_at": completed_at, "source_id": envelope_id,
                    "source_type": "docusign", "authoritative": True,
                }
        values["Agent Field Provenance"] = json.dumps(
            provenance, sort_keys=True, separators=(",", ":"))
        if self.optional_column("deal", "Review Status"):
            values["Review Status"] = "Verified"
        self.change_columns(
            "deal", deal_id, values, create_labels_if_missing=False)
        return not (stage in {"won", "closed won"} and
                    existing_envelope == envelope_id)

    def client_onboarding_alert(self, deal_id: str) -> dict:
        titles = [
            "Client Onboarding Slack TS", "Client Onboarding Slack Link",
            "Client Onboarding Slack Last TS",
            "Client Collaboration Connected At",
        ]
        columns = {title: self.column("deal", title)["id"] for title in titles}
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "column_values(ids:$columns){id text}}}")
        rows = self._post(query, {
            "ids": [str(deal_id)], "columns": list(columns.values()),
        }).get("items") or []
        values = {
            value["id"]: str(value.get("text") or "")
            for value in (rows[0].get("column_values") if rows else []) or []
        }
        return {
            "slack_ts": values.get(columns[titles[0]], ""),
            "slack_link": values.get(columns[titles[1]], ""),
            "slack_last_ts": values.get(columns[titles[2]], ""),
            "connected_at": values.get(columns[titles[3]], ""),
        }

    def save_client_onboarding_alert(self, deal_id: str, ts: str,
                                     link: str) -> None:
        self.change_columns("deal", deal_id, {
            "Client Onboarding Slack TS": ts,
            "Client Onboarding Slack Link": link,
            "Client Onboarding Slack Last TS": ts,
        })

    def pending_client_onboarding(self) -> list[dict]:
        rows = []
        for deal in self.won_deals_for_engagements():
            alert = self.client_onboarding_alert(deal["id"])
            project = self.active_client_project_for_deal(
                deal["account_id"], deal["id"])
            if alert.get("slack_ts") and not alert.get("connected_at") and project:
                rows.append({
                    "deal_id": deal["id"],
                    "account_id": deal["account_id"],
                    "project_id": str(project["id"]),
                    **alert,
                })
        return rows

    def save_client_onboarding_cursor(self, deal_id: str, ts: str) -> None:
        self.change_columns("deal", deal_id, {
            "Client Onboarding Slack Last TS": ts,
        })

    def connect_client_channel(self, row: dict, platform: str, url: str,
                               ts: str) -> None:
        self.change_columns("account", row["account_id"], {
            "Collaboration Platform": platform,
            "Client Collaboration Channel": url,
        })
        values = {
            "Collaboration Platform": platform,
            "Client Collaboration Channel": url,
        }
        if platform == "Slack":
            values["Slack Channel"] = url
        self.change_columns("client_project", row["project_id"], values)
        self.change_columns("deal", row["deal_id"], {
            "Client Collaboration Connected At":
                dt.datetime.now(dt.timezone.utc).isoformat(),
            "Client Onboarding Slack Last TS": ts,
        })

    def email_contact_index(self) -> dict[str, dict]:
        """Return exact email -> Contact relations used by the E&A adapter."""
        titles = ["Email"]
        for title in ("Accounts", "Deals"):
            if self.optional_column("contact", title):
                titles.append(title)
        columns = {title: self.column("contact", title)["id"] for title in titles}
        cursor = None
        identities: dict[str, list[dict]] = {}
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["contact"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {row["id"]: row for row in item["column_values"]}
                email = (values.get(columns["Email"], {}).get("text") or "").strip().lower()
                if not email:
                    continue
                identities.setdefault(email, []).append({
                    "id": str(item["id"]), "name": item["name"], "email": email,
                    "account_ids": [str(value) for value in values.get(
                        columns.get("Accounts"), {}).get("linked_item_ids") or []],
                    "deal_ids": [str(value) for value in values.get(
                        columns.get("Deals"), {}).get("linked_item_ids") or []],
                })
            cursor = page.get("cursor")
            if not cursor:
                configured = _json_dict(
                    config.EMAIL_CONTACT_IDENTITY_RESOLUTIONS)
                resolved: dict[str, dict] = {}
                ambiguities = []
                resolutions = []
                for email, records in identities.items():
                    if len(records) == 1:
                        resolved[email] = records[0]
                        continue
                    identity_hash = hashlib.sha256(email.encode()).hexdigest()
                    canonical_id = configured.get(identity_hash, "")
                    if not canonical_id:
                        ambiguities.append(records)
                        continue
                    matches = [row for row in records
                               if str(row["id"]) == canonical_id]
                    if len(matches) != 1:
                        raise RuntimeError(
                            "configured Contact identity resolution does not name "
                            "one member of its duplicate group")
                    canonical = dict(matches[0])
                    canonical["timeline_contact_ids"] = [
                        str(row["id"]) for row in records]
                    resolved[email] = canonical
                    resolutions.append({
                        "identity_sha256": identity_hash,
                        "canonical_contact_id": canonical_id,
                        "quarantined_contact_ids": [
                            str(row["id"]) for row in records
                            if str(row["id"]) != canonical_id],
                    })
                self.email_contact_ambiguities = ambiguities
                self.email_contact_ambiguity_count = len(
                    self.email_contact_ambiguities)
                self.email_contact_identity_resolutions = resolutions
                return resolved

    def email_account_index(self) -> dict[str, dict]:
        """Return exact normalized business domain -> Account."""
        title = "Domain (match key)"
        column = self.optional_column("account", title)
        if not column:
            return {}
        cursor = None
        rows: dict[str, dict] = {}
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["account"])],
                "columns": [column["id"]], "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                domain = str(
                    (item.get("column_values") or [{}])[0].get("text") or ""
                ).strip().casefold().removeprefix("www.")
                if not domain:
                    continue
                if domain in rows and rows[domain]["id"] != str(item["id"]):
                    raise RuntimeError(f"duplicate Account domain match key: {domain}")
                rows[domain] = {"id": str(item["id"]), "name": item["name"]}
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def email_deal_index(self) -> dict[str, dict]:
        """Return all Deal relations/stages once, avoiding per-message reads."""
        titles = ["Stage"]
        for title in (
                "Account", "Contacts", "Owner", "Last interaction",
                "Last Inbound Email At", "Nudge Slack TS", "Nudge Slack Link",
                "Nudge Condition Key"):
            if self.optional_column("deal", title):
                titles.append(title)
        columns = {title: self.column("deal", title)["id"] for title in titles}
        cursor = None
        rows: dict[str, dict] = {}
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["deal"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {row["id"]: row for row in item["column_values"]}
                stage = (values.get(columns["Stage"], {}).get("text") or "").strip()
                rows[str(item["id"])] = {
                    "id": str(item["id"]), "name": item["name"], "stage": stage,
                    # Paused remains routable CRM context but is deliberately
                    # excluded from pipeline and stalled-Deal nudges.
                    "active": is_open_deal_stage(stage),
                    "account_ids": [str(value) for value in values.get(
                        columns.get("Account"), {}).get("linked_item_ids") or []],
                    "contact_ids": [str(value) for value in values.get(
                        columns.get("Contacts"), {}).get("linked_item_ids") or []],
                    "owner_ids": _people_ids(values.get(
                        columns.get("Owner"), {}).get("value")),
                    "owner": values.get(columns.get("Owner"), {}).get("text") or "",
                    "last_interaction_at": _date_time(
                        values.get(columns.get("Last interaction"), {})),
                    "last_inbound_email_at": _raw_text(values.get(
                        columns.get("Last Inbound Email At"), {})),
                    "nudge_ts": _raw_text(values.get(
                        columns.get("Nudge Slack TS"), {})),
                    "nudge_link": _raw_text(values.get(
                        columns.get("Nudge Slack Link"), {})),
                    "nudge_key": _raw_text(values.get(
                        columns.get("Nudge Condition Key"), {})),
                }
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def email_timeline_items(self, contact_id: str) -> dict:
        """Read monday's supported latest E&A page for one Contact."""
        query = ("query($id:ID!){timeline(id:$id,skipConnectedItems:true){"
                 "timeline_items_page{cursor timeline_items{id title type content "
                 "custom_activity_id created_at "
                 "user{id name} board{id} item{id name} metadata{"
                 "... on EmailTimelineItemMetadata{from to cc bcc}}}}}}")
        page = (self._post(query, {"id": str(contact_id)}).get("timeline") or {}
                ).get("timeline_items_page") or {}
        return {
            "cursor": str(page.get("cursor") or ""),
            "items": page.get("timeline_items") or [],
        }

    def email_reasoning_context(self, *, account_id: str | None,
                                contact_ids: list[str],
                                deal_id: str | None) -> dict:
        account = (self._selected_item_context(
            "account", account_id, ["Type", "Engagement Type", "Industry", "Notes"])
            if account_id else {})
        contacts = [self._selected_item_context(
            "contact", contact_id,
            ["Email", "Role / Title", "Relationship Type", "Notes"])
            for contact_id in contact_ids]
        deal = (self._selected_item_context(
            "deal", deal_id, ["Stage", "Deal Value", "Expected Close Date",
                              "Fixed Fee", "Monthly Retainer", "Hourly Rate",
                              "Identified Pain", "Notes"])
                if deal_id else {})
        return {"account": account, "contacts": [row for row in contacts if row],
                "deal": deal}

    def bounded_crm_context(self, entity_ids: list[str]) -> list[dict]:
        """Read a capped allowlist of current CRM fields for exact item IDs."""
        if not entity_ids:
            return []
        allowed = {
            "account": ["Type", "Engagement Type", "Industry", "Notes",
                        "Last interaction"],
            "contact": ["Role / Title", "Relationship Type", "Notes",
                        "Last interaction"],
            "deal": ["Stage", "Deal Value", "Expected Close Date", "Fixed Fee",
                     "Monthly Retainer", "Hourly Rate", "Identified Pain",
                     "Notes", "Last interaction"],
            "client_project": ["Status", "Owner", "Priority", "Timeline"],
        }
        board_keys = {str(value): key for key, value in BOARDS.items()
                      if key in allowed}
        query = ("query($ids:[ID!]){items(ids:$ids){id name board{id} "
                 "column_values{id text}}}")
        rows = self._post(query, {"ids": [str(value) for value in entity_ids]
                                  }).get("items") or []
        result = []
        for item in rows:
            board_key = board_keys.get(str((item.get("board") or {}).get("id") or ""))
            if not board_key:
                continue
            columns = self.columns(board_key)
            selected = {columns[title]["id"]: title for title in allowed[board_key]
                        if title in columns}
            values = {selected[row["id"]]: str(row.get("text") or "")
                      for row in item.get("column_values") or []
                      if row.get("id") in selected and row.get("text")}
            result.append({"id": str(item["id"]), "type": board_key,
                           "name": str(item.get("name") or ""), "fields": values})
        return result

    def recent_updates_for_items(self, entity_ids: list[str], *, limit: int = 5
                                 ) -> list[dict]:
        """Read recent native monday Updates for exact item IDs."""
        query = ("query($ids:[ID!],$limit:Int!){items(ids:$ids){id name "
                 "updates(limit:$limit){id body created_at}}}")
        items = self._post(query, {
            "ids": [str(value) for value in entity_ids], "limit": int(limit),
        }).get("items") or []
        return [{
            "item_id": str(item["id"]), "item_name": str(item.get("name") or ""),
            "updates": [{"id": str(row.get("id") or ""),
                         "created_at": str(row.get("created_at") or ""),
                         "body": str(row.get("body") or "")[:8_000]}
                        for row in (item.get("updates") or [])[:limit]],
        } for item in items]

    def terminal_deal_portfolio(self, *, max_deals: int = 200,
                                updates_limit: int = 10) -> list[dict]:
        """Read Won/Lost Deal facts and recent Updates without mutating Deals."""
        titles = ["Stage"]
        optional = [
            "Deal Value", "Fixed Fee", "Monthly Retainer", "Hourly Rate",
            "Service Type", "Billing Model", "Source",
            "Identified Pain", "Economic Buyer", "Decision Criteria",
            "Decision Process", "Qualification Gaps", "Competition", "Notes",
            "Contacts", "Champion", "Expected Close Date", "Engagement Dates",
        ]
        titles.extend(title for title in optional
                      if self.optional_column("deal", title))
        columns = {title: self.column("deal", title)["id"] for title in titles}
        relation_titles = {"Contacts", "Champion"}
        cursor = None
        rows = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){"
                     "cursor items{id name created_at updated_at "
                     "column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["deal"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page.get("items") or []:
                values = {value["id"]: value
                          for value in item.get("column_values") or []}
                stage = str(values.get(columns["Stage"], {}).get("text") or "").strip()
                if not is_closed_deal_stage(stage):
                    continue
                facts = {}
                for title, column_id in columns.items():
                    value = values.get(column_id, {})
                    if title in relation_titles:
                        facts[title] = len(value.get("linked_item_ids") or [])
                    else:
                        facts[title] = str(value.get("text") or "")
                rows.append({
                    "id": str(item["id"]), "name": str(item.get("name") or ""),
                    "created_at": str(item.get("created_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                    "facts": facts,
                })
            cursor = page.get("cursor")
            if not cursor:
                break
        if len(rows) > max_deals:
            raise RuntimeError(
                f"terminal Deal count exceeds configured cap ({len(rows)} > {max_deals})")
        updates_by_id = {}
        for chunk in _chunks([row["id"] for row in rows], 50):
            for item in self.recent_updates_for_items(chunk, limit=updates_limit):
                updates_by_id[item["item_id"]] = item["updates"]
        for row in rows:
            row["updates"] = updates_by_id.get(row["id"], [])
        return rows

    def deal_pattern_brief_state(self) -> dict:
        """Read the one private Agent Events state row for Phase 5."""
        if "agent_event" not in BOARDS:
            raise RuntimeError("MONDAY_AGENT_EVENTS_BOARD_ID is required")
        matches = self._items_named("agent_event", "state:deal-pattern-brief")
        if len(matches) > 1:
            raise RuntimeError("duplicate deal pattern brief state rows")
        if not matches:
            return {}
        return self._agent_event_state(str(matches[0]["id"]))["reasoning_cache"]

    def save_deal_pattern_brief_state(self, state: dict) -> str:
        """Write only the private aggregate brief state; never a Deal record."""
        if not self._agent_events_ready:
            self.prepare_agent_events_schema()
        matches = self._items_named("agent_event", "state:deal-pattern-brief")
        if len(matches) > 1:
            raise RuntimeError("duplicate deal pattern brief state rows")
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"))
        generated_at = str(state.get("generated_at") or "")
        values = {
            "Source Type": "manual_note", "Source ID": "deal-pattern-brief",
            "Occurred At": generated_at,
            "Content SHA256": hashlib.sha256(payload.encode()).hexdigest(),
            "Processing Status": "Processed", "Processing Started At": "",
            "Matched Account IDs": "[]", "Matched Contact IDs": "[]",
            "Matched Deal IDs": "[]", "Reasoning Cache": payload,
            "Effects/Audit JSON": json.dumps({
                "kind": "advisory_portfolio_brief", "deal_writes": 0,
            }, separators=(",", ":")),
            "Error/Ambiguity": "",
        }
        if not matches:
            return str(self.create_item(
                "agent_event", "state:deal-pattern-brief", values))
        item_id = str(matches[0]["id"])
        self.change_columns("agent_event", item_id, values)
        return item_id

    def open_activity_candidates_for_entities(self, entity_ids: set[str]) -> list[dict]:
        """Retrieve only open Tasks related to the already-resolved entities."""
        if not entity_ids:
            return []
        titles = ["Status", "Activity Type", "Task Type", "Due Date", "Related item"]
        columns = {title: self.column("activity", title)["id"] for title in titles}
        cursor = None
        rows = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["activity"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {row["id"]: row for row in item["column_values"]}
                status = values.get(columns["Status"], {}).get("text") or ""
                kind = values.get(columns["Activity Type"], {}).get("text") or ""
                related = [str(value) for value in values.get(
                    columns["Related item"], {}).get("linked_item_ids") or []]
                if (status.casefold() == "done" or kind.casefold() != "task" or
                        not entity_ids.intersection(related)):
                    continue
                rows.append({
                    "id": str(item["id"]), "title": item["name"],
                    "task_type": values.get(columns["Task Type"], {}).get("text") or "",
                    "due_date": values.get(columns["Due Date"], {}).get("text") or "",
                    "related_ids": related,
                })
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def meetings_for_call_notes(self, *, include_processed: bool = False) -> list[dict]:
        """Return unprocessed meeting metadata used by the Slack note matcher."""
        titles = ["Date", "Processing Status", "Attendee Emails", "Account"]
        titles.extend(title for title in (
            "Manual Notes", "Manual Notes Cache", "Manual Notes Payload",
            "Manual Notes Chars",
            "Manual Notes SHA256", "Slack Note IDs", "Call Notes Prompt TS",
            "Call Notes Last TS", "Notes Requested At", "Notes Status",
            "Fireflies Transcript ID", "Prospect Account ID",
            "Partner Account IDs", "Contact IDs", "Resolved Deal ID",
            "Contact Email IDs",
            "External Attendees", "Deal",
            "Fireflies Summary", "Owner", "Attendee Details",
            "Reasoning Cache", "Reasoning Result", "Processing Started At",
            "Slack Intake TS", "Slack Intake User",
            "Reasoning Audit", "Reasoning Feedback", "Reasoning Score",
            "Feedback Last TS", "Reasoning Review TS",
        )
                      if self.optional_column("meeting", title))
        columns = {title: self.column("meeting", title)["id"] for title in titles}
        cursor = None
        rows = []
        while True:
            query = """
            query($boards:[ID!], $columns:[String!], $cursor:String) {
              boards(ids:$boards) {
                items_page(limit:100, cursor:$cursor) {
                  cursor
                  items {
                    id
                    name
                    created_at
                    column_values(ids:$columns) {
                      id
                      text
                      value
                      ... on BoardRelationValue { linked_item_ids }
                    }
                  }
                }
              }
            }
            """
            page = self._post(query, {
                "boards": [str(BOARDS["meeting"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                status = (values.get(columns["Processing Status"], {}).get("text") or "")
                if status == "Processed" and not include_processed:
                    continue
                account_ids = values.get(columns["Account"], {}).get("linked_item_ids") or []
                rows.append({
                    "id": str(item["id"]), "title": item["name"],
                    "created_at": item.get("created_at") or "",
                    "date": values.get(columns["Date"], {}).get("text") or "",
                    "status": status,
                    "attendee_emails": values.get(columns["Attendee Emails"], {}).get("text") or "",
                    "attendee_names": _json_dict(values.get(
                        columns.get("Attendee Details"), {}).get("text") or ""),
                    "account_ids": [str(value) for value in account_ids],
                    "manual_notes": (
                        _decode_payload(values.get(
                            columns.get("Manual Notes Payload"), {}))
                        or
                        _raw_text(values.get(columns.get("Manual Notes Cache"), {}))
                        or values.get(columns.get("Manual Notes"), {}).get("text") or ""
                    ),
                    "manual_notes_chars": values.get(
                        columns.get("Manual Notes Chars"), {}).get("text") or "",
                    "manual_notes_sha256": values.get(
                        columns.get("Manual Notes SHA256"), {}).get("text") or "",
                    "slack_note_ids": values.get(columns.get("Slack Note IDs"), {}).get("text") or "",
                    "call_notes_prompt_ts": values.get(
                        columns.get("Call Notes Prompt TS"), {}).get("text") or "",
                    "call_notes_last_ts": values.get(
                        columns.get("Call Notes Last TS"), {}).get("text") or "",
                    "notes_requested_at": values.get(
                        columns.get("Notes Requested At"), {}).get("text") or "",
                    "notes_status": values.get(
                        columns.get("Notes Status"), {}).get("text") or "",
                    "fireflies_id": values.get(
                        columns.get("Fireflies Transcript ID"), {}).get("text") or "",
                    "slack_intake_ts": values.get(
                        columns.get("Slack Intake TS"), {}).get("text") or "",
                    "slack_intake_user": values.get(
                        columns.get("Slack Intake User"), {}).get("text") or "",
                    "prospect_account_id": values.get(
                        columns.get("Prospect Account ID"), {}).get("text") or "",
                    "partner_account_ids": _lines(values.get(
                        columns.get("Partner Account IDs"), {}).get("text") or ""),
                    "contact_ids": _relation_or_lines(
                        values.get(columns.get("External Attendees"), {}),
                        values.get(columns.get("Contact IDs"), {}).get("text") or ""),
                    "contact_email_ids": _json_dict(values.get(
                        columns.get("Contact Email IDs"), {}).get("text") or ""),
                    "deal_ids": _relation_or_lines(
                        values.get(columns.get("Deal"), {}),
                        values.get(columns.get("Resolved Deal ID"), {}).get("text") or ""),
                    "fireflies_summary": values.get(
                        columns.get("Fireflies Summary"), {}).get("text") or "",
                    "reasoning_result": (
                        _json_object(_raw_text(values.get(
                            columns.get("Reasoning Cache"), {})))
                        or _json_object(values.get(
                            columns.get("Reasoning Result"), {}).get("text") or "")
                    ),
                    "processing_started_at": values.get(
                        columns.get("Processing Started At"), {}).get("text") or "",
                    "reasoning_audit": _json_object(_raw_text(values.get(
                        columns.get("Reasoning Audit"), {}))),
                    "reasoning_feedback": _raw_text(values.get(
                        columns.get("Reasoning Feedback"), {})),
                    "reasoning_score": values.get(
                        columns.get("Reasoning Score"), {}).get("text") or "",
                    "feedback_last_ts": values.get(
                        columns.get("Feedback Last TS"), {}).get("text") or "",
                    "reasoning_review_ts": values.get(
                        columns.get("Reasoning Review TS"), {}).get("text") or "",
                    "owner_ids": _people_ids(
                        values.get(columns.get("Owner"), {}).get("value")),
                })
            cursor = page.get("cursor")
            if not cursor:
                break
        account_ids = sorted({item for row in rows for item in row["account_ids"]})
        names = {}
        if account_ids:
            items = self._post("query($ids:[ID!]){items(ids:$ids){id name}}",
                               {"ids": account_ids})["items"]
            names = {str(item["id"]): item["name"] for item in items}
        for row in rows:
            row["account_names"] = [names[item] for item in row["account_ids"] if item in names]
        return rows

    def meetings_for_reasoning_feedback(self) -> list[dict]:
        """Return meetings including completed runs whose Slack feedback stays open."""
        return self.meetings_for_call_notes(include_processed=True)

    def prepare_reasoning_columns(self) -> None:
        self.ensure_text_column("meeting", "Reasoning Cache")
        self.ensure_text_column("meeting", "Processing Started At")

    def begin_meeting_processing(self, meeting_id: str) -> None:
        self.prepare_reasoning_columns()
        self.change_columns("meeting", meeting_id, {
            "Processing Status": "Processing",
            "Processing Started At": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    def save_reasoning_result(self, meeting_id: str, result: dict) -> None:
        self.prepare_reasoning_columns()
        values = {
            "Reasoning Cache": json.dumps(
                result, sort_keys=True, separators=(",", ":")),
        }
        audit = result.get("_audit") if isinstance(result, dict) else None
        if isinstance(audit, dict):
            self.ensure_text_column("meeting", "Reasoning Audit")
            values["Reasoning Audit"] = json.dumps(
                audit, sort_keys=True, separators=(",", ":"))
        self.change_columns("meeting", meeting_id, values)

    def save_call_notes_prompt(self, meeting_id: str, prompt_ts: str,
                               requested_at: str) -> None:
        self.prepare_call_notes_columns()
        self.change_columns("meeting", meeting_id, {
            "Call Notes Prompt TS": prompt_ts,
            "Call Notes Last TS": prompt_ts,
            "Notes Requested At": requested_at,
            "Notes Status": "Awaiting",
        })

    def prepare_call_notes_columns(self) -> None:
        for title in ("Call Notes Prompt TS", "Call Notes Last TS",
                      "Notes Requested At", "Notes Status"):
            self.ensure_text_column("meeting", title)

    def save_call_notes(self, meeting_id: str, notes: str, note_ids: list[str],
                        *, last_ts: str | None = None) -> None:
        self.ensure_column("meeting", "Manual Notes", "long_text")
        self.ensure_text_column("meeting", "Manual Notes Cache")
        self.ensure_text_column("meeting", "Manual Notes Payload")
        self.ensure_text_column("meeting", "Manual Notes Chars")
        self.ensure_text_column("meeting", "Manual Notes SHA256")
        self.ensure_column("meeting", "Slack Note IDs", "text")
        self.ensure_text_column("meeting", "Notes Status")
        values = {
            "Manual Notes": _long_text_preview(notes),
            "Manual Notes Cache": notes,
            "Manual Notes Payload": _encode_payload(notes),
            "Manual Notes Chars": str(len(notes)),
            "Manual Notes SHA256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "Slack Note IDs": "\n".join(note_ids),
            "Notes Status": "Received",
        }
        if last_ts:
            self.ensure_text_column("meeting", "Call Notes Last TS")
            values["Call Notes Last TS"] = last_ts
        self.change_columns("meeting", meeting_id, values)

    def save_reasoning_feedback(self, meeting_id: str, feedback: str,
                                score: str, last_ts: str) -> None:
        for title in ("Reasoning Feedback", "Reasoning Score", "Feedback Last TS"):
            self.ensure_text_column("meeting", title)
        self.change_columns("meeting", meeting_id, {
            "Reasoning Feedback": feedback,
            "Reasoning Score": score,
            "Feedback Last TS": last_ts,
        })

    def save_reasoning_review_ts(self, meeting_id: str, message_ts: str) -> None:
        self.ensure_text_column("meeting", "Reasoning Review TS")
        self.change_columns("meeting", meeting_id, {
            "Reasoning Review TS": message_ts,
        })

    def save_call_notes_cursor(self, meeting_id: str, last_ts: str) -> None:
        self.ensure_text_column("meeting", "Call Notes Last TS")
        self.change_columns("meeting", meeting_id, {
            "Call Notes Last TS": last_ts,
        })

    def save_meeting_attendees(self, meeting_id: str, emails: list[str],
                               names: dict[str, str]) -> None:
        """Persist human-supplied attendees for a background recording."""
        self.ensure_column("meeting", "Attendee Details", "long_text")
        self.change_columns("meeting", meeting_id, {
            "Attendee Emails": "\n".join(dict.fromkeys(emails)),
            "Attendee Details": json.dumps(names, sort_keys=True),
        })

    def scrap_meeting(self, meeting_id: str) -> None:
        """Tombstone a recording without losing its Fireflies dedupe key."""
        self.ensure_text_column("meeting", "Notes Status")
        self.change_columns("meeting", meeting_id, {
            "Processing Status": "Processed",
            "Notes Status": "Scrapped",
        })

    def mark_notes_fallback(self, meeting_id: str) -> None:
        self.ensure_text_column("meeting", "Notes Status")
        self.change_columns("meeting", meeting_id, {
            "Notes Status": "Fallback",
        })

    def column_title(self, board_key: str, *candidates: str,
                     column_type: str | None = None) -> str:
        """Return the first live title matching a semantic column role."""
        columns = self.columns(board_key)
        for title in candidates:
            col = columns.get(title)
            if col and (column_type is None or col["type"] == column_type):
                return title
        wanted = ", ".join(repr(value) for value in candidates)
        raise RuntimeError(f"none of columns {wanted} found on {board_key} board")

    def _format(self, value, column_type: str):
        if value is None:
            return None
        if column_type in ("color", "status"):
            return {"label": str(value)}
        if column_type == "board_relation":
            values = value if isinstance(value, list) else [value]
            return {"item_ids": [int(item_id) for item_id in values if item_id]}
        if column_type == "email":
            return {"email": str(value), "text": str(value)}
        if column_type == "link":
            return {"url": str(value), "text": str(value)}
        if column_type == "dropdown":
            values = value if isinstance(value, list) else [value]
            return {"labels": [str(item) for item in values]}
        if column_type == "date":
            raw = str(value)
            formatted = {"date": raw[:10]}
            if "T" in raw:
                try:
                    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone(ZoneInfo("America/New_York"))
                        formatted["date"] = parsed.date().isoformat()
                    formatted["time"] = parsed.strftime("%H:%M:%S")
                except ValueError:
                    pass
            return formatted
        if column_type == "timeline" and isinstance(value, dict):
            start = str(value.get("from") or "")[:10]
            end = str(value.get("to") or "")[:10]
            return {"from": start, "to": end} if start and end else None
        if column_type == "people":
            values = value if isinstance(value, list) else [value]
            return {"personsAndTeams": [
                {"id": int(item_id), "kind": "person"}
                for item_id in values if item_id
            ]}
        if column_type in ("boolean", "checkbox"):
            return {"checked": "true" if value else "false"}
        return str(value)

    def change_columns(self, board_key: str, item_id: str,
                       values: dict[str, object], *,
                       create_labels_if_missing: bool = True) -> None:
        payload = {}
        for title, value in values.items():
            col = self.column(board_key, title)
            payload[col["id"]] = self._format(value, col["type"])
        if not payload:
            return
        create_labels = "true" if create_labels_if_missing else "false"
        query = ("mutation($board:ID!,$item:ID!,$values:JSON!){"
                 "change_multiple_column_values(board_id:$board,item_id:$item,"
                 f"column_values:$values,create_labels_if_missing:{create_labels})"
                 "{id}}")
        self._post(query, {
            "board": str(BOARDS[board_key]), "item": str(item_id),
            "values": json.dumps(payload),
        })

    def create_item(self, board_key: str, name: str, values: dict[str, object]) -> str:
        payload = {}
        for title, value in values.items():
            col = self.column(board_key, title)
            payload[col["id"]] = self._format(value, col["type"])
        query = ("mutation($board:ID!,$name:String!,$values:JSON!){create_item("
                 "board_id:$board,item_name:$name,column_values:$values,"
                 "create_labels_if_missing:true){id}}")
        data = self._post(query, {
            "board": str(BOARDS[board_key]), "name": name,
            "values": json.dumps(payload),
        })
        return data["create_item"]["id"]

    def _find_by_column(self, board_key: str, title: str, value: str) -> dict | None:
        column_id = self.column(board_key, title)["id"]
        query = ("query($board:ID!,$column:String!,$value:String!){"
                 "items_page_by_column_values(board_id:$board,limit:10,columns:[{"
                 "column_id:$column,column_values:[$value]}]){items{id name}}}")
        items = self._post(query, {
            "board": str(BOARDS[board_key]), "column": column_id, "value": value,
        })["items_page_by_column_values"]["items"]
        return items[0] if items else None

    def find_account_by_domain(self, domain: str) -> dict | None:
        title = self.column_title("account", "Domain (match key)", "Domain")
        value = domain.lower()
        if self.column("account", title)["type"] == "link":
            value = f"https://{value}"
        return self._find_by_column("account", title, value)

    def find_accounts_by_name(self, name: str) -> list[dict]:
        """Return exact normalized Account-name matches."""
        return self._items_named("account", name)

    def create_account(self, domain: str) -> str:
        label = domain.split(".", 1)[0].replace("-", " ").title()
        values: dict[str, object] = {}
        columns = self.columns("account")
        if "Domain (match key)" in columns:
            values["Domain (match key)"] = domain.lower()
        if "Domain" in columns:
            values["Domain"] = f"https://{domain.lower()}"
        if "Relationship Owner" in columns:
            values["Relationship Owner"] = [config.MONDAY_DEFAULT_OWNER_ID]
        if "Type" in columns:
            values["Type"] = "Prospect"
        if "Review Status" in columns:
            values["Review Status"] = "Verified"
        if not values:
            raise RuntimeError("no Domain column found on account board")
        return self.create_item("account", label, values)

    def create_account_from_manual(self, name: str, domain: str = "") -> str:
        """Create a final Account from manual-note identity."""
        values: dict[str, object] = {}
        columns = self.columns("account")
        if domain and "Domain (match key)" in columns:
            values["Domain (match key)"] = domain.lower()
        if domain and "Domain" in columns:
            values["Domain"] = f"https://{domain.lower()}"
        if "Relationship Owner" in columns:
            values["Relationship Owner"] = [config.MONDAY_DEFAULT_OWNER_ID]
        if "Type" in columns:
            values["Type"] = "Prospect"
        if "Review Status" in columns:
            values["Review Status"] = "Verified"
        label = name or domain.split(".", 1)[0].replace("-", " ").title()
        return self.create_item("account", label, values)

    def find_contact_by_email(self, email: str) -> dict | None:
        return self._find_by_column("contact", "Email", email.lower())

    def find_contacts_by_name(self, name: str, account_id: str) -> list[dict]:
        """Return exact Contact-name matches related to the selected Account."""
        relation = self.column_title(
            "contact", "Account", "Accounts", column_type="board_relation")
        column_id = self.column("contact", relation)["id"]
        wanted = _norm_title(name)
        cursor = None
        matches = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){... on BoardRelationValue{"
                     "linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["contact"])], "columns": [column_id],
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                linked = (item["column_values"][0].get("linked_item_ids")
                          if item["column_values"] else []) or []
                if (_norm_title(item["name"]) == wanted and
                        str(account_id) in {str(value) for value in linked}):
                    matches.append({"id": str(item["id"]), "name": item["name"]})
            cursor = page.get("cursor")
            if not cursor:
                return matches

    def create_contact(self, email: str, account_id: str | None = None,
                       name: str | None = None,
                       deal_ids: list[str] | None = None) -> str:
        values: dict[str, object] = {"Email": email.lower()}
        if account_id:
            relation = self.column_title(
                "contact", "Account", "Accounts", column_type="board_relation")
            values[relation] = [account_id]
        if deal_ids and self.optional_column("contact", "Deals"):
            values["Deals"] = [str(value) for value in deal_ids]
        if self.optional_column("contact", "Review Status"):
            values["Review Status"] = "Verified"
        return self.create_item("contact", name or _name_from_email(email), values)

    def link_contacts_to_deal(self, contact_ids: list[str], deal_id: str) -> None:
        """Merge newly discovered Contacts onto one exact Deal relation."""
        additions = [str(value) for value in contact_ids if value]
        if not additions:
            return
        relation = self.column_title(
            "deal", "Contacts", "Contact", column_type="board_relation")
        column_id = self.column("deal", relation)["id"]
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "column_values(ids:$columns){... on BoardRelationValue{"
                 "linked_item_ids}}}}")
        rows = self._post(query, {
            "ids": [str(deal_id)], "columns": [column_id],
        }).get("items") or []
        current = [str(value) for value in (
            ((rows[0].get("column_values") or [{}])[0].get("linked_item_ids")
             if rows else []) or [])]
        merged = list(dict.fromkeys([*current, *additions]))
        if merged != current:
            self.change_columns("deal", str(deal_id), {relation: merged})

    def create_contact_from_manual(self, name: str, account_id: str,
                                   email: str = "") -> str:
        """Create a final Contact from manual-note identity."""
        relation = self.column_title(
            "contact", "Account", "Accounts", column_type="board_relation")
        values: dict[str, object] = {relation: [account_id]}
        if email:
            values["Email"] = email.lower()
        if self.optional_column("contact", "Review Status"):
            values["Review Status"] = "Verified"
        return self.create_item(
            "contact", name or _name_from_email(email), values)

    def _items_named(self, board_key: str, name: str) -> list[dict]:
        wanted = _norm_title(name)
        cursor = None
        matches = []
        while True:
            query = ("query($boards:[ID!],$cursor:String){boards(ids:$boards){"
                     "items_page(limit:100,cursor:$cursor){cursor items{id name}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS[board_key])], "cursor": cursor,
            })["boards"][0]["items_page"]
            matches.extend(
                {"id": str(item["id"]), "name": item["name"]}
                for item in page["items"] if _norm_title(item["name"]) == wanted
            )
            cursor = page.get("cursor")
            if not cursor:
                return matches

    def item_names(self, item_ids: list[str]) -> list[str]:
        ids = [str(value) for value in item_ids if value]
        if not ids:
            return []
        items = self._post("query($ids:[ID!]){items(ids:$ids){id name}}",
                           {"ids": ids})["items"]
        return [item["name"] for item in items]

    def create_deal(self, *, account_id: str, contact_ids: list[str],
                    meeting_id: str, stage: str, name: str,
                    source: str | None = None) -> str:
        values: dict[str, object] = {
            "Stage": stage,
            "Owner": [config.MONDAY_DEFAULT_OWNER_ID],
            "Contacts": contact_ids,
            "link to Meetings": [meeting_id],
        }
        account_relation = self.column_title(
            "deal", "Account", "Accounts", column_type="board_relation")
        values[account_relation] = [account_id]
        # Source means acquisition/origination source, not deal motion. Leave it
        # blank unless an upstream system supplies an evidenced source.
        if source:
            values["Source"] = source
        if self.optional_column("deal", "Review Status"):
            values["Review Status"] = "Verified"
        return self.create_item("deal", name, values)

    def ensure_client_project(self, *, account_id: str, deal_id: str,
                              contact_ids: list[str], name: str) -> str:
        """Return the Deal's project; relation-only repair if automation lagged."""
        # A completed project still owns the Won Deal. Reusing it prevents the
        # lifecycle poller from creating a fresh project after delivery ends.
        existing_row = self.client_project_for_deal(account_id, deal_id)
        existing = str(existing_row["id"]) if existing_row else None
        if existing:
            return existing
        # Native automation 100000015 is the primary creator.  This fallback
        # writes only identity relations; human-managed project fields stay
        # untouched and the board's creator automation may assign Owner.
        values: dict[str, object] = {
            "Deals": [deal_id], "Accounts": [account_id],
        }
        if contact_ids:
            values["Contact"] = [contact_ids[0]]
        return self.create_item("client_project", name, values)

    def won_deals_for_engagements(self) -> list[dict]:
        """Return Won Deals with the relations required for project creation."""
        columns = self.columns("deal")
        account_title = next((title for title in ("Account", "Accounts")
                              if columns.get(title, {}).get("type") ==
                              "board_relation"), None)
        if not account_title:
            return []
        contact_title = next((title for title in ("Contacts", "Contact")
                              if columns.get(title, {}).get("type") ==
                              "board_relation"), None)
        selected = ["Stage", account_title]
        if contact_title:
            selected.append(contact_title)
        ids = {title: self.column("deal", title)["id"] for title in selected}
        cursor = None
        rows = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["deal"])], "columns": list(ids.values()),
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                stage = (values.get(ids["Stage"], {}).get("text") or "").strip()
                if not is_won_deal_stage(stage):
                    continue
                accounts = (values.get(ids[account_title], {}).get("linked_item_ids")
                            or [])
                contacts = (values.get(ids.get(contact_title), {}).get("linked_item_ids")
                            or []) if contact_title else []
                if len(accounts) == 1:
                    rows.append({
                        "id": str(item["id"]), "name": item["name"],
                        "stage": stage, "account_id": str(accounts[0]),
                        "contact_ids": [str(value) for value in contacts],
                    })
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def flag_client_project_needed(self, deal_id: str) -> None:
        """Persist a diagnostic when monday blocks project creation."""
        proposed = self.optional_column("deal", "Proposed Changes")
        if not proposed:
            self.change_columns("deal", deal_id, {"Review Status": "Verified"})
            return
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "column_values(ids:$columns){text}}}")
        items = self._post(query, {
            "ids": [str(deal_id)], "columns": [proposed["id"]],
        })["items"]
        prior = ""
        if items and items[0]["column_values"]:
            prior = (items[0]["column_values"][0].get("text") or "").strip()
        message = ("Client Project creation blocked: the monday API user is a "
                   "viewer on the owner-edit-only Client Projects board.")
        lines = prior.splitlines() if prior else []
        if message not in lines:
            lines.append(message)
        self.change_columns("deal", deal_id, {
            "Proposed Changes": "\n".join(lines),
            "Review Status": "Verified",
        })

    def link_meeting_deal(self, meeting_id: str, deal_id: str) -> None:
        self.ensure_text_column("meeting", "Resolved Deal ID")
        self.change_columns("meeting", meeting_id, {
            "Deal": [deal_id],
            "Resolved Deal ID": deal_id,
        })

    def existing_activity_titles(self, meeting_id: str) -> set[str]:
        source = self.column("activity", "Source Meeting")["id"]
        cursor = None
        titles = set()
        while True:
            query = ("query($boards:[ID!],$column:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "name column_values(ids:$column){... on BoardRelationValue{"
                     "linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["activity"])], "column": [source],
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                linked = (item["column_values"][0].get("linked_item_ids")
                          if item["column_values"] else []) or []
                if str(meeting_id) in {str(value) for value in linked}:
                    titles.add(_norm_title(item["name"]))
            cursor = page.get("cursor")
            if not cursor:
                return titles

    def create_activity(self, *, title: str, task_type: str, due_date: str,
                        owner_ids: list[str], related_ids: list[str],
                        source_meeting_id: str, slack_link: str,
                        notes: str, source_event_key: str = "") -> str:
        self.ensure_column("activity", "Notes", "long_text")
        self.ensure_column("activity", "Slack Link", "link")
        self.ensure_column("activity", "Source Notes Link", "link")
        values: dict[str, object] = {
            "Activity Type": "Task",
            "Status": "Open",
            "Task Type": task_type,
            "Review Status": "Verified",
            "Priority": "Medium",
            "Due Date": due_date,
            "Related item": related_ids,
            "Notes": notes,
        }
        if source_meeting_id:
            values["Source Meeting"] = [source_meeting_id]
        if source_event_key:
            self.ensure_text_column("activity", "Source Event Key")
            values["Source Event Key"] = source_event_key
        if owner_ids:
            values["Owner"] = owner_ids
        if slack_link:
            values["Source Notes Link"] = slack_link
        return self.create_item("activity", title, values)

    def existing_activity_titles_for_event(
            self, source_event_key: str, related_ids: set[str]) -> set[str]:
        column = self.optional_column("activity", "Source Event Key")
        if not column:
            return set()
        related = self.column("activity", "Related item")["id"]
        cursor = None
        titles: set[str] = set()
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["activity"])],
                "columns": [column["id"], related], "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {row["id"]: row for row in item["column_values"]}
                if _raw_text(values.get(column["id"], {})) != source_event_key:
                    continue
                linked = {str(value) for value in values.get(
                    related, {}).get("linked_item_ids") or []}
                if not related_ids or linked.intersection(related_ids):
                    titles.add(_norm_title(item["name"]))
            cursor = page.get("cursor")
            if not cursor:
                return titles

    def complete_activity_from_event(
            self, activity_id: str, *, event_key: str, occurred_at: str,
            source_type: str, evidence: str, confidence: float,
            awaiting_client_reply: bool, source_timeline_id: str,
            expected_title: str = "",
            expected_related_ids: list[str] | None = None) -> bool:
        """Complete only if the exact retrieved Activity is still open/unchanged."""
        self.prepare_phase2_schema()
        status = self.column("activity", "Status")["id"]
        related = self.column("activity", "Related item")["id"]
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){id name "
                 "column_values(ids:$columns){id text "
                 "... on BoardRelationValue{linked_item_ids}}}}")
        items = self._post(query, {
            "ids": [str(activity_id)], "columns": [status, related],
        }).get("items") or []
        if len(items) != 1:
            return False
        values = {row["id"]: row for row in items[0].get("column_values") or []}
        current_status = str(values.get(status, {}).get("text") or "").casefold()
        current_related = {
            str(value) for value in values.get(related, {}).get("linked_item_ids") or []}
        if current_status not in {"open", "working on it", "in progress"}:
            return False
        if expected_title and _norm_title(items[0].get("name") or "") != _norm_title(expected_title):
            return False
        expected = {str(value) for value in expected_related_ids or []}
        if expected and current_related != expected:
            return False
        self.change_columns("activity", activity_id, {
            "Status": "Done",
            "Review Status": "Verified",
            "Completed At": occurred_at,
            "Completion Source Type": source_type,
            "Completion Source Event Key": event_key,
            "Completion Source Timeline ID": source_timeline_id,
            "Completion Evidence": evidence,
            "Completion Confidence": confidence,
            "Awaiting Client Reply": awaiting_client_reply,
        })
        return True

    def stamp_last_interaction(
            self, *, deal_ids: list[str], contact_ids: list[str],
            occurred_at: str, event_key: str, inbound_email_at: str = "") -> None:
        """Monotonically stamp exact event time on the existing date columns."""
        self.prepare_phase2_schema()
        event_time = _as_utc(occurred_at)
        for deal_id in deal_ids:
            titles = ["Last interaction"]
            if inbound_email_at:
                titles.append("Last Inbound Email At")
            columns = {title: self.column("deal", title)["id"] for title in titles}
            query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                     "column_values(ids:$columns){id text value}}}")
            items = self._post(query, {
                "ids": [str(deal_id)], "columns": list(columns.values()),
            }).get("items") or []
            values = {row["id"]: row for row in (
                items[0].get("column_values") if items else []) or []}
            current = _date_time(values.get(columns["Last interaction"], {}))
            changes: dict[str, object] = {}
            if not current or event_time > _as_utc(current, date_at_end=True):
                changes.update({
                    "Last interaction": event_time.isoformat(),
                    "Last Interaction Source Event": event_key,
                })
            if inbound_email_at:
                inbound_time = _as_utc(inbound_email_at)
                inbound = _raw_text(values.get(columns["Last Inbound Email At"], {}))
                if not inbound or inbound_time > _as_utc(inbound, date_at_end=True):
                    changes.update({
                        "Last Inbound Email At": inbound_time.isoformat(),
                        "Last Inbound Source Event": event_key,
                    })
            if changes:
                self.change_columns("deal", deal_id, changes)
        column = self.optional_column("contact", "Last Interaction")
        if contact_ids and column:
            query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){id "
                     "column_values(ids:$columns){id text value}}}")
            items = self._post(query, {
                "ids": [str(value) for value in contact_ids],
                "columns": [column["id"]],
            }).get("items") or []
            for item in items:
                values = item.get("column_values") or []
                current = _date_time(values[0] if values else {})
                if not current or event_time > _as_utc(current, date_at_end=True):
                    self.change_columns("contact", str(item["id"]), {
                        "Last Interaction": event_time.isoformat(),
                    })

    def followup_nudge_candidates(self) -> list[dict]:
        """Return completed email commitments plus their persisted nudge state."""
        required = [
            "Status", "Owner", "Related item", "Completed At",
            "Completion Source Event Key", "Awaiting Client Reply",
            "Follow-up Nudge Slack TS", "Follow-up Nudge Slack Link",
            "Follow-up Nudge Condition Key",
        ]
        if any(not self.optional_column("activity", title) for title in required):
            return []
        columns = {title: self.column("activity", title)["id"] for title in required}
        cursor = None
        rows = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["activity"])],
                "columns": list(columns.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {row["id"]: row for row in item["column_values"]}
                status = values.get(columns["Status"], {}).get("text") or ""
                awaiting_raw = values.get(columns["Awaiting Client Reply"], {}).get("value")
                awaiting = "true" in str(awaiting_raw).casefold()
                completed = _raw_text(values.get(columns["Completed At"], {}))
                if status.casefold() != "done" or not awaiting or not completed:
                    continue
                related = [str(value) for value in values.get(
                    columns["Related item"], {}).get("linked_item_ids") or []]
                rows.append({
                    "id": str(item["id"]), "title": item["name"],
                    "owner": values.get(columns["Owner"], {}).get("text") or "",
                    "owner_ids": _people_ids(values.get(columns["Owner"], {}).get("value")),
                    "related_ids": related,
                    "deal_ids": [value for value in related if value],
                    "completed_at": completed,
                    "completion_event_key": _raw_text(values.get(
                        columns["Completion Source Event Key"], {})),
                    "nudge_ts": _raw_text(values.get(
                        columns["Follow-up Nudge Slack TS"], {})),
                    "nudge_link": _raw_text(values.get(
                        columns["Follow-up Nudge Slack Link"], {})),
                    "nudge_key": _raw_text(values.get(
                        columns["Follow-up Nudge Condition Key"], {})),
                })
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def save_deal_nudge(self, deal_id: str, *, condition_key: str,
                        message_ts: str, permalink: str) -> None:
        self.change_columns("deal", deal_id, {
            "Nudge Condition Key": condition_key,
            "Nudge Slack TS": message_ts,
            "Nudge Slack Link": permalink,
        })

    def clear_deal_nudge(self, deal_id: str) -> None:
        self.change_columns("deal", deal_id, {
            "Nudge Condition Key": "", "Nudge Slack TS": "",
            "Nudge Slack Link": None,
        })

    def save_followup_nudge(self, activity_id: str, *, condition_key: str,
                            message_ts: str, permalink: str) -> None:
        self.change_columns("activity", activity_id, {
            "Follow-up Nudge Condition Key": condition_key,
            "Follow-up Nudge Slack TS": message_ts,
            "Follow-up Nudge Slack Link": permalink,
        })

    def clear_followup_nudge(self, activity_id: str) -> None:
        self.change_columns("activity", activity_id, {
            "Follow-up Nudge Condition Key": "",
            "Follow-up Nudge Slack TS": "",
            "Follow-up Nudge Slack Link": None,
        })

    def prepare_activity_alert_columns(self) -> None:
        self.ensure_text_column("activity", "Slack Notification TS")
        self.ensure_text_column("activity", "Slack Last TS")
        self.ensure_column("activity", "Slack Link", "link")

    def activities_for_alerts(self) -> list[dict]:
        """Return open tasks plus completed tasks whose Slack post needs cleanup."""
        self.prepare_activity_alert_columns()
        titles = [
            "Owner", "Activity Type", "Status", "Task Type", "Review Status",
            "Due Date", "Related item", "Slack Link", "Slack Notification TS",
            "Slack Last TS", "Source Meeting",
        ]
        if self.optional_column("activity", "Source Event Key"):
            titles.append("Source Event Key")
        columns = {title: self.column("activity", title)["id"] for title in titles}
        cursor = None
        rows = []
        related_ids = set()
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["activity"])], "columns": list(columns.values()),
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                if (values.get(columns["Activity Type"], {}).get("text") or "") != "Task":
                    continue
                status = values.get(columns["Status"], {}).get("text") or ""
                notification_ts = values.get(
                    columns["Slack Notification TS"], {}).get("text") or ""
                if status == "Done" and not notification_ts:
                    continue
                source_ids = (values.get(
                    columns["Source Meeting"], {}).get("linked_item_ids") or [])
                source_event_key = (_raw_text(values.get(
                    columns.get("Source Event Key"), {}))
                    if columns.get("Source Event Key") else "")
                # Only the meeting-processing runtime owns this notification
                # loop plus Phase 2 email-created tasks. Leave manually created
                # and legacy monday tasks alone.
                if not source_ids and not source_event_key:
                    continue
                linked = values.get(columns["Related item"], {}).get("linked_item_ids") or []
                related_ids.update(str(value) for value in linked)
                rows.append({
                    "id": str(item["id"]), "title": item["name"],
                    "owner": values.get(columns["Owner"], {}).get("text") or "",
                    "owner_ids": _people_ids(values.get(columns["Owner"], {}).get("value")),
                    "status": status,
                    "task_type": values.get(columns["Task Type"], {}).get("text") or "",
                    "review_status": values.get(columns["Review Status"], {}).get("text") or "",
                    "due_date": values.get(columns["Due Date"], {}).get("text") or "",
                    "related_ids": [str(value) for value in linked],
                    "source_meeting_ids": [str(value) for value in source_ids],
                    "source_event_key": source_event_key,
                    "slack_link": values.get(columns["Slack Link"], {}).get("text") or "",
                    "slack_notification_ts": notification_ts,
                    "slack_last_ts": values.get(
                        columns["Slack Last TS"], {}).get("text") or "",
                    "url": (f"https://harboradvisors.monday.com/boards/"
                            f"{BOARDS['activity']}/pulses/{item['id']}"),
                })
            cursor = page.get("cursor")
            if not cursor:
                break
        meeting_accounts: dict[str, list[str]] = {}
        all_source_meetings = {
            value for row in rows for value in row["source_meeting_ids"]}
        if all_source_meetings:
            account_column = self.column("meeting", "Account")["id"]
            query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){id "
                     "column_values(ids:$columns){... on BoardRelationValue{"
                     "linked_item_ids}}}}")
            # monday's items(ids:) resolver can silently omit records from large
            # lists. Batch lookups so an account near the end of a busy board is
            # not mislabeled as the generic "CRM"/"Needs account" fallback.
            for meeting_batch in _chunks(sorted(all_source_meetings), 20):
                for item in self._post(query, {
                        "ids": meeting_batch,
                        "columns": [account_column]})["items"]:
                    linked = (item["column_values"][0].get("linked_item_ids")
                              if item["column_values"] else []) or []
                    meeting_accounts[str(item["id"])] = [str(value) for value in linked]
                    related_ids.update(str(value) for value in linked)
        names = {}
        account_ids = set()
        if related_ids:
            items = []
            for related_batch in _chunks(sorted(related_ids), 20):
                items.extend(self._post(
                    "query($ids:[ID!]){items(ids:$ids){id name board{id}}}",
                    {"ids": related_batch})["items"])
            names = {str(item["id"]): item["name"] for item in items}
            account_ids = {
                str(item["id"]) for item in items
                if str((item.get("board") or {}).get("id") or "") ==
                str(BOARDS["account"])
            }
        for row in rows:
            row["related_names"] = [names[value] for value in row["related_ids"]
                                    if value in names]
            source_account_ids = [
                account_id for meeting_id in row["source_meeting_ids"]
                for account_id in meeting_accounts.get(meeting_id, [])
            ]
            row["company_name"] = next(
                (names[value] for value in [*row["related_ids"], *source_account_ids]
                 if value in account_ids),
                row["related_names"][0] if row["related_names"] else "Needs account",
            )
        return rows

    def ensure_activity_owner(self, activity_id: str,
                              owner_ids: list[str]) -> list[str]:
        """Guarantee an engine-created Activity always has an accountable owner."""
        resolved = [str(value) for value in owner_ids if value]
        if resolved:
            return resolved
        resolved = [str(config.MONDAY_DEFAULT_OWNER_ID)]
        self.change_columns("activity", activity_id, {"Owner": resolved})
        return resolved

    def save_activity_alert(self, activity_id: str, message_ts: str,
                            permalink: str) -> None:
        self.prepare_activity_alert_columns()
        self.change_columns("activity", activity_id, {
            "Slack Notification TS": message_ts,
            "Slack Last TS": message_ts,
            "Slack Link": permalink,
        })

    def save_activity_alert_cursor(self, activity_id: str, last_ts: str) -> None:
        self.prepare_activity_alert_columns()
        self.change_columns("activity", activity_id, {"Slack Last TS": last_ts})

    def complete_activity_from_slack(self, activity_id: str, last_ts: str) -> None:
        self.prepare_activity_alert_columns()
        self.change_columns("activity", activity_id, {
            "Status": "Done", "Review Status": "Verified", "Slack Last TS": last_ts,
        })

    def clear_activity_alert(self, activity_id: str) -> None:
        """Clear the retry cursor only after the bot post is confirmed deleted."""
        self.prepare_activity_alert_columns()
        self.change_columns("activity", activity_id, {
            "Slack Notification TS": "",
            "Slack Last TS": "",
            "Slack Link": None,
        })

    def set_meeting_owner(self, meeting_id: str, owner_id: str) -> None:
        self.change_columns("meeting", meeting_id, {"Owner": [owner_id]})

    def existing_fireflies_ids(self) -> set[str]:
        column = self.optional_column("meeting", "Fireflies Transcript ID")
        if not column:
            return set()
        cursor = None
        values: set[str] = set()
        while True:
            query = ("query($boards:[ID!],$column:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "column_values(ids:$column){text}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["meeting"])], "column": [column["id"]],
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                for value in item["column_values"]:
                    if (value.get("text") or "").strip():
                        values.add(value["text"].strip())
            cursor = page.get("cursor")
            if not cursor:
                return values

    def existing_slack_intake_ids(self) -> set[str]:
        """All manual Slack source timestamps, including processed Meetings."""
        column = self.optional_column("meeting", "Slack Intake TS")
        if not column:
            return set()
        cursor = None
        values: set[str] = set()
        while True:
            query = ("query($boards:[ID!],$column:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "column_values(ids:$column){text}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["meeting"])], "column": [column["id"]],
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                for value in item["column_values"]:
                    if (value.get("text") or "").strip():
                        values.add(value["text"].strip())
            cursor = page.get("cursor")
            if not cursor:
                return values

    def existing_fireflies_rows(self) -> list[dict]:
        """All monday Meeting signatures used to dedupe later team copies."""
        ff = self.optional_column("meeting", "Fireflies Transcript ID")
        if not ff:
            return []
        date = self.column("meeting", "Date")
        invited = self.column("meeting", "Attendee Emails")
        column_ids = [ff["id"], date["id"], invited["id"]]
        cursor = None
        rows = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "name column_values(ids:$columns){id text}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["meeting"])], "columns": column_ids,
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value.get("text") or ""
                          for value in item["column_values"]}
                if not values.get(ff["id"], "").strip():
                    continue
                rows.append({
                    "url": values[ff["id"]].strip(),
                    "title": item["name"],
                    "date": values.get(date["id"], ""),
                    "invited": _lines(values.get(invited["id"], "")),
                })
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def create_meeting_from_fireflies(self, transcript: dict, *, date: str,
                                      attendee_emails: list[str]) -> str:
        self.ensure_text_column("meeting", "Fireflies Transcript ID")
        self.ensure_column("meeting", "Fireflies Summary", "long_text")
        self.ensure_column("meeting", "Attendee Details", "long_text")
        from .fireflies import attendee_details, fireflies_summary
        values: dict[str, object] = {
            "Fireflies Transcript ID": str(transcript["id"]),
            "Date": date,
            "Attendee Emails": "\n".join(attendee_emails),
            "Attendee Details": json.dumps(
                attendee_details(transcript), sort_keys=True),
            "Processing Status": "Awaiting",
            "Fireflies Summary": fireflies_summary(transcript),
        }
        owner_id = USER_IDS_BY_EMAIL.get(
            str(transcript.get("organizer_email") or "").strip().lower())
        if owner_id:
            values["Owner"] = [owner_id]
        if transcript.get("transcript_url"):
            values["Transcript"] = transcript["transcript_url"]
        return self.create_item(
            "meeting", transcript.get("title") or "Untitled Fireflies meeting", values)

    def create_manual_meeting(self, *, title: str, date: str, notes: str,
                              slack_ts: str, slack_user_id: str,
                              requested_at: str) -> str:
        """Persist a Slack note as a first-class Meeting before model work."""
        self.prepare_call_notes_columns()
        self.ensure_column("meeting", "Manual Notes", "long_text")
        self.ensure_text_column("meeting", "Manual Notes Cache")
        self.ensure_text_column("meeting", "Manual Notes Payload")
        self.ensure_text_column("meeting", "Manual Notes Chars")
        self.ensure_text_column("meeting", "Manual Notes SHA256")
        self.ensure_column("meeting", "Slack Note IDs", "text")
        self.ensure_text_column("meeting", "Slack Intake TS")
        self.ensure_text_column("meeting", "Slack Intake User")
        return self.create_item("meeting", title, {
            "Date": date,
            "Processing Status": "Ready",
            "Manual Notes": _long_text_preview(notes),
            "Manual Notes Cache": notes,
            "Manual Notes Payload": _encode_payload(notes),
            "Manual Notes Chars": str(len(notes)),
            "Manual Notes SHA256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "Slack Note IDs": slack_ts,
            "Call Notes Prompt TS": slack_ts,
            "Call Notes Last TS": slack_ts,
            "Notes Requested At": requested_at,
            "Notes Status": "Received",
            "Slack Intake TS": slack_ts,
            "Slack Intake User": slack_user_id,
            "Owner": [config.MONDAY_DEFAULT_OWNER_ID],
        })

    def save_manual_meeting_context(
            self, meeting_id: str, *, account_id: str,
            contact_ids: list[str], contact_email_ids: dict[str, str],
            deal_id: str | None) -> None:
        """Link manual identity while preserving the Processing claim."""
        for title in ("Prospect Account ID", "Partner Account IDs",
                      "Contact IDs", "Resolved Deal ID"):
            self.ensure_text_column("meeting", title)
        self.ensure_column("meeting", "Contact Email IDs", "long_text")
        values: dict[str, object] = {
            "Internal/External": "External",
            "Account": [account_id],
            "External Attendees": contact_ids,
            "Prospect Account ID": account_id,
            "Partner Account IDs": "",
            "Contact IDs": "\n".join(contact_ids),
            "Contact Email IDs": json.dumps(contact_email_ids, sort_keys=True),
            "Resolved Deal ID": deal_id or "",
        }
        if deal_id:
            values["Deal"] = [deal_id]
        self.change_columns("meeting", meeting_id, values)

    def related_item_ids(self, board_key: str, item_id: str, title: str) -> list[str]:
        column_id = self.column(board_key, title)["id"]
        query = ("query($ids:[ID!],$column:[String!]){items(ids:$ids){"
                 "column_values(ids:$column){... on BoardRelationValue{linked_item_ids}}}}")
        items = self._post(query, {"ids": [str(item_id)], "column": [column_id]})["items"]
        if not items or not items[0]["column_values"]:
            return []
        return [str(value) for value in
                (items[0]["column_values"][0].get("linked_item_ids") or [])]

    def deals_for_account(self, account_id: str, *, include_closed: bool = True
                          ) -> list[dict]:
        """Return Deals for an Account, optionally excluding closed stages.

        monday's native Deals board mirrors Account through Contacts, so reads
        must use MirrorValue.display_value; generic column ``text`` is empty for
        this field in the current API.
        """
        deal_columns = self.columns("deal")
        account_title = next((title for title in ("Account", "Accounts")
                              if deal_columns.get(title, {}).get("type") in
                              ("board_relation", "mirror")), None)
        if not account_title:
            return []
        account_col = self.column("deal", account_title)
        stage_col = self.column("deal", "Stage")
        owner_col = self.optional_column("deal", "Owner")
        column_ids = [account_col["id"], stage_col["id"]]
        if owner_col:
            column_ids.append(owner_col["id"])
        account_name = self.item_name(account_id).strip().casefold()
        cursor = None
        matches = []
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text value "
                     "... on MirrorValue{display_value} "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["deal"])], "columns": column_ids,
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                account_value = values.get(account_col["id"], {})
                if account_col["type"] == "board_relation":
                    belongs = str(account_id) in {
                        str(value) for value in account_value.get("linked_item_ids") or []}
                else:
                    display = account_value.get("display_value") or ""
                    belongs = account_name in {
                        part.strip().casefold() for part in display.split(",") if part.strip()}
                if not belongs:
                    continue
                stage = (values.get(stage_col["id"], {}).get("text") or "").strip()
                if not include_closed and is_closed_deal_stage(stage):
                    continue
                owner_ids = (_people_ids(values.get(owner_col["id"], {}).get("value"))
                             if owner_col else [])
                matches.append({
                    "id": str(item["id"]), "name": item["name"],
                    "stage": stage, "owner_ids": owner_ids,
                })
            cursor = page.get("cursor")
            if not cursor:
                return matches

    def active_deals_for_account(self, account_id: str) -> list[dict]:
        """Return every non-closed Deal for an Account."""
        return self.deals_for_account(account_id, include_closed=False)

    def preferred_deal_for_account(self, account_id: str) -> str | None:
        """Return the one Deal that ongoing work should use, without guessing.

        An open Deal wins. If none exists, an Active Client or an Account with
        an active Client Project reuses its single Won Deal. This prevents a
        transcript that merely *sounds* introductory from inventing a new
        Discovery Deal for an existing client.
        """
        active = self.active_deals_for_account(account_id)
        if len(active) > 1:
            raise RuntimeError(
                f"account {account_id} has multiple active Deals; refusing to guess")
        if active:
            return str(active[0]["id"])
        all_deals = self.deals_for_account(account_id)
        won = [row for row in all_deals
               if is_won_deal_stage(row.get("stage"))]
        projects = self.client_projects_for_account(account_id)
        ongoing = (is_active_client_type(self.account_type(account_id))
                   or any(row.get("active") for row in projects))
        if not ongoing:
            return None
        if len(won) > 1:
            raise RuntimeError(
                f"account {account_id} has multiple Won Deals; refusing to guess")
        return str(won[0]["id"]) if won else None

    def find_deal_for_account(self, account_id: str) -> str | None:
        """Return the Deal only when exactly one active Deal exists."""
        deals = self.active_deals_for_account(account_id)
        return deals[0]["id"] if len(deals) == 1 else None

    def owner_ids_for_context(self, *, account_id: str | None,
                              deal_id: str | None,
                              meeting_owner_ids: list[str] | None = None) -> list[str]:
        """Resolve the single accountability owner: Deal, then Account, then Meeting."""
        lookups = []
        if deal_id:
            lookups.append(("deal", deal_id, "Owner"))
        if account_id:
            lookups.append(("account", account_id, "Relationship Owner"))
        for board_key, item_id, title in lookups:
            column = self.optional_column(board_key, title)
            if not column:
                continue
            query = "query($ids:[ID!],$cols:[String!]){items(ids:$ids){column_values(ids:$cols){value}}}"
            items = self._post(query, {
                "ids": [str(item_id)], "cols": [column["id"]],
            })["items"]
            if items and items[0]["column_values"]:
                owners = _people_ids(items[0]["column_values"][0].get("value"))
                if owners:
                    return owners
        if meeting_owner_ids:
            return [str(value) for value in meeting_owner_ids if value]
        return [config.MONDAY_DEFAULT_OWNER_ID]

    def item_name(self, item_id: str) -> str:
        query = "query($ids:[ID!]){items(ids:$ids){name}}"
        items = self._post(query, {"ids": [str(item_id)]})["items"]
        return items[0]["name"] if items else ""

    def account_type(self, account_id: str) -> str:
        column = self.optional_column("account", "Type")
        if not column:
            return ""
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
                 "column_values(ids:$columns){text}}}")
        items = self._post(query, {
            "ids": [str(account_id)], "columns": [column["id"]],
        })["items"]
        if not items or not items[0]["column_values"]:
            return ""
        return (items[0]["column_values"][0].get("text") or "").strip()

    def set_account_type(self, account_id: str, account_type: str) -> None:
        """Apply an allowlisted, human-supplied Account lifecycle classification."""
        if account_type != AI_INVESTMENT_TYPE:
            raise ValueError(f"unsupported deterministic Account Type: {account_type}")
        self.ensure_status_label(
            "account", "Type", account_type, color="navy")
        self.change_columns("account", account_id, {"Type": account_type})

    def set_account_active_client(self, account_id: str) -> None:
        if self.optional_column("account", "Type"):
            self.change_columns(
                "account", account_id, {"Type": ACTIVE_CLIENT_TYPE},
                create_labels_if_missing=False)

    def set_account_completed_past_client(self, account_id: str) -> None:
        if self.optional_column("account", "Type"):
            self.change_columns(
                "account", account_id,
                {"Type": COMPLETED_PAST_CLIENT_TYPE},
                create_labels_if_missing=False)

    def client_projects_for_account(self, account_id: str) -> list[dict]:
        """Return Client Projects linked to an Account with lifecycle context."""
        columns = self.columns("client_project")
        account_title = next((title for title in ("Accounts", "Account")
                              if columns.get(title, {}).get("type") ==
                              "board_relation"), None)
        if not account_title:
            return []
        deal_title = next((title for title in ("Deals", "Deal")
                           if columns.get(title, {}).get("type") ==
                           "board_relation"), None)
        status_title = next((title for title in ("Status", "Project Status")
                             if title in columns), None)
        selected = [account_title]
        if deal_title:
            selected.append(deal_title)
        if status_title:
            selected.append(status_title)
        ids = {title: self.column("client_project", title)["id"] for title in selected}
        cursor = None
        rows = []
        inactive = {"done", "completed", "inactive", "cancelled", "canceled"}
        while True:
            query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                     "id name column_values(ids:$columns){id text "
                     "... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS["client_project"])],
                "columns": list(ids.values()), "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                accounts = values.get(ids[account_title], {}).get("linked_item_ids") or []
                if str(account_id) not in {str(value) for value in accounts}:
                    continue
                status = (values.get(ids.get(status_title), {}).get("text") or "")
                deal_ids = (values.get(ids.get(deal_title), {}).get("linked_item_ids")
                            or []) if deal_title else []
                rows.append({
                    "id": str(item["id"]), "name": item["name"], "status": status,
                    "active": status.strip().casefold() not in inactive,
                    "deal_ids": [str(value) for value in deal_ids],
                })
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def active_client_project_for_deal(self, account_id: str,
                                       deal_id: str) -> dict | None:
        return next((row for row in self.client_projects_for_account(account_id)
                     if row.get("active") and str(deal_id) in row.get("deal_ids", [])),
                    None)

    def client_project_for_deal(self, account_id: str,
                                deal_id: str) -> dict | None:
        """Return the Deal's project regardless of delivery lifecycle state."""
        return next((row for row in self.client_projects_for_account(account_id)
                     if str(deal_id) in row.get("deal_ids", [])), None)

    def reasoning_context(self, account_id: str,
                          contact_ids: list[str]) -> dict:
        """Compact authoritative CRM snapshot supplied before model listening."""
        account = self._selected_item_context(
            "account", account_id,
            ["Type", "Engagement Type", "Industry", "Notes"])
        contacts = [self._selected_item_context(
            "contact", contact_id, ["Email", "Role / Title", "Notes"])
            for contact_id in contact_ids]
        return {
            "account": account,
            "contacts": [row for row in contacts if row],
            "deals": self.deals_for_account(account_id),
            "client_projects": self.client_projects_for_account(account_id),
        }

    def _selected_item_context(self, board_key: str, item_id: str,
                               titles: list[str]) -> dict:
        selected = [title for title in titles if self.optional_column(board_key, title)]
        columns = {title: self.column(board_key, title)["id"] for title in selected}
        query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){id name "
                 "column_values(ids:$columns){id text}}}")
        items = self._post(query, {
            "ids": [str(item_id)], "columns": list(columns.values()),
        })["items"]
        if not items:
            return {}
        values = {value["id"]: value.get("text") or ""
                  for value in items[0]["column_values"]}
        return {
            "id": str(items[0]["id"]), "name": items[0]["name"],
            **{title: values.get(column_id, "")
               for title, column_id in columns.items()},
        }

    def _find_item_with_text(self, board_key: str, title: str,
                             expected: str) -> str | None:
        if not expected:
            return None
        column_id = self.column(board_key, title)["id"]
        cursor = None
        while True:
            query = ("query($boards:[ID!],$column:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{id "
                     "column_values(ids:$column){text}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS[board_key])], "column": [column_id], "cursor": cursor,
            })["boards"][0]["items_page"]
            wanted = expected.strip().casefold()
            for item in page["items"]:
                texts = [value.get("text") or "" for value in item["column_values"]]
                names = {part.strip().casefold() for text in texts
                         for part in text.split(",") if part.strip()}
                if wanted in names:
                    return str(item["id"])
            cursor = page.get("cursor")
            if not cursor:
                return None

    def _find_item_with_relation(self, board_key: str, title: str,
                                 target_ids: set[str]) -> str | None:
        column_id = self.column(board_key, title)["id"]
        cursor = None
        while True:
            query = ("query($boards:[ID!],$column:[String!],$cursor:String){"
                     "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{id "
                     "column_values(ids:$column){... on BoardRelationValue{linked_item_ids}}}}}}")
            page = self._post(query, {
                "boards": [str(BOARDS[board_key])], "column": [column_id], "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                linked = item["column_values"][0].get("linked_item_ids") or []
                if target_ids.intersection(str(value) for value in linked):
                    return str(item["id"])
            cursor = page.get("cursor")
            if not cursor:
                return None

    def set_meeting_gated(self, meeting_id: str, meeting_type: str) -> None:
        self.change_columns("meeting", meeting_id, {
            "Internal/External": meeting_type,
            "Processing Status": "Gated",
        })

    def set_meeting_ready(self, meeting_id: str, account_ids: list[str],
                          contact_ids: list[str], deal_id: str | None,
                          meeting_type: str = "External",
                          contact_email_ids: dict[str, str] | None = None) -> None:
        values: dict[str, object] = {
            "Internal/External": meeting_type,
            "Processing Status": "Ready",
            "Account": account_ids,
            "External Attendees": contact_ids,
        }
        if deal_id:
            values["Deal"] = [deal_id]
        for title in ("Prospect Account ID", "Partner Account IDs",
                      "Contact IDs", "Resolved Deal ID"):
            self.ensure_text_column("meeting", title)
        self.ensure_column("meeting", "Contact Email IDs", "long_text")
        values.update({
            "Prospect Account ID": account_ids[0] if account_ids else "",
            "Partner Account IDs": "\n".join(account_ids[1:]),
            "Contact IDs": "\n".join(contact_ids),
            "Contact Email IDs": json.dumps(contact_email_ids or {}, sort_keys=True),
            "Resolved Deal ID": deal_id or "",
        })
        self.change_columns("meeting", meeting_id, values)


def _name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    words = local.replace(".", " ").replace("_", " ").replace("-", " ").split()
    return " ".join(word.title() for word in words) or email


def _lines(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").replace(",", "\n").splitlines()
            if part.strip()]


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _relation_or_lines(relation: dict, fallback: str) -> list[str]:
    linked = relation.get("linked_item_ids") or []
    return [str(value) for value in linked] or _lines(fallback)


def _people_ids(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    people = value.get("personsAndTeams") if isinstance(value, dict) else []
    return [str(person.get("id")) for person in people or []
            if person.get("kind") == "person" and person.get("id")]


def _norm_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _long_text_preview(value: str, limit: int = 1_950) -> str:
    """Return a visibly truncated preview that always fits monday long-text."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit - 31].rstrip() + "\n\n[Full notes stored in cache]"


def _json_dict(value: str) -> dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return ({str(key).lower(): str(item) for key, item in parsed.items()}
                if isinstance(parsed, dict) else {})
    except json.JSONDecodeError:
        return {}


def _json_object(value: str) -> dict:
    """Read structured JSON without flattening nested sections to strings."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _raw_text(column_value: dict) -> str:
    """Return the exact stored string instead of monday's rendered text.

    monday's ``text`` representation strips Slack-style ``<url|label>`` links
    from ordinary text columns. The JSON ``value`` retains the original bytes
    and is therefore the only safe source for integrity checks and model input.
    """
    raw = (column_value or {}).get("value")
    if raw not in (None, ""):
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            decoded = raw
        if isinstance(decoded, str):
            return decoded
    return str((column_value or {}).get("text") or "")


def _link_url(column_value: dict) -> str:
    raw = (column_value or {}).get("value")
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        decoded = {}
    if isinstance(decoded, dict) and decoded.get("url"):
        return str(decoded["url"]).strip()
    text = str((column_value or {}).get("text") or "").strip()
    return text if text.startswith("https://") else ""


def _date_time(column_value: dict) -> str:
    """Preserve monday Date time when present; keep date-only values explicit."""
    raw = (column_value or {}).get("value")
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        value = {}
    if isinstance(value, dict) and value.get("date"):
        date = str(value["date"])[:10]
        clock = str(value.get("time") or "").strip()
        if clock:
            local = dt.datetime.fromisoformat(f"{date}T{clock}").replace(
                tzinfo=ZoneInfo("America/New_York"))
            return local.astimezone(dt.timezone.utc).isoformat()
        return date
    return str((column_value or {}).get("text") or "").strip()


def _as_utc(value: str, *, date_at_end: bool = False) -> dt.datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("timestamp is blank")
    if len(raw) == 10:
        clock = dt.time.max if date_at_end else dt.time.min
        parsed = dt.datetime.combine(
            dt.date.fromisoformat(raw), clock,
            tzinfo=ZoneInfo("America/New_York"),
        )
    else:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(dt.timezone.utc)


def _encode_payload(value: str) -> str:
    return base64.b64encode(str(value or "").encode("utf-8")).decode("ascii")


def _decode_payload(column_value: dict) -> str:
    encoded = _raw_text(column_value).strip()
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _status_color_name(value) -> str:
    """Normalize monday's numeric status colors to GraphQL enum names."""
    if isinstance(value, str) and not value.isdigit():
        return value
    try:
        return STATUS_COLORS[int(value)]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"unknown monday status color: {value!r}") from error


def _claim_is_stale(value: str, minutes: int, *,
                    now: dt.datetime | None = None) -> bool:
    if not value:
        return True
    try:
        started = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone.utc) - started.astimezone(
        dt.timezone.utc) >= dt.timedelta(minutes=minutes)
