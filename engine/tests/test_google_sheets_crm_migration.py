"""Deterministic policy tests for the Google Sheets CRM migration."""
from __future__ import annotations

import csv
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migrations.import_google_sheets_crm import (  # noqa: E402
    Migration,
    LegacyRow,
    account_base_name,
    compatible_stage,
    desired_stage,
    load_rows,
    migration_note,
    strict_provisional_value,
)
import migrations.import_google_sheets_crm as migration_module  # noqa: E402


def row(**overrides) -> LegacyRow:
    values = {
        "row_number": 2,
        "company": "Example Co",
        "source": "Referral",
        "project": "",
        "time_text": "",
        "status": "New",
        "fees": "$50,000",
        "line_item": "",
        "billing": "",
        "notes": "",
        "occurrence": 1,
    }
    values.update(overrides)
    return LegacyRow(**values)


def test_strict_provisional_value_accepts_only_one_bare_amount():
    assert strict_provisional_value("$50,000") == 50_000
    assert strict_provisional_value("15000") == 15_000
    assert strict_provisional_value("$7,500.50") == 7_500.50
    assert strict_provisional_value("6000/month") is None
    assert strict_provisional_value("220k / year") is None
    assert strict_provisional_value("7500 + $100 / capture") is None
    assert strict_provisional_value("big?") is None


def test_status_policy_preserves_advanced_open_stages():
    assert desired_stage(row(status="Ongoing"), "Discovery") == "Won"
    assert desired_stage(row(status="Completed"), "") == "Won"
    assert desired_stage(row(status="Closed lost"), "Proposal") == "Lost"
    assert desired_stage(row(status="New"), "Proposal") == "Proposal"
    assert desired_stage(row(status="New"), "Lost") == "New"
    assert desired_stage(row(status="Paused"), "Discovery") is None


def test_deal_reuse_never_reopens_closed_records_for_new_work():
    assert compatible_stage(row(status="New"), "Discovery")[0] > 0
    assert compatible_stage(row(status="New"), "")[0] > 0
    assert compatible_stage(row(status="New"), "Won") == (0, 0)
    assert compatible_stage(row(status="New"), "Lost") == (0, 0)
    assert compatible_stage(row(status="Ongoing"), "Won")[0] > 0
    assert compatible_stage(row(status="Closed lost"), "Lost")[0] > 0


def test_account_aliases_and_phase_suffixes():
    assert account_base_name("Quillr / Bluejay") == "Quillr"
    assert account_base_name("Arc Logistics Phase 2") == "Arc"
    assert account_base_name("Keystone Phase 3") == "Keystone Site Services"
    assert account_base_name("ClinicMD") == "Clinic MD"
    assert account_base_name("Ledgerline") == "Ledgerline Software"


def test_migration_note_excludes_raw_fee_and_collected_revenue():
    source = row(
        fees="$50,000", project="Implementation", line_item="Services",
        billing="On signature", notes="Follow up",
    )
    note = migration_note(source, provisional_value=50_000)
    assert source.marker in note
    assert "$50,000" not in note
    assert "2025/2026 collected revenue was not migrated" in note
    assert "Mercury" in note
    assert "provisional estimate" in note


def test_load_rows_uses_stable_occurrence_key_for_duplicate_rows():
    headers = [
        "Company Name", "Source", "Project", "Time", "Status", "Fees",
        "Line item", "Billing", "2025", "2026", "Notes",
    ]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "crm.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerow({"Company Name": "Ledgerline", "Project": "Market Study",
                             "Status": "Completed", "Fees": "$50,000"})
            writer.writerow({"Company Name": "Ledgerline", "Project": "Market Study",
                             "Status": "Completed", "Fees": "$9,000"})
        rows, physical = load_rows(path)
    assert physical == 2
    assert [item.occurrence for item in rows] == [1, 2]
    assert rows[0].migration_key != rows[1].migration_key
    assert "50000" not in rows[0].migration_key


def test_complexity_rejection_retries_only_after_provider_delay():
    attempts = []
    delays = []

    def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError(
                'monday mutation outcome is unknown: '
                'COMPLEXITY_BUDGET_EXHAUSTED "retry_in_seconds":2')
        return "ok"

    original_sleep = migration_module.time.sleep
    migration_module.time.sleep = delays.append
    try:
        assert Migration._with_complexity_retry(operation) == "ok"
    finally:
        migration_module.time.sleep = original_sleep
    assert len(attempts) == 2
    assert delays == [4]


def test_unknown_mutation_outcome_is_never_retried():
    attempts = []

    def operation():
        attempts.append(1)
        raise RuntimeError("monday mutation outcome is unknown: connection reset")

    try:
        Migration._with_complexity_retry(operation)
    except RuntimeError as error:
        assert "connection reset" in str(error)
    else:
        raise AssertionError("unknown mutation outcome must fail closed")
    assert len(attempts) == 1


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"{len(tests)}/{len(tests)} tests passed")
