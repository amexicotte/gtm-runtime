"""Notion REST client — thin wrapper over the parts of the API we need.

Uses the internal-integration token (NOTION_API_TOKEN). Talks the raw REST API
(NOT the MCP the humans use), so property payloads/readers below follow Notion's
public v1 shapes.

LIVE-VALIDATION NOTE: this hasn't been run against the real token (Claude never
holds secrets). The two things most likely to need a one-line adjust after the
first real run are (a) the query endpoint — data-sources API vs classic
databases API — and (b) the create-page parent shape. Both are centralized here.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import config

API = "https://api.notion.com/v1"


class Notion:
    def __init__(self, token: str | None = None, version: str | None = None):
        self.token = token or config.NOTION_API_TOKEN
        self.version = version or config.NOTION_VERSION
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        })

    # --- low-level ---
    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            r = self.s.request(method, f"{API}{path}", timeout=60, **kw)
            if r.status_code == 429:  # rate limited
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            if r.status_code >= 500:
                time.sleep(1 + attempt)
                continue
            if not r.ok:
                raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:400]}")
            return r.json()
        raise RuntimeError(f"Notion {method} {path} failed after retries")

    # --- queries ---
    def query(self, data_source_id: str, filter_: dict | None = None,
              sorts: list | None = None, page_size: int = 100) -> list[dict]:
        """Query a data source, following pagination. Endpoint centralised here:
        if this token is on the classic API, change to /databases/{id}/query."""
        body: dict[str, Any] = {"page_size": page_size}
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        out, cursor = [], None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            data = self._req("POST", f"/data_sources/{data_source_id}/query", json=body)
            out.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return out

    def get_page(self, page_id: str) -> dict:
        return self._req("GET", f"/pages/{page_id}")

    def block_children_text(self, block_id: str) -> str:
        """Concatenate plain text of a page's body blocks — the Fireflies
        transcript (synced natively) plus any appended `## Notes`."""
        parts, cursor = [], None
        while True:
            q = f"?start_cursor={cursor}" if cursor else ""
            data = self._req("GET", f"/blocks/{block_id}/children{q}")
            for b in data.get("results", []):
                parts.append(_block_text(b))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return "\n".join(p for p in parts if p).strip()

    # --- writes ---
    def update_page(self, page_id: str, properties: dict) -> dict:
        return self._req("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def create_page(self, data_source_id: str, properties: dict,
                    children: list | None = None) -> dict:
        body: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        return self._req("POST", "/pages", json=body)

    def append_notes(self, page_id: str, text: str) -> dict:
        children = [
            {"object": "block", "type": "heading_2",
             "heading_2": {"rich_text": _rt(text="Notes")}},
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": _rt(text=text)}},
        ]
        return self._req("PATCH", f"/blocks/{page_id}/children", json={"children": children})

    # --- domain lookup for the matcher ---
    def find_company_by_domain(self, domain: str) -> dict | None:
        res = self.query(config.DS_COMPANIES, filter_={
            "property": "Domain", "rich_text": {"equals": domain}
        }, page_size=1)
        return res[0] if res else None

    def find_tasks_for_meeting(self, meeting_page_id: str) -> list[dict]:
        return self.query(config.DS_TASKS, filter_={
            "property": "Source meeting", "relation": {"contains": meeting_page_id}
        })


# --- meeting content assembly ---
def meeting_content(page: dict, body_text: str = "") -> str:
    """Reasoning input. The Fireflies transcript now syncs into the page body
    (native integration toggle), alongside any appended `## Notes` — so the page
    body IS the full input. Fall back to the summary properties only if the body
    is empty (e.g. a non-Fireflies meeting)."""
    if body_text and body_text.strip():
        return body_text.strip()
    p = page.get("properties", {})
    blocks = [
        ("Gist", read_text(p.get("Gist"))),
        ("Overview", read_text(p.get("Overview"))),
        ("Bullet Notes", read_text(p.get("Bullet Notes"))),
        ("Action Items", read_text(p.get("Action Items"))),
    ]
    return "\n\n".join(f"## {label}\n{val}" for label, val in blocks if val).strip()


# --- property builders (write side) ---
def _rt(text: str) -> list:
    return [{"type": "text", "text": {"content": text[:1990]}}] if text else []

def title(text: str) -> dict: return {"title": _rt(text)}
def rich_text(text: str) -> dict: return {"rich_text": _rt(text)}
def select(name: str) -> dict: return {"select": {"name": name}}
def number(n) -> dict: return {"number": n}
def date(iso: str) -> dict: return {"date": {"start": iso}}
def relation(page_ids: list[str]) -> dict: return {"relation": [{"id": i} for i in page_ids]}
def people(user_ids: list[str]) -> dict: return {"people": [{"id": i} for i in user_ids]}
def url(u: str) -> dict: return {"url": u}


# --- property readers (read side; raw Notion API shapes) ---
def read_text(prop: dict | None) -> str:
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []))
    return ""

def read_select(prop: dict | None) -> str | None:
    if prop and prop.get("select"):
        return prop["select"].get("name")
    return None

def read_number(prop: dict | None):
    return prop.get("number") if prop else None

def read_relation_ids(prop: dict | None) -> list[str]:
    if prop and prop.get("relation"):
        return [r["id"] for r in prop["relation"]]
    return []

def read_multi_select(prop: dict | None) -> list[str]:
    if prop and prop.get("multi_select"):
        return [o["name"] for o in prop["multi_select"]]
    return []


def _block_text(block: dict) -> str:
    t = block.get("type", "")
    node = block.get(t, {})
    rts = node.get("rich_text") if isinstance(node, dict) else None
    if rts:
        return "".join(x.get("plain_text", "") for x in rts)
    return ""
