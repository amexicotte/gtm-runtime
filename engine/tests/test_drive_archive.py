"""Exact-folder, upload-only signed-contract archive tests."""
import json
import hashlib
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.drive_archive import (  # noqa: E402
    SignedContractArchive, archive_filename, folder_id_from_url,
)


FOLDER = "folder_1000000018"
URL = f"https://drive.google.com/drive/folders/{FOLDER}"


class Runner:
    def __init__(self, rows=None): self.rows = rows or []; self.calls = []
    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        stdout = json.dumps(self.rows) if args[1] == "lsjson" else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def writer(runner):
    with patch("runtime.drive_archive._rclone_config_scope", return_value="drive"):
        return SignedContractArchive(remote="writer", runner=runner)


def test_archive_roots_every_operation_to_exact_account_folder_and_uploads_once():
    runner = Runner()
    result = writer(runner).upload(
        b"%PDF-contract", folder_url=URL, account_name="Acme / Inc.",
        signed_date="2026-08-23", envelope_id="env-100000019")
    assert result.uploaded is True
    assert [call[0][1] for call in runner.calls] == ["lsjson", "rcat"]
    assert all(call[0][-2:] == ["--drive-root-folder-id", FOLDER]
               or "--drive-root-folder-id" in call[0] for call in runner.calls)
    assert runner.calls[1][1]["input"] == b"%PDF-contract"
    assert "/" not in result.name


def test_existing_same_size_contract_is_deduplicated_without_upload():
    name = archive_filename(
        "Acme", signed_date="2026-08-23", envelope_id="env-123")
    runner = Runner([{
        "Name": name, "Size": len(b"%PDF-contract"),
        "Hashes": {"MD5": hashlib.md5(
            b"%PDF-contract", usedforsecurity=False).hexdigest()},
    }])
    result = writer(runner).upload(
        b"%PDF-contract", folder_url=URL, account_name="Acme",
        signed_date="2026-08-23", envelope_id="env-123")
    assert result.uploaded is False
    assert len(runner.calls) == 1


def test_folder_id_must_come_from_a_drive_folder_url():
    assert folder_id_from_url(URL) == FOLDER
    try:
        folder_id_from_url("https://drive.google.com/drive/my-drive")
    except ValueError:
        pass
    else:
        raise AssertionError("missing folder ID must fail closed")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
