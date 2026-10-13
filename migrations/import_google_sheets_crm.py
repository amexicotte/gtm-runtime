#!/usr/bin/env python3
"""Deterministic Google Sheets CSV -> monday CRM migration.

The importer treats CSV cells as data, never instructions.  It is dry-run by
default, reconciles against live monday records before writing, and preserves a
stable marker in Deal Notes so an identical row is idempotent on retry.

Commercial policy for this one-time migration is deliberately narrow:

* ``Fees`` is not copied into final commercial fields.
* 2025/2026 collected-revenue columns are not copied; Mercury is deferred.
* An open Deal receives a provisional Deal Value only when Fees contains one
  bare numeric amount (for example ``$50,000``), with no cadence or qualifier.
* Ongoing clients are marked Won/Active Client and receive one open Client
  Project. Completed historical Deals are Won, but any project created by the
  native Won automation is closed rather than left active.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from runtime.config import config  # noqa: E402
from runtime.discovery import load_env_file  # noqa: E402
from runtime.monday import BOARDS, Monday  # noqa: E402


REQUIRED_HEADERS = {
    "Company Name", "Source", "Project", "Time", "Status", "Fees",
    "Line item", "Billing", "2025", "2026", "Notes",
}
DEAL_STATUSES = {"ongoing", "new", "completed", "closed lost", "paused"}
OPEN_STAGES = {"new", "discovery", "proposal", "negotiation"}
SOURCE_MAP = {
    "inbound": "Inbound",
    "referral": "Referral",
    "network": "Network",
    "expansion": "Expansion",
    "outbound": "Outbound",
    "partner": "Referral",
    "granite peak capital": "Referral",
    "expert network": "Referral",
    "chisel": "Referral",
    "taylor kim": "Referral",
}
ACCOUNT_ALIASES = {
    "quillr / bluejay": "Quillr",
    "northpeak": "Northpeak Health",
    "arc logistics": "Arc",
    "arc logistics phase 2": "Arc",
    "keystone phase 2": "Keystone Site Services",
    "keystone phase 3": "Keystone Site Services",
    "clinicmd": "Clinic MD",
    "ledgerline": "Ledgerline Software",
}
DEAL_ALIASES = {
    "quillr / bluejay": "Quillr",
    "northpeak": "Northpeak Health",
}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def account_base_name(company: str) -> str:
    alias = ACCOUNT_ALIASES.get(clean(company).casefold())
    if alias:
        return alias
    return re.sub(r"\s+phase\s+\d+\s*$", "", clean(company), flags=re.I).strip()


def strict_provisional_value(raw: str) -> float | None:
    """Accept one bare USD amount and reject cadence/range/qualified text."""
    value = clean(raw)
    if not re.fullmatch(r"\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", value):
        return None
    number = float(value.replace("$", "").replace(",", "").strip())
    return number if 0 < number <= 100_000_000 else None


@dataclass(frozen=True)
class LegacyRow:
    row_number: int
    company: str
    source: str
    project: str
    time_text: str
    status: str
    fees: str
    line_item: str
    billing: str
    notes: str
    occurrence: int

    @property
    def status_key(self) -> str:
        return self.status.casefold()

    @property
    def migration_key(self) -> str:
        canonical = "|".join((
            norm(self.company), norm(self.project), self.status_key,
            str(self.occurrence),
        ))
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"google-sheets-crm:{digest}"

    @property
    def marker(self) -> str:
        return f"Legacy migration key: {self.migration_key}"


def load_rows(path: Path) -> tuple[list[LegacyRow], int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_HEADERS - headers)
        if missing:
            raise ValueError(f"CSV is missing required headers: {missing}")
        physical = list(reader)
    occurrences: dict[tuple[str, str, str], int] = {}
    rows = []
    for row_number, raw in enumerate(physical, start=2):
        company = clean(raw.get("Company Name"))
        if not company:
            continue
        status = clean(raw.get("Status"))
        identity = (norm(company), norm(raw.get("Project") or ""), status.casefold())
        occurrences[identity] = occurrences.get(identity, 0) + 1
        rows.append(LegacyRow(
            row_number=row_number,
            company=company,
            source=clean(raw.get("Source")),
            project=clean(raw.get("Project")),
            time_text=clean(raw.get("Time")),
            status=status,
            fees=clean(raw.get("Fees")),
            line_item=clean(raw.get("Line item")),
            billing=clean(raw.get("Billing")),
            notes=clean(raw.get("Notes")),
            occurrence=occurrences[identity],
        ))
    return rows, len(physical)


def migration_note(row: LegacyRow, *, provisional_value: float | None) -> str:
    lines = [row.marker, f"Legacy status: {row.status or 'Unclassified'}"]
    for label, value in (
        ("Legacy project", row.project),
        ("Legacy source", row.source),
        ("Legacy line item", row.line_item),
        ("Legacy billing", row.billing),
        ("Legacy time", row.time_text),
        ("Legacy note", row.notes),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if provisional_value is not None:
        lines.append(
            "Deal Value is a provisional estimate migrated from one unqualified "
            "numeric sheet value; replace it with the best current estimate."
        )
    lines.extend((
        "Legacy Fees was not migrated into final commercial fields; signed "
        "DocuSign terms are authoritative.",
        "Legacy 2025/2026 collected revenue was not migrated; Mercury is the "
        "deferred source of truth.",
    ))
    return "\n".join(lines)


def desired_stage(row: LegacyRow, existing_stage: str = "") -> str | None:
    status = row.status_key
    if status in {"ongoing", "completed"}:
        return "Won"
    if status == "closed lost":
        return "Lost"
    if status == "new":
        return existing_stage if existing_stage.casefold() in OPEN_STAGES else "New"
    return None


def compatible_stage(row: LegacyRow, stage: str) -> tuple[int, int]:
    """Return a sortable compatibility score; zero means do not reuse."""
    current = clean(stage).casefold()
    if row.status_key == "new":
        if current in OPEN_STAGES:
            return (3, 1)
        return (2, 1) if not current else (0, 0)
    if row.status_key == "ongoing":
        if current == "won":
            return (4, 1)
        if current in OPEN_STAGES:
            return (3, 1)
        if not current:
            return (2, 1)
        return (0, 0)
    if row.status_key == "completed":
        if current == "won":
            return (4, 1)
        if current in OPEN_STAGES or not current:
            return (2, 1)
        return (0, 0)
    if row.status_key == "closed lost":
        if current == "lost":
            return (4, 1)
        if current in OPEN_STAGES or not current:
            return (2, 1)
        return (0, 0)
    if row.status_key == "paused":
        return (2, 1) if not current or current in OPEN_STAGES else (0, 0)
    return (0, 0)


def _append_note(existing: str, block: str) -> str:
    existing = str(existing or "").strip()
    return block if not existing else f"{existing}\n\n{block}"


class Migration:
    def __init__(self, crm: Monday, rows: list[LegacyRow], *, apply: bool):
        self.crm = crm
        self.rows = rows
        self.apply = apply
        self.accounts = self._read_board(
            "account", ["Type", "Source", "Notes", "Deals", "Client Projects"])
        self.deals = self._read_board(
            "deal", ["Stage", "Account", "Deal Value", "Source", "Notes", "Client Project"])
        self.projects = self._read_board(
            "client_project", ["Status", "Accounts", "Deals"])
        self.used_deals: set[str] = set()
        self.actions: list[dict[str, Any]] = []
        self.ambiguities: list[dict[str, Any]] = []
        self._virtual = 0

    def _read_board(self, key: str, titles: list[str]) -> list[dict[str, Any]]:
        columns = {title: self.crm.column(key, title)["id"] for title in titles}
        cursor = None
        rows = []
        while True:
            query = (
                "query($boards:[ID!],$columns:[String!],$cursor:String){"
                "boards(ids:$boards){items_page(limit:100,cursor:$cursor){cursor items{"
                "id name group{id title} column_values(ids:$columns){id text value "
                "... on BoardRelationValue{linked_item_ids}}}}}}"
            )
            page = self.crm._post(query, {
                "boards": [str(BOARDS[key])], "columns": list(columns.values()),
                "cursor": cursor,
            })["boards"][0]["items_page"]
            for item in page["items"]:
                values = {value["id"]: value for value in item["column_values"]}
                row: dict[str, Any] = {
                    "id": str(item["id"]), "name": item["name"],
                    "group": (item.get("group") or {}).get("title", ""),
                }
                for title, column_id in columns.items():
                    value = values.get(column_id, {})
                    row[title] = clean(value.get("text"))
                    row[f"{title}__ids"] = [
                        str(linked) for linked in value.get("linked_item_ids") or []]
                rows.append(row)
            cursor = page.get("cursor")
            if not cursor:
                return rows

    def _new_virtual_id(self, prefix: str) -> str:
        self._virtual += 1
        return f"{prefix}:{self._virtual}"

    def _deal_candidates(self, row: LegacyRow) -> list[dict[str, Any]]:
        marker = [deal for deal in self.deals if row.marker in deal.get("Notes", "")]
        if marker:
            return marker
        names = {norm(row.company)}
        alias = DEAL_ALIASES.get(row.company.casefold())
        if alias:
            names.add(norm(alias))
        exact = [deal for deal in self.deals if norm(deal["name"]) in names]
        scored = []
        for deal in exact:
            score = compatible_stage(row, deal.get("Stage", ""))
            if score[0] and deal["id"] not in self.used_deals:
                scored.append((score, int(deal["id"]) if deal["id"].isdigit() else 0, deal))
        return [row[-1] for row in sorted(scored, reverse=True)]

    def _resolve_deal(self, row: LegacyRow) -> dict[str, Any] | None:
        candidates = self._deal_candidates(row)
        if not candidates:
            return None
        best_score = compatible_stage(row, candidates[0].get("Stage", ""))[0]
        best = [deal for deal in candidates
                if compatible_stage(row, deal.get("Stage", ""))[0] == best_score]
        # The newest compatible record is the active canonical record. This
        # handles the known duplicate Plotbox and ClinicMD histories.
        selected = max(best, key=lambda deal: int(deal["id"]))
        self.used_deals.add(selected["id"])
        return selected

    def _account_candidates(self, row: LegacyRow, deal: dict[str, Any] | None
                            ) -> list[dict[str, Any]]:
        if deal and len(deal.get("Account__ids", [])) == 1:
            account_id = deal["Account__ids"][0]
            return [account for account in self.accounts if account["id"] == account_id]
        desired = norm(account_base_name(row.company))
        exact = [account for account in self.accounts if norm(account["name"]) == desired]
        if exact:
            return exact
        source = norm(row.company)
        prefix = [account for account in self.accounts
                  if len(norm(account["name"])) >= 5 and source.startswith(norm(account["name"]))]
        if not prefix:
            return []
        longest = max(len(norm(account["name"])) for account in prefix)
        return [account for account in prefix if len(norm(account["name"])) == longest]

    def _account_type(self, row: LegacyRow, existing: str) -> str | None:
        current = clean(existing)
        if row.status_key == "ongoing":
            return "Active Client"
        if row.status_key == "new" and current in {"", "Past Client", "Prospect"}:
            return "Prospect"
        if row.status_key == "completed" and not current:
            return "Past Client"
        if row.status_key == "referral partner" and not current:
            return "Partner"
        if not current and row.status_key in {"closed lost", "paused"}:
            return "Prospect"
        return None

    def _ensure_account(self, row: LegacyRow, deal: dict[str, Any] | None
                        ) -> dict[str, Any] | None:
        candidates = self._account_candidates(row, deal)
        if len(candidates) > 1:
            self.ambiguities.append({
                "row": row.row_number, "company": row.company,
                "kind": "multiple_accounts",
                "candidates": [{"id": x["id"], "name": x["name"]} for x in candidates],
            })
            return None
        source = SOURCE_MAP.get(row.source.casefold())
        if candidates:
            account = candidates[0]
            values: dict[str, object] = {}
            account_type = self._account_type(row, account.get("Type", ""))
            if account_type and account_type != account.get("Type"):
                values["Type"] = account_type
            if source and not account.get("Source"):
                values["Source"] = source
            if values:
                self._update("account", account, values, row)
            return account
        if row.status_key not in DEAL_STATUSES | {"referral partner"}:
            return None
        values = {
            "Relationship Owner": [config.MONDAY_DEFAULT_OWNER_ID],
            "Review Status": "Verified",
        }
        account_type = self._account_type(row, "")
        if account_type:
            values["Type"] = account_type
        if source:
            values["Source"] = source
        name = account_base_name(row.company)
        account_id = self._create("account", name, values, row)
        account = {
            "id": account_id, "name": name, "Type": account_type or "",
            "Source": source or "", "Notes": "", "Deals__ids": [],
            "Client Projects__ids": [],
        }
        self.accounts.append(account)
        return account

    def _deal_name(self, row: LegacyRow) -> str:
        if row.project:
            candidate = f"{row.company} — {row.project}"
        else:
            candidate = row.company
        if any(norm(deal["name"]) == norm(candidate) for deal in self.deals):
            return f"{candidate} — legacy {row.migration_key.rsplit(':', 1)[-1][:8]}"
        return candidate

    def _ensure_deal(self, row: LegacyRow, account: dict[str, Any],
                     existing: dict[str, Any] | None) -> dict[str, Any]:
        provisional = (strict_provisional_value(row.fees)
                       if row.status_key == "new" else None)
        note = migration_note(row, provisional_value=provisional)
        stage = desired_stage(row, existing.get("Stage", "") if existing else "")
        source = SOURCE_MAP.get(row.source.casefold())
        if existing:
            values: dict[str, object] = {}
            if stage and stage != existing.get("Stage"):
                values["Stage"] = stage
            account_ids = existing.get("Account__ids", [])
            if not account_ids:
                values["Account"] = [account["id"]]
            elif account["id"] not in account_ids:
                self.ambiguities.append({
                    "row": row.row_number, "company": row.company,
                    "kind": "deal_account_conflict", "deal_id": existing["id"],
                    "deal_account_ids": account_ids, "selected_account_id": account["id"],
                })
                return existing
            if source and not existing.get("Source"):
                values["Source"] = source
            if row.marker not in existing.get("Notes", ""):
                values["Notes"] = _append_note(existing.get("Notes", ""), note)
            if provisional is not None and not existing.get("Deal Value"):
                values["Deal Value"] = provisional
            if values:
                self._update("deal", existing, values, row)
            existing.update({key: clean(value) if key != "Account" else value
                             for key, value in values.items() if key != "Notes"})
            if "Notes" in values:
                existing["Notes"] = str(values["Notes"])
            if "Account" in values:
                existing["Account__ids"] = [account["id"]]
            return existing
        values: dict[str, object] = {
            "Owner": [config.MONDAY_DEFAULT_OWNER_ID],
            "Account": [account["id"]],
            "Review Status": "Verified",
            "Notes": note,
        }
        if stage:
            values["Stage"] = stage
        if source:
            values["Source"] = source
        if provisional is not None:
            values["Deal Value"] = provisional
        name = self._deal_name(row)
        deal_id = self._create("deal", name, values, row)
        deal = {
            "id": deal_id, "name": name, "Stage": stage or "",
            "Account__ids": [account["id"]], "Deal Value": provisional or "",
            "Source": source or "", "Notes": note, "Client Project__ids": [],
        }
        self.deals.append(deal)
        self.used_deals.add(deal_id)
        return deal

    def _create(self, board: str, name: str, values: dict[str, object],
                row: LegacyRow) -> str:
        if self.apply:
            item_id = self._with_complexity_retry(
                lambda: self.crm.create_item(board, name, values))
            time.sleep(0.8)
        else:
            item_id = self._new_virtual_id(board)
        self.actions.append({
            "action": "create", "board": board, "item_id": item_id,
            "name": name, "row": row.row_number,
            "fields": sorted(values),
        })
        return item_id

    def _update(self, board: str, item: dict[str, Any],
                values: dict[str, object], row: LegacyRow) -> None:
        if self.apply:
            self._with_complexity_retry(lambda: self.crm.change_columns(
                board, item["id"], values, create_labels_if_missing=False))
            time.sleep(0.8)
        self.actions.append({
            "action": "update", "board": board, "item_id": item["id"],
            "name": item["name"], "row": row.row_number,
            "fields": sorted(values),
        })

    @staticmethod
    def _with_complexity_retry(operation):
        """Retry only monday's explicit pre-execution complexity rejection."""
        for attempt in range(8):
            try:
                return operation()
            except RuntimeError as error:
                message = str(error)
                if "COMPLEXITY_BUDGET_EXHAUSTED" not in message or attempt == 7:
                    raise
                match = re.search(r'"retry_in_seconds":\s*(\d+)', message)
                delay = int(match.group(1)) if match else 5
                time.sleep(min(max(delay + 2, 4), 30))

    def _projects_for_deal(self, deal_id: str) -> list[dict[str, Any]]:
        return [project for project in self.projects
                if deal_id in project.get("Deals__ids", [])]

    def _reconcile_project(self, row: LegacyRow, account: dict[str, Any],
                           deal: dict[str, Any]) -> None:
        if row.status_key not in {"ongoing", "completed"}:
            return
        matches = self._projects_for_deal(deal["id"])
        if len(matches) > 1:
            self.ambiguities.append({
                "row": row.row_number, "company": row.company,
                "kind": "multiple_client_projects", "deal_id": deal["id"],
                "project_ids": [item["id"] for item in matches],
            })
            return
        desired = "Working on it" if row.status_key == "ongoing" else "Done"
        if matches:
            project = matches[0]
            values: dict[str, object] = {}
            if account["id"] not in project.get("Accounts__ids", []):
                values["Accounts"] = [account["id"]]
            if project.get("Status") != desired:
                values["Status"] = desired
            if values:
                self._update("client_project", project, values, row)
            return
        # Historical completed Deals do not need a new delivery record. If the
        # native Won automation creates one, the post-apply refresh closes it.
        if row.status_key == "completed":
            self.actions.append({
                "action": "project_not_required", "board": "client_project",
                "deal_id": deal["id"], "row": row.row_number,
            })
            return
        project_id = self._create(
            "client_project", deal["name"], {
                "Deals": [deal["id"]], "Accounts": [account["id"]],
                "Status": "Working on it",
            }, row)
        self.projects.append({
            "id": project_id, "name": deal["name"], "Status": "Working on it",
            "Deals__ids": [deal["id"]], "Accounts__ids": [account["id"]],
        })

    def run(self) -> dict[str, Any]:
        deal_rows = 0
        skipped = []
        resolved: list[tuple[LegacyRow, dict[str, Any], dict[str, Any]]] = []
        for row in self.rows:
            existing_deal = self._resolve_deal(row) if row.status_key in DEAL_STATUSES else None
            account = self._ensure_account(row, existing_deal)
            if row.status_key not in DEAL_STATUSES:
                skipped.append({
                    "row": row.row_number, "company": row.company,
                    "status": row.status or "Unclassified",
                    "reason": "not_a_deal_status",
                    "account_preserved": bool(account),
                })
                continue
            if not account:
                continue
            deal_rows += 1
            deal = self._ensure_deal(row, account, existing_deal)
            resolved.append((row, account, deal))

        if self.ambiguities and self.apply:
            raise RuntimeError(
                "live migration encountered ambiguity after preflight; rerun dry-run"
            )
        if self.apply and any(row.status_key in {"ongoing", "completed"}
                              for row, _, _ in resolved):
            # Give the existing Won -> Client Project automation a bounded chance
            # to create its record, then repair relations/status without racing it.
            time.sleep(8)
            self.projects = self._read_board(
                "client_project", ["Status", "Accounts", "Deals"])
        for row, account, deal in resolved:
            self._reconcile_project(row, account, deal)

        return {
            "mode": "apply" if self.apply else "dry-run",
            "source_company_rows": len(self.rows),
            "deal_rows": deal_rows,
            "skipped_rows": skipped,
            "ambiguities": self.ambiguities,
            "actions": self.actions,
            "counts": {
                key: sum(1 for action in self.actions if action["action"] == key)
                for key in ("create", "update", "project_not_required")
            },
            "commercial_policy": {
                "fees": "ignored except a bare numeric estimate on open Deals",
                "ongoing_commercials": "DocuSign structured fields only",
                "collected_revenue": "not imported; Mercury deferred",
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--env-file", type=Path, default=ENGINE / ".env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-company-row-count", type=int,
        help="required with --apply; must equal the parsed non-empty company-row count",
    )
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows, physical_rows = load_rows(args.csv_path)
    load_env_file(args.env_file)
    if args.apply:
        if args.confirm_company_row_count != len(rows):
            print(
                "refusing live migration: --confirm-company-row-count must equal "
                f"{len(rows)}", file=sys.stderr,
            )
            return 2
        if os.environ.get("BUSINESS_LISTENING_WRITE_KILL_SWITCH", "").strip().casefold() in {
                "1", "true", "yes", "on"}:
            print("refusing live migration: business-listening write kill switch is active",
                  file=sys.stderr)
            return 2
        config.require_business_writes()
    try:
        result = Migration(Monday(), rows, apply=args.apply).run()
    except Exception as error:
        print(f"CRM migration failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    result["source_file"] = args.csv_path.name
    result["physical_rows"] = physical_rows
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not result["ambiguities"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
