"""Bounded, read-only business context tools for Phase 3.

Credentials stay inside deterministic clients. Model-facing callers receive only
bounded results and content-free audit metadata; no general provider client or
mutation method is exposed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import subprocess
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .config import config
from .discovery import SAFE_RCLONE_REMOTE, _rclone_config_scope


NOTION_API = "https://api.notion.com/v1"
OPERATING_CONTEXT_QUERIES = ("Harbor", "HQ", "Goals", "Strategy", "Services")
SAFE_ENTITY_ID = re.compile(r"^[0-9]+$")
SAFE_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
TEXT_EXTENSIONS = {".csv", ".md", ".text", ".txt"}
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


class ContextError(RuntimeError):
    """Fail-closed context boundary or provider error."""


def _notion_page_title(page: dict) -> str:
    properties = page.get("properties") or {}
    if not isinstance(properties, dict):
        return ""
    for prop in properties.values():
        if not isinstance(prop, dict) or prop.get("type") != "title":
            continue
        rich = prop.get("title") or []
        return "".join(str(value.get("plain_text") or "")
                       for value in rich if isinstance(value, dict)).strip()
    return ""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_ids(values, *, maximum: int | None = None) -> list[str]:
    maximum = maximum or config.BUSINESS_CONTEXT_MAX_ENTITY_IDS
    result = list(dict.fromkeys(str(value).strip() for value in values))
    if not result or len(result) > maximum or any(
            not SAFE_ENTITY_ID.fullmatch(value) for value in result):
        raise ContextError(
            f"entity IDs must contain 1-{maximum} unique numeric monday IDs")
    return result


@dataclass(frozen=True)
class ContextAudit:
    tool: str
    called_at: str
    request_sha256: str
    result_sha256: str
    result_count: int
    duration_ms: int


class WorkspaceNotion:
    """Bounded read-only search over pages visible to the existing integration."""

    def __init__(self, *, token: str | None = None,
                 version: str | None = None, session=None):
        self.token = (token if token is not None else config.NOTION_API_TOKEN).strip()
        self.version = version or config.NOTION_VERSION
        self.session = session or requests.Session()
        self._page_excerpts: dict[str, str] = {}
        if not self.token:
            raise ContextError("NOTION_API_TOKEN is required")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self.session.request(
            method, f"{NOTION_API}{path}", headers=self.headers,
            timeout=30, **kwargs)
        if not response.ok:
            raise ContextError(
                f"Notion request failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ContextError("Notion returned an invalid response")
        return payload

    def connectivity(self) -> dict:
        identity = self._request("GET", "/users/me")
        sample = self._request("POST", "/search", json={
            "filter": {"property": "object", "value": "page"},
            "page_size": 1,
        })
        results = sample.get("results") or []
        if not isinstance(results, list):
            raise ContextError("Notion workspace probe returned invalid results")
        return {
            "status": "ready",
            "access": "workspace-visible pages are readable",
            "integration": {"id": str(identity.get("id") or ""),
                            "name": str(identity.get("name") or "")},
            "accessible_page_sample_count": len(results),
            "api_version": self.version,
        }

    def _children(self, block_id: str) -> list[dict]:
        rows: list[dict] = []
        cursor = ""
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._request(
                "GET", f"/blocks/{block_id}/children", params=params)
            batch = payload.get("results") or []
            if not isinstance(batch, list):
                raise ContextError("Notion block response is invalid")
            rows.extend(batch)
            if len(rows) > 500:
                raise ContextError("Notion page exceeds the block cap")
            if not payload.get("has_more"):
                return rows
            cursor = str(payload.get("next_cursor") or "")
            if not cursor:
                raise ContextError("Notion pagination cursor is missing")

    def get_operating_brief(self) -> dict:
        """Build a compact stable context from existing workspace pages."""
        pages: list[dict] = []
        seen: set[str] = set()
        for query in OPERATING_CONTEXT_QUERIES:
            for page in self.search_business_docs(query, limit=2):
                page_id = str(page.get("id") or "")
                if not page_id or page_id in seen or not page.get("excerpt"):
                    continue
                seen.add(page_id)
                pages.append(page)
                if len(pages) >= config.BUSINESS_CONTEXT_MAX_DOC_RESULTS:
                    break
            if len(pages) >= config.BUSINESS_CONTEXT_MAX_DOC_RESULTS:
                break
        text = "\n\n".join(
            f"{page['name']}\n{page['excerpt']}" for page in pages)
        text = text[:config.BUSINESS_CONTEXT_MAX_PREFETCH_CHARS].strip()
        if not text:
            raise ContextError(
                "Notion workspace has no readable operating-context pages")
        return {
            "source": "notion_workspace",
            "page_count": len(pages),
            "pages": [{"id": page["id"], "name": page["name"],
                       "modified_at": page["modified_at"]} for page in pages],
            "content": text,
            "content_sha256": _sha(text),
        }

    def _blocks_text(self, block_id: str, *, depth: int = 0) -> str:
        if depth > 3:
            return ""
        lines: list[str] = []
        for block in self._children(block_id):
            kind = str(block.get("type") or "")
            node = block.get(kind) or {}
            rich = node.get("rich_text") if isinstance(node, dict) else []
            line = "".join(str(value.get("plain_text") or "")
                           for value in rich or []).strip()
            if line:
                lines.append(line)
            if block.get("has_children"):
                nested = self._blocks_text(str(block.get("id") or ""), depth=depth + 1)
                if nested:
                    lines.append(nested)
            if sum(len(value) for value in lines) > (
                    config.BUSINESS_CONTEXT_MAX_EXCERPT_CHARS * 2):
                break
        return "\n".join(lines).strip()[:
            config.BUSINESS_CONTEXT_MAX_EXCERPT_CHARS]

    def search_business_docs(self, query: str, *, account_id: str = "",
                             deal_id: str = "", limit: int | None = None
                             ) -> list[dict]:
        query = re.sub(r"\s+", " ", str(query)).strip()
        if not query or len(query) > 200:
            raise ContextError("Notion search query must contain 1-200 characters")
        for value in (account_id, deal_id):
            if value and not SAFE_ENTITY_ID.fullmatch(str(value)):
                raise ContextError("optional monday context IDs must be numeric")
        limit = min(limit or config.BUSINESS_CONTEXT_MAX_DOC_RESULTS,
                    config.BUSINESS_CONTEXT_MAX_DOC_RESULTS)
        payload = self._request("POST", "/search", json={
            "query": query,
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending",
                     "timestamp": "last_edited_time"},
            "page_size": min(100, max(limit * 4, 10)),
        })
        rows = payload.get("results") or []
        if not isinstance(rows, list):
            raise ContextError("Notion workspace search returned invalid results")
        results = []
        for page in rows:
            if not isinstance(page, dict) or page.get("object") != "page":
                continue
            page_id = str(page.get("id") or "")
            title = _notion_page_title(page)
            if not page_id or not title:
                continue
            if page_id not in self._page_excerpts:
                self._page_excerpts[page_id] = self._blocks_text(page_id)
            excerpt = self._page_excerpts[page_id]
            results.append({
                "source": "notion", "id": page_id, "name": title,
                "modified_at": str(page.get("last_edited_time") or ""),
                "excerpt": excerpt,
            })
            if len(results) >= limit:
                break
        return results


# Backward-compatible import name; access is now workspace-wide and read-only.
ScopedNotion = WorkspaceNotion


class WorkspaceDrive:
    """Bounded reader over the existing account's whole visible Drive."""

    def __init__(self, *, remote: str | None = None, runner=None):
        self.remote = (remote or config.GOOGLE_DRIVE_RCLONE_REMOTE).strip()
        self.runner = runner or subprocess.run
        self._index: list[dict] | None = None
        if not SAFE_RCLONE_REMOTE.fullmatch(self.remote):
            raise ContextError("Google Drive rclone remote is invalid")
        try:
            scope = _rclone_config_scope(self.remote)
        except Exception as error:
            raise ContextError("Google Drive rclone remote is not readable") from error
        if scope != "drive.readonly":
            raise ContextError("Google Drive rclone remote must use drive.readonly")

    def _run(self, args: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess:
        result = self.runner(args, check=False, capture_output=True, text=True,
                             timeout=timeout)
        if result.returncode:
            raise ContextError(
                f"Drive command failed: {args[1]} [{_drive_failure_code(result.stderr)}]")
        return result

    def connectivity(self) -> dict:
        result = self._run(["rclone", "about", f"{self.remote}:", "--json"])
        try:
            metadata = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise ContextError("Drive connectivity response is invalid") from error
        if not isinstance(metadata, dict):
            raise ContextError("Drive connectivity response is invalid")
        shared_drives = self._shared_drives()
        return {"status": "ready", "access": "whole visible Drive is readable",
                "remote": self.remote, "scope": "drive.readonly",
                "shared_drive_count": len(shared_drives)}

    def _shared_drives(self) -> list[dict]:
        result = self._run([
            "rclone", "backend", "drives", f"{self.remote}:",
        ])
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as error:
            raise ContextError("Drive shared-drive response is invalid") from error
        if not isinstance(rows, list) or len(rows) > 100:
            raise ContextError("Drive shared-drive response exceeds its cap")
        result_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            drive_id = str(row.get("id") or "")
            if not SAFE_DRIVE_ID.fullmatch(drive_id):
                raise ContextError("Drive returned an invalid shared-drive ID")
            result_rows.append({"id": drive_id, "name": str(row.get("name") or "")})
        return result_rows

    def _files(self) -> list[dict]:
        if self._index is not None:
            return self._index
        collected: list[dict] = []
        locations: list[tuple[str, list[str]]] = [
            ("my_drive", []),
            ("shared_with_me", ["--drive-shared-with-me"]),
        ]
        locations.extend((
            f"shared_drive:{row['id']}",
            [f"--drive-team-drive={row['id']}"],
        ) for row in self._shared_drives())
        for location, location_args in locations:
            args = [
                "rclone", "lsjson", f"{self.remote}:",
                "--recursive", "--files-only",
            ]
            args.extend(location_args)
            result = self._run(args)
            try:
                rows = json.loads(result.stdout or "[]")
            except json.JSONDecodeError as error:
                raise ContextError("Drive metadata response is invalid") from error
            if not isinstance(rows, list):
                raise ContextError("Drive metadata response is invalid")
            for row in rows:
                if isinstance(row, dict):
                    value = dict(row)
                    value["_location"] = location
                    collected.append(value)
            if len(collected) > 10_000:
                raise ContextError("Drive query exceeds the metadata cap")
        unique = {}
        for row in collected:
            key = str(row.get("ID") or row.get("Path") or "")
            if key:
                unique.setdefault(key, row)
        self._index = list(unique.values())
        return self._index

    def search_business_docs(self, query: str, *, account_id: str = "",
                             deal_id: str = "", limit: int | None = None) -> list[dict]:
        query = re.sub(r"\s+", " ", str(query)).strip()
        if not query or len(query) > 200:
            raise ContextError("Drive search query must contain 1-200 characters")
        for value in (account_id, deal_id):
            if value and not SAFE_ENTITY_ID.fullmatch(str(value)):
                raise ContextError("optional monday context IDs must be numeric")
        limit = min(limit or config.BUSINESS_CONTEXT_MAX_DOC_RESULTS,
                    config.BUSINESS_CONTEXT_MAX_DOC_RESULTS)
        terms = [term.casefold() for term in re.findall(r"[\w.-]+", query)
                 if len(term) > 1]
        matches = []
        if not terms:
            raise ContextError("Drive search query has no usable terms")
        for row in self._files():
            path = str(row.get("Path") or row.get("Name") or "")
            safe_path = _safe_drive_path(path)
            haystack = f"{safe_path} {row.get('MimeType', '')}".casefold()
            if not terms or not all(term in haystack for term in terms):
                continue
            matches.append((max((haystack.count(term) for term in terms), default=0), row,
                            safe_path))
        matches.sort(key=lambda value: (-value[0], value[2].casefold()))
        results = []
        for _score, row, path in matches[:limit]:
            mime = str(row.get("MimeType") or "")
            result = {
                "source": "google_drive",
                "id": str(row.get("ID") or ""), "name": str(row.get("Name") or ""),
                "path": path, "mime_type": mime,
                "modified_at": str(row.get("ModTime") or ""),
                "size": int(row.get("Size") or 0),
            }
            suffix = PurePosixPath(path).suffix.casefold()
            if (mime == GOOGLE_DOC_MIME or suffix in TEXT_EXTENSIONS or
                    suffix in {".pdf", ".docx"}):
                result["excerpt"] = self._document_excerpt(
                    row, path, location=str(row.get("_location") or "my_drive"))
            results.append(result)
        return results

    def _document_excerpt(self, row: dict, path: str, *,
                          location: str = "my_drive") -> str:
        path = _safe_drive_path(path)
        size = int(row.get("Size") or 0)
        if size > config.BUSINESS_CONTEXT_MAX_FILE_BYTES:
            return ""
        suffix = PurePosixPath(path).suffix.casefold()
        args = [
            "rclone", "cat", f"{self.remote}:{path}",
        ]
        if location == "shared_with_me":
            args.append("--drive-shared-with-me")
        elif location.startswith("shared_drive:"):
            drive_id = location.partition(":")[2]
            if not SAFE_DRIVE_ID.fullmatch(drive_id):
                raise ContextError("Drive shared-drive location is invalid")
            args.append(f"--drive-team-drive={drive_id}")
        if suffix in {".pdf", ".docx"}:
            result = self.runner(args, check=False, capture_output=True,
                                 text=False, timeout=90)
            if result.returncode:
                raise ContextError("Drive command failed: cat")
            raw = bytes(result.stdout or b"")
            if len(raw) > config.BUSINESS_CONTEXT_MAX_FILE_BYTES:
                return ""
            try:
                if suffix == ".pdf":
                    from pypdf import PdfReader
                    value = "\n".join(
                        str(page.extract_text() or "")
                        for page in PdfReader(BytesIO(raw)).pages[:50])
                else:
                    from docx import Document
                    value = "\n".join(
                        paragraph.text for paragraph in Document(BytesIO(raw)).paragraphs)
            except Exception as error:
                raise ContextError("Drive document extraction failed") from error
            return re.sub(r"\s+", " ", value).strip()[:
                config.BUSINESS_CONTEXT_MAX_EXCERPT_CHARS]
        args.extend(["--head", str(config.BUSINESS_CONTEXT_MAX_FILE_BYTES)])
        if suffix not in TEXT_EXTENSIONS:
            args.extend(["--drive-export-formats", "txt"])
        value = self._run(args).stdout
        return re.sub(r"\s+", " ", value).strip()[:
            config.BUSINESS_CONTEXT_MAX_EXCERPT_CHARS]


# Backward-compatible import name; access is now whole-Drive and read-only.
ScopedDrive = WorkspaceDrive


def _safe_drive_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ContextError("Drive path is invalid")
    return str(path)


def _drive_failure_code(stderr: str) -> str:
    """Classify rclone failures without returning provider or token details."""
    value = str(stderr or "").casefold()
    patterns = (
        ("didn't find section", "remote_missing"),
        ("empty token", "token_empty"),
        ("failed to parse token", "token_invalid"),
        ("invalid character", "token_invalid"),
        ("invalid_grant", "oauth_invalid_grant"),
        ("invalid_client", "oauth_invalid_client"),
        ("couldn't fetch token", "oauth_refresh_failed"),
        ("couldn't find root directory", "root_lookup_failed"),
        ("unauthorized", "unauthorized"),
        ("forbidden", "forbidden"),
        (" 401 ", "unauthorized"),
        (" 403 ", "forbidden"),
    )
    return next((code for needle, code in patterns if needle in value),
                "provider_command_failed")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


class WebsiteContext:
    """Public website reader restricted to the exact Harbor domain."""

    def __init__(self, *, url: str | None = None, session=None):
        self.url = url or config.HARBOR_WEBSITE_URL
        self.session = session or requests.Session()

    def refresh(self) -> dict:
        url = self.url
        for _attempt in range(4):
            _validate_website_url(url)
            response = self.session.get(
                url, timeout=30, allow_redirects=False,
                headers={"User-Agent": "HarborBusinessContext/1.0"})
            if response.status_code in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            if not response.ok:
                raise ContextError(
                    f"Harbor website refresh failed with HTTP {response.status_code}")
            if len(response.content) > 1_000_000:
                raise ContextError("Harbor website response exceeds the byte cap")
            parser = _TextExtractor()
            parser.feed(response.text)
            text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:30_000]
            if not text:
                raise ContextError("Harbor website returned no readable text")
            return {
                "url": url, "refreshed_at": dt.datetime.now(
                    dt.timezone.utc).isoformat(),
                "content": text, "content_sha256": _sha(text),
            }
        raise ContextError("Harbor website redirect limit exceeded")


