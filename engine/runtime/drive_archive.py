"""Upload-only signed-contract archive into an exact monday Drive folder."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import subprocess

from .config import config
from .discovery import _rclone_config_scope


SAFE_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SAFE_FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")


@dataclass(frozen=True)
class ArchiveResult:
    name: str
    folder_id: str
    drive_url: str
    uploaded: bool


class SignedContractArchive:
    """An rclone writer with list/upload only and a per-call folder-ID root."""

    def __init__(self, *, remote: str | None = None, runner=None):
        self.remote = (remote or config.GOOGLE_DRIVE_WRITER_RCLONE_REMOTE).strip()
        self.runner = runner or subprocess.run
        if not SAFE_REMOTE.fullmatch(self.remote):
            raise RuntimeError("Google Drive writer remote is invalid")
        if _rclone_config_scope(self.remote) != "drive":
            raise RuntimeError(
                "Google Drive writer must use the separately approved drive scope")

    def connectivity(self) -> dict:
        self._run(["rclone", "about", f"{self.remote}:", "--json"], text=True)
        return {"status": "ready", "remote": self.remote,
                "scope": "drive", "operations": ["list", "upload"]}

    def upload(self, pdf_bytes: bytes, *, folder_url: str, account_name: str,
               signed_date: str, envelope_id: str) -> ArchiveResult:
        folder_id = folder_id_from_url(folder_url)
        filename = archive_filename(
            account_name, signed_date=signed_date, envelope_id=envelope_id)
        root_args = ["--drive-root-folder-id", folder_id]
        listed = self._run([
            "rclone", "lsjson", f"{self.remote}:", "--files-only",
            "--max-depth", "1", "--hash", *root_args,
        ], text=True)
        try:
            rows = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError as error:
            raise RuntimeError("Drive archive listing was invalid") from error
        matches = [row for row in rows if row.get("Name") == filename]
        if len(matches) > 1:
            raise RuntimeError("Drive archive contains duplicate contract names")
        if matches:
            size = int(matches[0].get("Size") or -1)
            hashes = matches[0].get("Hashes") or {}
            remote_md5 = str(hashes.get("MD5") or hashes.get("md5") or "").casefold()
            local_md5 = hashlib.md5(pdf_bytes, usedforsecurity=False).hexdigest()
            if size != len(pdf_bytes) or remote_md5 != local_md5:
                raise RuntimeError(
                    "Drive archive filename exists with different content")
            return ArchiveResult(filename, folder_id, _drive_url(folder_id), False)
        self._run([
            "rclone", "rcat", f"{self.remote}:{filename}", *root_args,
            "--no-traverse",
        ], input_bytes=pdf_bytes)
        return ArchiveResult(filename, folder_id, _drive_url(folder_id), True)

    def _run(self, args: list[str], *, text: bool = False,
             input_bytes: bytes | None = None):
        kwargs = {
            "check": False, "capture_output": True, "timeout": 120,
        }
        if input_bytes is not None:
            kwargs["input"] = input_bytes
        kwargs["text"] = text
        result = self.runner(args, **kwargs)
        if result.returncode:
            raise RuntimeError(f"Drive archive command failed: {args[1]}")
        return result


def folder_id_from_url(value: str) -> str:
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", str(value or ""))
    folder_id = match.group(1) if match else ""
    if not SAFE_FOLDER_ID.fullmatch(folder_id):
        raise ValueError("Account Drive Folder is missing a valid folder ID")
    return folder_id


def archive_filename(account_name: str, *, signed_date: str,
                     envelope_id: str) -> str:
    safe_account = re.sub(r"[^A-Za-z0-9 .&_-]+", "", account_name).strip()
    safe_account = re.sub(r"\s+", " ", safe_account)[:80]
    safe_envelope = re.sub(r"[^A-Za-z0-9-]", "", envelope_id)[:36]
    if not safe_account or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", signed_date):
        raise ValueError("contract archive name inputs are invalid")
    if not safe_envelope:
        raise ValueError("DocuSign envelope ID is invalid for archive naming")
    return f"{signed_date} - {safe_account} - Signed MSA + SOW - {safe_envelope}.pdf"


def _drive_url(folder_id: str) -> str:
    # The Account folder remains the stable link; individual file IDs are not
    # fetched because this writer intentionally exposes no broader metadata API.
    return f"https://drive.google.com/drive/folders/{folder_id}"
