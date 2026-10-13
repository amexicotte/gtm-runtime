"""Read-only connectivity and schema discovery for Phase 0.

The production runtimes intentionally keep their write clients separate from this
module. Every monday operation here is a GraphQL query, Notion probes only
identity/page-count metadata, and Drive requires an rclone `drive.readonly` remote.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

import requests

from .monday import API as MONDAY_API
from .monday import BOARDS


NOTION_API = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2025-09-03"
SAFE_RCLONE_REMOTE = re.compile(r"^[A-Za-z0-9_.-]+$")


class DiscoveryError(RuntimeError):
    """A safe, user-facing Phase 0 connectivity failure."""


def load_env_file(path: Path) -> list[str]:
    """Load simple KEY=VALUE entries without overriding the current process.

    The return value contains variable names only so callers can report which
    configuration surfaces were found without ever logging their values.
    """
    if not path.is_file():
        return []
    configured: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            configured.append(name)
            os.environ.setdefault(name, value)
    return configured


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DiscoveryError(f"missing required environment variable: {name}")
    return value


def _monday_query(query: str, variables: dict[str, Any] | None = None) -> dict:
    if re.search(r"\bmutation\b", query, flags=re.IGNORECASE):
        raise DiscoveryError("Phase 0 monday discovery refuses GraphQL mutations")
    token = _required_env("MONDAY_API_TOKEN")
    version = os.environ.get("MONDAY_API_VERSION", "2026-07").strip() or "2026-07"
    session = requests.Session()
    session.headers.update({
        "Authorization": token,
        "API-Version": version,
        "Content-Type": "application/json",
    })
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.post(
                MONDAY_API,
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 3:
                    response.raise_for_status()
                time.sleep(float(response.headers.get("Retry-After", 1 + attempt)))
                continue
            if not response.ok:
                raise DiscoveryError(f"monday connectivity failed with HTTP {response.status_code}")
            payload = response.json()
            if payload.get("errors"):
                messages = "; ".join(
                    str(error.get("message", "unknown GraphQL error"))
                    for error in payload["errors"]
                )
                raise DiscoveryError(f"monday GraphQL query failed: {messages}")
            return payload.get("data") or {}
        except DiscoveryError:
            raise
        except Exception as error:  # requests exposes several transport subclasses
            last_error = error
            if attempt < 3:
                time.sleep(1 + attempt)
    raise DiscoveryError(f"monday connectivity failed: {last_error}")


def monday_connectivity() -> dict:
    data = _monday_query("query { me { id name } account { id name slug } }")
    return {
        "status": "ready",
        "access": "read-only discovery query succeeded",
        "user": data.get("me"),
        "account": data.get("account"),
        "api_version": os.environ.get("MONDAY_API_VERSION", "2026-07"),
    }


def _settings(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def monday_schema() -> dict:
    query = """
    query PhaseZeroSchema($ids: [ID!]) {
      boards(ids: $ids) {
        id name state board_kind permissions items_count workspace_id
        groups { id title }
        views { id name type }
        columns { id title type settings }
      }
    }
    """
    data = _monday_query(query, {"ids": [str(value) for value in BOARDS.values()]})
    boards = []
    for board in data.get("boards") or []:
        columns = []
        for column in board.get("columns") or []:
            settings = _settings(column.get("settings"))
            target_ids = settings.get("boardIds") or []
            if not target_ids and settings.get("boardId"):
                target_ids = [settings["boardId"]]
            columns.append({
                "id": column.get("id"),
                "title": column.get("title"),
                "type": column.get("type"),
                "target_board_ids": [str(value) for value in target_ids],
                "settings": settings,
            })
        boards.append({
            "id": str(board.get("id")),
            "key": next(
                (key for key, value in BOARDS.items() if str(value) == str(board.get("id"))),
                None,
            ),
            "name": board.get("name"),
            "state": board.get("state"),
            "kind": board.get("board_kind"),
            "permissions": board.get("permissions"),
            "items_count": board.get("items_count"),
            "workspace_id": str(board.get("workspace_id")),
            "groups": board.get("groups") or [],
            "views": board.get("views") or [],
            "columns": columns,
        })
    return {
        "status": "ready",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "api_version": os.environ.get("MONDAY_API_VERSION", "2026-07"),
        "boards": boards,
    }


def monday_timeline(item_id: str) -> dict:
    if not item_id.isdigit():
        raise DiscoveryError("monday item ID must be numeric")
    query = """
    query PhaseZeroTimeline($id: ID!) {
      timeline(id: $id, skipConnectedItems: true) {
        timeline_items_page {
          cursor
          timeline_items {
            id title type custom_activity_id created_at
            user { id name }
            board { id }
            item { id name }
          }
        }
      }
    }
    """
    data = _monday_query(query, {"id": item_id})
    page = (data.get("timeline") or {}).get("timeline_items_page") or {}
    items = page.get("timeline_items") or []
    return {
        "status": "ready",
        "item_id": item_id,
        "items_returned": len(items),
        "has_more": bool(page.get("cursor")),
        "timeline_items": items,
    }


def notion_connectivity() -> dict:
    token = _required_env("NOTION_API_TOKEN")
    version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION)
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }
    me_response = requests.get(f"{NOTION_API}/users/me", headers=headers, timeout=30)
    if not me_response.ok:
        raise DiscoveryError(f"Notion identity probe failed with HTTP {me_response.status_code}")
    search_response = requests.post(f"{NOTION_API}/search", headers=headers,
                                    json={"filter": {"property": "object",
                                                     "value": "page"},
                                          "page_size": 1}, timeout=30)
    if not search_response.ok:
        raise DiscoveryError(
            f"Notion workspace probe failed with HTTP {search_response.status_code}"
        )
    identity = me_response.json()
    sample = search_response.json()
    results = sample.get("results") or [] if isinstance(sample, dict) else []
    return {
        "status": "ready",
        "access": "workspace-visible pages are readable",
        "integration": {"id": identity.get("id"), "name": identity.get("name")},
        "accessible_page_sample_count": len(results) if isinstance(results, list) else 0,
        "api_version": version,
    }


def _rclone_config_scope(remote: str) -> str | None:
    # GitHub Actions defines ephemeral rclone remotes through environment
    # variables. Older distro rclone builds can use those remotes for I/O but
    # `config redacted <name>` only inspects the on-disk config file.
    # rclone preserves punctuation when mapping a remote name to environment
    # keys (for example `harbor-drive` -> `RCLONE_CONFIG_HARBOR-DRIVE_SCOPE`).
    # Keep the underscore variant as a compatibility fallback for file/config
    # wrappers that normalize environment names.
    exact_env_remote = remote.upper()
    normalized_env_remote = re.sub(r"[^A-Za-z0-9]", "_", remote).upper()
    for env_remote in (exact_env_remote, normalized_env_remote):
        env_scope = os.environ.get(f"RCLONE_CONFIG_{env_remote}_SCOPE", "").strip()
        if env_scope:
            return env_scope
    result = subprocess.run(
        ["rclone", "config", "redacted", remote],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise DiscoveryError(f"rclone remote {remote!r} is not readable")
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "scope":
            return value.strip()
    return None


def drive_connectivity() -> dict:
    remote = _required_env("GOOGLE_DRIVE_RCLONE_REMOTE")
    if not SAFE_RCLONE_REMOTE.fullmatch(remote):
        raise DiscoveryError("GOOGLE_DRIVE_RCLONE_REMOTE contains unsupported characters")
    scope = _rclone_config_scope(remote)
    if scope != "drive.readonly":
        raise DiscoveryError(
            f"rclone remote {remote!r} must use drive.readonly; configured scope is {scope or 'unknown'}"
        )
    result = subprocess.run(
        [
            "rclone", "about", f"{remote}:", "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise DiscoveryError(f"Drive root probe failed: {detail}")
    try:
        metadata = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise DiscoveryError("rclone returned invalid JSON") from error
    return {
        "status": "ready",
        "access": "whole visible Drive is readable",
        "remote": remote,
        "scope": scope,
        "quota_metadata_available": isinstance(metadata, dict),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harbor Phase 0 read-only discovery CLI")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="optional local env file; existing process variables take precedence",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("monday-connectivity")
    subparsers.add_parser("monday-schema")
    timeline = subparsers.add_parser("monday-timeline")
    timeline.add_argument("item_id")
    subparsers.add_parser("notion-connectivity")
    subparsers.add_parser("drive-connectivity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    load_env_file(args.env_file)
    commands = {
        "monday-connectivity": monday_connectivity,
        "monday-schema": monday_schema,
        "monday-timeline": lambda: monday_timeline(args.item_id),
        "notion-connectivity": notion_connectivity,
        "drive-connectivity": drive_connectivity,
    }
    try:
        result = commands[args.command]()
    except (DiscoveryError, requests.RequestException, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