def _validate_website_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {
            "harboradvisors.example", "www.harboradvisors.example"}:
        raise ContextError("website context is restricted to harboradvisors.example")


class ContextTools:
    """Model-safe facade with a hard call budget and content-free audit."""

    def __init__(self, *, crm=None, notion=None, drive=None, max_calls: int | None = None):
        self.crm = crm
        self.notion = notion
        self.drive = drive
        self.max_calls = max_calls or config.BUSINESS_CONTEXT_MAX_TOOL_CALLS
        self.audit: list[dict] = []

    def _call(self, tool: str, request: dict, function) -> dict:
        if len(self.audit) >= self.max_calls:
            raise ContextError("business-context tool-call budget exhausted")
        started = time.monotonic()
        result = function()
        serialized = json.dumps(result, sort_keys=True, default=str)
        count = len(result) if isinstance(result, list) else int(bool(result))
        audit = ContextAudit(
            tool=tool,
            called_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            request_sha256=_sha(json.dumps(request, sort_keys=True)),
            result_sha256=_sha(serialized), result_count=count,
            duration_ms=int((time.monotonic() - started) * 1_000),
        )
        row = asdict(audit)
        self.audit.append(row)
        return {"data": result, "audit": row}

    def get_crm_context(self, entity_ids) -> dict:
        ids = _bounded_ids(entity_ids)
        if self.crm is None:
            raise ContextError("monday read client is unavailable")
        return self._call("get_crm_context", {"entity_ids": ids},
                          lambda: self.crm.bounded_crm_context(ids))

    def get_recent_updates(self, entity_ids, limit: int = 5) -> dict:
        ids = _bounded_ids(entity_ids)
        limit = int(limit)
        if not 1 <= limit <= config.BUSINESS_CONTEXT_MAX_UPDATES:
            raise ContextError("recent Updates limit is outside the configured cap")
        if self.crm is None:
            raise ContextError("monday read client is unavailable")
        return self._call(
            "get_recent_updates", {"entity_ids": ids, "limit": limit},
            lambda: self.crm.recent_updates_for_items(ids, limit=limit))

    def get_open_activities(self, entity_ids) -> dict:
        ids = _bounded_ids(entity_ids)
        if self.crm is None:
            raise ContextError("monday read client is unavailable")
        return self._call(
            "get_open_activities", {"entity_ids": ids},
            lambda: self.crm.open_activity_candidates_for_entities(set(ids))[:50])

    def get_operating_brief(self) -> dict:
        if self.notion is None:
            raise ContextError("Notion workspace reader is unavailable")
        return self._call("get_operating_brief", {}, self.notion.get_operating_brief)

    def search_business_docs(self, query: str, *, account_id: str = "",
                             deal_id: str = "", limit: int | None = None) -> dict:
        if self.notion is None and self.drive is None:
            raise ContextError("business-document readers are unavailable")
        limit = min(limit or config.BUSINESS_CONTEXT_MAX_DOC_RESULTS,
                    config.BUSINESS_CONTEXT_MAX_DOC_RESULTS)
        request = {"query_sha256": _sha(str(query)),
                   "account_id": str(account_id), "deal_id": str(deal_id),
                   "limit": limit}
        def read():
            results = []
            for provider in (self.notion, self.drive):
                if provider is not None:
                    results.extend(provider.search_business_docs(
                        query, account_id=account_id, deal_id=deal_id,
                        limit=limit))
            results.sort(key=lambda row: (
                str(row.get("source") or ""),
                str(row.get("name") or "").casefold()))
            return results[:limit]
        return self._call(
            "search_business_docs", request, read)

    def get_deal_pattern_brief(self) -> dict:
        if self.crm is None:
            raise ContextError("monday read client is unavailable")
        def read():
            state = self.crm.deal_pattern_brief_state()
            return state.get("brief") if isinstance(state, dict) else None
        return self._call("get_deal_pattern_brief", {}, read)


