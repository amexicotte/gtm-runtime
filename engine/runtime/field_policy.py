"""Validation for the complete monday field dictionary."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .schema_contract import verify_lifecycle_labels


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIELDS = ROOT / "reasoning" / "config" / "fields.yaml"
DEFAULT_SNAPSHOT = ROOT / "docs" / "live-monday-schema.json"
BOARD_KEYS = {
    "Meetings": "meeting",
    "Client Projects": "client_project",
    "Deals": "deal",
    "Accounts": "account",
    "Contacts": "contact",
    "Activities": "activity",
}
ALLOWED_POLICIES = {
    "overwrite", "merge", "explicit_only", "bounded", "relation", "never",
}


def validate_field_dictionary(
        fields_path: Path = DEFAULT_FIELDS,
        snapshot_path: Path = DEFAULT_SNAPSHOT) -> dict:
    fields = yaml.safe_load(fields_path.read_text())
    snapshot = json.loads(snapshot_path.read_text())
    errors: list[str] = []
    snapshot_columns = 0
    managed_additions = 0

    for board in snapshot.get("boards") or []:
        board_name = str(board.get("name") or "")
        board_key = BOARD_KEYS.get(board_name)
        if not board_key:
            errors.append(f"unmapped snapshot board: {board_name}")
            continue
        dictionary = fields.get(board_key)
        if not isinstance(dictionary, dict):
            errors.append(f"missing dictionary object: {board_key}")
            continue
        by_title: dict[str, list[tuple[str, dict]]] = {}
        for semantic_key, spec in dictionary.items():
            if isinstance(spec, dict):
                by_title.setdefault(str(spec.get("column") or ""), []).append(
                    (semantic_key, spec))
        snapshot_titles = set()
        for column in board.get("columns") or []:
            snapshot_columns += 1
            title = str(column.get("title") or "")
            snapshot_titles.add(title)
            matches = by_title.get(title) or []
            if len(matches) != 1:
                errors.append(
                    f"{board_key}.{title}: expected one dictionary entry, "
                    f"found {len(matches)}")
                continue
            semantic_key, spec = matches[0]
            # Managed additions are provisioned by title/type and monday may
            # assign an opaque ID even when creation requested a stable one.
            # Once present, live title/type plus the schema fingerprint are the
            # authoritative contract; unmanaged baseline IDs remain exact.
            if (not spec.get("managed_addition") and
                    spec.get("id") != column.get("id")):
                errors.append(
                    f"{board_key}.{semantic_key}: id {spec.get('id')!r} != "
                    f"snapshot {column.get('id')!r}")
            if spec.get("type") != column.get("type"):
                errors.append(
                    f"{board_key}.{semantic_key}: type {spec.get('type')!r} != "
                    f"snapshot {column.get('type')!r}")
        for semantic_key, spec in dictionary.items():
            if not isinstance(spec, dict):
                errors.append(f"{board_key}.{semantic_key}: spec must be an object")
                continue
            _validate_spec(board_key, semantic_key, spec, errors)
            if spec.get("column") not in snapshot_titles:
                if spec.get("managed_addition"):
                    managed_additions += 1
                else:
                    errors.append(
                        f"{board_key}.{semantic_key}: column is absent from snapshot "
                        "without managed_addition")

    agent_events = fields.get("agent_event") or {}
    for semantic_key, spec in agent_events.items():
        _validate_spec("agent_event", semantic_key, spec, errors)
        if spec.get("managed_addition"):
            managed_additions += 1
    for semantic_key, spec in (fields.get("client_project") or {}).items():
        if spec.get("policy") != "never":
            errors.append(
                f"client_project.{semantic_key}: structured fields must be human-managed")
    stage = (fields.get("deal") or {}).get("stage") or {}
    allowed_stages = set(stage.get("allowed_values") or [])
    if "Won" in allowed_stages:
        errors.append("deal.stage: Won must remain prohibited")
    if not {"New", "Discovery", "Proposal", "Negotiation", "Lost"}.issubset(
            allowed_stages):
        errors.append("deal.stage: open stages plus explicit Lost must be represented")
    errors.extend(verify_lifecycle_labels(snapshot))

    return {
        "valid": not errors,
        "errors": errors,
        "snapshot_columns": snapshot_columns,
        "managed_additions": managed_additions,
        "dictionary_entries": sum(
            len(value) for key, value in fields.items()
            if key != "_meta" and isinstance(value, dict)),
    }


def _validate_spec(board_key: str, semantic_key: str, spec: dict,
                   errors: list[str]) -> None:
    if spec.get("policy") not in ALLOWED_POLICIES:
        errors.append(
            f"{board_key}.{semantic_key}: unsupported policy {spec.get('policy')!r}")
    if not str(spec.get("owner") or "").strip():
        errors.append(f"{board_key}.{semantic_key}: owner is required")
    if not str(spec.get("column") or "").strip():
        errors.append(f"{board_key}.{semantic_key}: column title is required")
    if spec.get("extract") and spec.get("policy") != "never":
        if spec.get("evidence") != "required":
            errors.append(
                f"{board_key}.{semantic_key}: model-writable fields require evidence")
        if (spec.get("type") in {"status", "dropdown"} and
                not spec.get("allowed_values")):
            errors.append(
                f"{board_key}.{semantic_key}: model-writable enums require allowed_values")


def main() -> int:
    result = validate_field_dictionary()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
