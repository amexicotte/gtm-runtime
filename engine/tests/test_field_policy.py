"""The field dictionary must cover the complete Phase 0 live schema."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.field_policy import validate_field_dictionary  # noqa: E402


def test_field_dictionary_covers_every_snapshot_column_and_managed_addition():
    result = validate_field_dictionary()
    assert result == {
        "valid": True,
        "errors": [],
        "snapshot_columns": 203,
        "managed_additions": 14,
        "dictionary_entries": 217,
    }


if __name__ == "__main__":
    test_field_dictionary_covers_every_snapshot_column_and_managed_addition()
    print("1/1 tests passed")
