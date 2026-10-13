"""Deterministic monday schema normalization and drift reporting."""
from __future__ import annotations

import json
from pathlib import Path

from .lifecycle import ACCOUNT_TYPE_LABELS, DEAL_STAGE_LABELS


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = ROOT / "docs" / "live-monday-schema.json"


def normalized_schema(snapshot: dict) -> dict:
    """Return stable behavior-relevant metadata, excluding item counts/noise."""
    boards = []
    for board in snapshot.get("boards") or []:
        columns = []
        for column in board.get("columns") or []:
            settings = column.get("settings") or {}
            labels = []
            if column.get("type") in {"status", "dropdown"}:
                labels = sorted({
                    str(row.get("label") or "").strip()
                    for row in settings.get("labels") or []
                    if str(row.get("label") or "").strip()
                    and not row.get("is_deactivated")
                }, key=str.casefold)
            columns.append({
                "id": str(column.get("id") or ""),
                "title": str(column.get("title") or ""),
                "type": str(column.get("type") or ""),
                "target_board_ids": sorted(
                    str(value) for value in column.get("target_board_ids") or []),
                "labels": labels,
            })
        boards.append({
            "id": str(board.get("id") or ""),
            "name": str(board.get("name") or ""),
            "groups": sorted(
                ({"id": str(row.get("id") or ""),
                  "title": str(row.get("title") or "")}
                 for row in board.get("groups") or []),
                key=lambda row: (row["id"], row["title"])),
            "columns": sorted(columns, key=lambda row: row["id"]),
        })
    return {"boards": sorted(boards, key=lambda row: row["id"])}


def schema_fingerprint(snapshot: dict) -> str:
    import hashlib
    rendered = json.dumps(
        normalized_schema(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode()).hexdigest()


def schema_drift(expected: dict, live: dict) -> list[str]:
    """Return compact semantic differences without exposing item data."""
    left = _paths(normalized_schema(expected))
    right = _paths(normalized_schema(live))
    differences = []
    for path in sorted(set(left) | set(right)):
        if path not in right:
            differences.append(f"removed: {path}={left[path]!r}")
        elif path not in left:
            differences.append(f"added: {path}={right[path]!r}")
        elif left[path] != right[path]:
            differences.append(
                f"changed: {path}: {left[path]!r} -> {right[path]!r}")
    return differences


def verify_lifecycle_labels(snapshot: dict) -> list[str]:
    problems = []
    expected = {
        ("Deals", "Stage"): set(DEAL_STAGE_LABELS),
        ("Accounts", "Type"): set(ACCOUNT_TYPE_LABELS),
    }
    tolerated_legacy = {
        ("Deals", "Stage"): {"Completed"},
    }
    for (board_name, column_title), wanted in expected.items():
        column = _column(snapshot, board_name, column_title)
        if not column:
            problems.append(f"missing {board_name}.{column_title}")
            continue
        labels = {
            str(row.get("label") or "").strip()
            for row in (column.get("settings") or {}).get("labels") or []
            if str(row.get("label") or "").strip()
            and not row.get("is_deactivated")
        }
        extra = labels - wanted
        missing = wanted - labels
        if missing or extra - tolerated_legacy.get((board_name, column_title), set()):
            problems.append(
                f"{board_name}.{column_title} labels: "
                f"expected {sorted(wanted)}, got {sorted(labels)}")
    return problems


def load_snapshot(path: Path = DEFAULT_SNAPSHOT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _column(snapshot: dict, board_name: str, title: str) -> dict | None:
    for board in snapshot.get("boards") or []:
        if board.get("name") != board_name:
            continue
        return next((column for column in board.get("columns") or []
                     if column.get("title") == title), None)
    return None


def _paths(value: object, prefix: str = "schema") -> dict[str, object]:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result.update(_paths(item, f"{prefix}.{key}"))
        return result
    if isinstance(value, list):
        result = {}
        stable_key = next((
            key for key in ("id", "label")
            if value
            and all(isinstance(item, dict) and key in item for item in value)
            and len({str(item[key]) for item in value}) == len(value)
        ), None)
        if stable_key:
            for item in value:
                marker = str(item[stable_key])
                result.update(_paths(
                    item, f"{prefix}[{stable_key}={marker!r}]"))
            return result
        for index, item in enumerate(value):
            result.update(_paths(item, f"{prefix}[{index}]"))
        return result
    return {prefix: value}