class BusinessContextPrefetcher:
    """Fetch obvious stable context once per reasoning run, not once per thread."""

    def __init__(self, tools: ContextTools, *, website=None):
        self.tools = tools
        self._brief: dict | None = None
        self.website = website
        self._website: dict | None = None
        self._docs: dict[str, list[dict]] = {}

    def for_email(self, crm_snapshot: dict | None = None) -> dict:
        if self._brief is None:
            self._brief = self.tools.get_operating_brief()["data"]
        if self.website is not None and self._website is None:
            self._website = self.website.refresh()
            self._website["content"] = str(self._website.get("content") or "")[:
                config.BUSINESS_CONTEXT_MAX_PREFETCH_CHARS]
        relevant = []
        for query in _snapshot_queries(crm_snapshot or {})[:2]:
            if query not in self._docs:
                if len(self.tools.audit) >= self.tools.max_calls:
                    break
                self._docs[query] = self.tools.search_business_docs(
                    query, limit=2)["data"]
            relevant.extend(self._docs[query])
        result = {
            "operating_brief": self._brief,
            "relevant_business_docs": relevant[:4],
            "context_audit": list(self.tools.audit),
        }
        if self._website is not None:
            result["website_context"] = self._website
        return result


def _snapshot_queries(snapshot: dict) -> list[str]:
    values = []
    for key in ("account", "deal"):
        row = snapshot.get(key) or {}
        if isinstance(row, dict):
            values.append(str(row.get("name") or ""))
    for row in snapshot.get("contacts") or []:
        if isinstance(row, dict):
            values.append(str(row.get("name") or ""))
    result = []
    for value in values:
        value = re.sub(r"\s+", " ", value).strip()
        if 2 <= len(value) <= 200 and value not in result:
            result.append(value)
    return result
