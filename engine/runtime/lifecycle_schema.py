"""Idempotent monday migration for the approved CRM lifecycle labels."""
from __future__ import annotations

import json

from .lifecycle import COMPLETED_PAST_CLIENT_TYPE
from .monday import BOARDS, STATUS_COLORS


ACCOUNT_OLD_LABEL = "Past Client"
DEAL_REMOVED_LABEL = "Completed"


def migrate_lifecycle_schema(crm, *, apply: bool = False) -> dict:
    """Plan or apply the approved status-label migration.

    The Deal label is removed only when no Deal uses it. Account status IDs are
    preserved, so existing Past Client records are renamed in place.
    """
    account = _status_column(crm, "account", "Type")
    deal = _status_column(crm, "deal", "Stage")
    completed_deals = _items_with_label(crm, "deal", deal["id"], DEAL_REMOVED_LABEL)
    if completed_deals:
        raise RuntimeError(
            "cannot remove Deals.Completed while records use it: " +
            ", ".join(row["id"] for row in completed_deals))

    account_labels, account_changed = _rename_label(
        account["labels"], ACCOUNT_OLD_LABEL, COMPLETED_PAST_CLIENT_TYPE)
    deal_labels, deal_changed = _remove_label(deal["labels"], DEAL_REMOVED_LABEL)
    result = {
        "mode": "apply" if apply else "plan",
        "account_label_renamed": account_changed,
        "deal_label_removed": deal_changed,
        "completed_deals": 0,
    }
    if not apply:
        return result
    if account_changed:
        _update_status_column(crm, "account", account, account_labels)
    if deal_changed:
        _update_status_column(crm, "deal", deal, deal_labels)
    return result


def _status_column(crm, board_key: str, title: str) -> dict:
    column_id = crm.column(board_key, title)["id"]
    query = ("query($boards:[ID!],$columns:[String!]){boards(ids:$boards){"
             "columns(ids:$columns){id revision settings}}}")
    rows = crm._post(query, {
        "boards": [str(BOARDS[board_key])], "columns": [column_id],
    }).get("boards") or []
    if not rows or not rows[0].get("columns"):
        raise RuntimeError(f"missing {board_key}.{title} status column")
    column = rows[0]["columns"][0]
    settings = column.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    return {
        "id": str(column["id"]), "revision": str(column["revision"]),
        "labels": list(settings.get("labels") or []),
    }


def _items_with_label(crm, board_key: str, column_id: str,
                      label: str) -> list[dict]:
    query = ("query($boards:[ID!],$columns:[String!],$cursor:String){"
             "boards(ids:$boards){items_page(limit:100,cursor:$cursor){"
             "cursor items{id name column_values(ids:$columns){text}}}}}")
    cursor = None
    matches = []
    while True:
        page = crm._post(query, {
            "boards": [str(BOARDS[board_key])], "columns": [column_id],
            "cursor": cursor,
        })["boards"][0]["items_page"]
        for item in page.get("items") or []:
            value = ((item.get("column_values") or [{}])[0].get("text") or "")
            if value.strip().casefold() == label.strip().casefold():
                matches.append({"id": str(item["id"]), "name": item.get("name")})
        cursor = page.get("cursor")
        if not cursor:
            return matches


def _rename_label(labels: list[dict], old: str,
                  new: str) -> tuple[list[dict], bool]:
    old_rows = [row for row in labels if _label(row) == old.casefold()]
    new_rows = [row for row in labels if _label(row) == new.casefold()]
    if old_rows and new_rows:
        raise RuntimeError(f"both {old!r} and {new!r} labels exist")
    if not old_rows:
        if new_rows:
            return labels, False
        raise RuntimeError(f"required Account Type label {old!r} is missing")
    updated = []
    for row in labels:
        item = dict(row)
        if _label(row) == old.casefold():
            item["label"] = new
        updated.append(item)
    return updated, True


def _remove_label(labels: list[dict], label: str) -> tuple[list[dict], bool]:
    updated = [row for row in labels if _label(row) != label.casefold()]
    return updated, len(updated) != len(labels)


def _label(row: dict) -> str:
    return str(row.get("label") or "").strip().casefold()


def _update_status_column(crm, board_key: str, column: dict,
                          labels: list[dict]) -> None:
    allowed = ("id", "label", "color", "index", "description",
               "is_deactivated", "is_done")
    prepared = []
    for row in labels:
        item = {key: row[key] for key in allowed if row.get(key) is not None}
        color = item.get("color")
        if isinstance(color, int) or str(color).isdigit():
            item["color"] = STATUS_COLORS[int(color)]
        prepared.append(item)
    mutation = ("mutation($board:ID!,$column:String!,$revision:String!,"
                "$settings:UpdateStatusColumnSettingsInput!){"
                "update_status_column(board_id:$board,id:$column,"
                "revision:$revision,settings:$settings){id revision settings}}")
    crm._post(mutation, {
        "board": str(BOARDS[board_key]), "column": column["id"],
        "revision": column["revision"], "settings": {"labels": prepared},
    })

