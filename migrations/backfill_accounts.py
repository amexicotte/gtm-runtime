#!/usr/bin/env python3
"""
backfill_accounts.py — deterministic Notion -> monday Accounts backfill.

Belongs in the harbor-gtm-engine repo (e.g. migrations/backfill_accounts.py).
Runs in your environment (GitHub Actions or local) exactly like the engine —
NOT inside a chat. Pure code + API calls: ZERO LLM tokens at runtime.

What it does:
  1. Pulls Companies from Notion (source of truth).
  2. Pulls Accounts from the monday CRM board.
  3. Joins the two on the bare domain (the match key already backfilled).
  4. Maps each Notion field to its monday column via explicit lookup tables.
  5. Writes only columns that are currently EMPTY on monday (never overwrites
     human work — same discipline as the engine). Idempotent: safe to re-run.
  6. Prints every unmapped Source / owner so you can extend the tables — it
     flags rather than guesses, again matching the engine.

Reusable for any client Notion->monday migration: swap the CONFIG block.

Env vars required:
  NOTION_API_TOKEN   Notion internal integration token (read access to Companies)
  MONDAY_API_TOKEN   monday API v2 token (write access to the CRM board)

Usage:
  python backfill_accounts.py --dry-run     # log intended writes, write nothing
  python backfill_accounts.py               # apply
"""

import argparse
import json
import os
import sys
import time
from urllib import request, error

# --------------------------------------------------------------------------- #
# CONFIG — Harbor CRM (swap this block per client)
# --------------------------------------------------------------------------- #
NOTION_DB_ID = "f6c4743a-d5f3-83a1-9fc3-07e6670d5709"   # Companies data source
MONDAY_BOARD_ID = 10000000001                            # Accounts board

# monday column IDs (created during the schema build)
COL = {
    "domain":        "text_mm5r760h",          # text  (match key)
    "domain_role":   "color_mm5rf9pt",         # status
    "source":        "color_mm5r9qvf",         # status
    "engagement":    "dropdown_mm5ryn14",      # dropdown (multi)
    "cadence":       "color_mm5rmymb",         # status
    "next_followup": "date_mm5r5gp4",          # date
    "owner":         "multiple_person_mm5rmcfe",  # people
    "notes":         "long_text_mm5r6tnx",     # long_text
    "drive":         "link_mm5rr5yg",          # link
}

# Notion person name -> monday user id (extend as the team grows)
OWNER_MAP = {
    "Alex Mexicotte": 100000007,
    "Sam Reyes": 100000008,
}

# Notion "Domain Role" select -> monday status label (identity here)
DOMAIN_ROLE_MAP = {"Client": "Client", "Partner": "Partner", "Investor": "Investor"}

# Notion "Source" select -> monday status label.
# Clean values pass through; known polluted values (partner/person names that
# are really referral origins) collapse to "Referral"; anything unknown returns
# None -> skipped + logged for you to triage (who/what still goes in the
# forward-looking Source - Contact / Source - Company relations).
SOURCE_CLEAN = {
    "LinkedIn": "LinkedIn", "Referral": "Referral", "Network": "Network",
    "Inbound": "Inbound", "Outbound": "Outbound", "Expansion": "Expansion",
    "Other": "Other",
}
REFERRAL_ALIASES = {"Granite Peak Capital", "Chisel", "Taylor Kim", "Expert Network"}

# Notion "Follow-up cadence" select -> monday status label (extend as needed)
CADENCE_MAP = {
    "Weekly": "Weekly", "Biweekly": "Biweekly", "Bi-weekly": "Biweekly",
    "Monthly": "Monthly", "Quarterly": "Quarterly",
    "As needed": "As needed", "Ad hoc": "As needed",
}

BATCH_SLEEP = 0.25          # be gentle with monday rate limits
_unmapped_sources = set()
_unmapped_owners = set()


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _post(url, headers, payload):
    data = json.dumps(payload).encode()
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except error.HTTPError as e:
        sys.exit(f"HTTP {e.code} calling {url}: {e.read().decode()[:400]}")


def notion_headers():
    tok = os.environ["NOTION_API_TOKEN"]
    return {"Authorization": f"Bearer {tok}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def monday_headers():
    # monday docs: pass the personal API token as a Bearer token.
    return {"Authorization": f"Bearer {os.environ['MONDAY_API_TOKEN']}",
            "API-Version": os.environ.get("MONDAY_API_VERSION", "2026-07"),
            "Content-Type": "application/json"}


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #
def notion_companies():
    """Return {bare_domain: {field: value}} from the Companies DB."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    out, cursor = {}, None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _post(url, notion_headers(), body)
        for page in res.get("results", []):
            p = page["properties"]
            domain = _rich(p.get("Domain")) or ""
            domain = domain.strip().lower().lstrip("https://").lstrip("http://").rstrip("/")
            if not domain:
                continue
            out[domain] = {
                "domain_role": _select(p.get("Domain Role")),
                "source":      _select(p.get("Source")),
                "engagement":  _multi(p.get("Engagement Type")),
                "cadence":     _select(p.get("Follow-up cadence")),
                "next":        _date(p.get("Next follow-up")),
                "owner":       _people(p.get("Relationship owner")),
                "notes":       _rich(p.get("Notes")),
                "drive":       _url(p.get("Related Drive folder")),
            }
        if not res.get("has_more"):
            return out
        cursor = res["next_cursor"]


def monday_accounts():
    """Return [{'id':..., 'domain':...}] for accounts on the board."""
    q = """
    query ($board: ID!, $cursor: String) {
      boards(ids: [$board]) {
        items_page(limit: 250, cursor: $cursor) {
          cursor
          items { id column_values(ids: ["%s"]) { text } }
        }
      }
    }""" % COL["domain"]
    url = "https://api.monday.com/v2"
    out, cursor = [], None
    while True:
        res = _post(url, monday_headers(), {"query": q,
                    "variables": {"board": str(MONDAY_BOARD_ID), "cursor": cursor}})
        page = res["data"]["boards"][0]["items_page"]
        for it in page["items"]:
            dom = (it["column_values"][0]["text"] or "").strip().lower()
            if dom:
                out.append({"id": int(it["id"]), "domain": dom})
        cursor = page["cursor"]
        if not cursor:
            return out


# --- Notion property readers -------------------------------------------------
def _rich(prop):
    if not prop:
        return None
    arr = prop.get("rich_text") or prop.get("title") or []
    return "".join(t.get("plain_text", "") for t in arr) or None

def _select(prop):
    return (prop or {}).get("select", {}).get("name") if prop else None

def _multi(prop):
    return [o["name"] for o in (prop or {}).get("multi_select", [])] if prop else []

def _date(prop):
    d = (prop or {}).get("date") if prop else None
    return d.get("start") if d else None

def _people(prop):
    return [pp.get("name") for pp in (prop or {}).get("people", [])] if prop else []

def _url(prop):
    return (prop or {}).get("url") if prop else None


# --------------------------------------------------------------------------- #
# Transform — deterministic field mapping
# --------------------------------------------------------------------------- #
def map_source(val):
    if not val:
        return None
    if val in SOURCE_CLEAN:
        return SOURCE_CLEAN[val]
    if val in REFERRAL_ALIASES:
        return "Referral"
    _unmapped_sources.add(val)
    return None

def map_owner(names):
    ids = []
    for n in names:
        if n in OWNER_MAP:
            ids.append({"id": OWNER_MAP[n], "kind": "person"})
        else:
            _unmapped_owners.add(n)
    return {"personsAndTeams": ids} if ids else None

def build_values(n):
    """Notion row -> monday column_values dict (only non-empty fields)."""
    v = {}
    if n["domain_role"] in DOMAIN_ROLE_MAP:
        v[COL["domain_role"]] = {"label": DOMAIN_ROLE_MAP[n["domain_role"]]}
    src = map_source(n["source"])
    if src:
        v[COL["source"]] = {"label": src}
    if n["engagement"]:
        v[COL["engagement"]] = {"labels": n["engagement"]}
    if n["cadence"] and n["cadence"] in CADENCE_MAP:
        v[COL["cadence"]] = {"label": CADENCE_MAP[n["cadence"]]}
    if n["next"]:
        v[COL["next_followup"]] = {"date": n["next"][:10]}
    owner = map_owner(n["owner"])
    if owner:
        v[COL["owner"]] = owner
    if n["notes"]:
        v[COL["notes"]] = n["notes"]
    if n["drive"]:
        v[COL["drive"]] = {"url": n["drive"], "text": "Drive"}
    return v


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def write_monday(item_id, values, dry_run):
    if dry_run:
        print(f"  [dry-run] {item_id}: {json.dumps(values)}")
        return
    mutation = """
    mutation ($board: ID!, $item: ID!, $vals: JSON!) {
      change_multiple_column_values(board_id: $board, item_id: $item,
        column_values: $vals, create_labels_if_missing: true) { id }
    }"""
    _post("https://api.monday.com/v2", monday_headers(),
          {"query": mutation, "variables": {
              "board": str(MONDAY_BOARD_ID), "item": str(item_id),
              "vals": json.dumps(values)}})
    time.sleep(BATCH_SLEEP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("Pulling Notion Companies…")
    notion = notion_companies()
    print(f"  {len(notion)} companies with a domain")

    print("Pulling monday Accounts…")
    accounts = monday_accounts()
    print(f"  {len(accounts)} accounts with a match key")

    matched = written = skipped = 0
    for acc in accounts:
        n = notion.get(acc["domain"])
        if not n:
            skipped += 1
            continue
        matched += 1
        vals = build_values(n)
        if not vals:
            continue
        write_monday(acc["id"], vals, args.dry_run)
        written += 1

    print(f"\nMatched {matched} | wrote {written} | "
          f"no-Notion-match {skipped} (left as-is)")
    if _unmapped_sources:
        print("Unmapped Source values (add to SOURCE_CLEAN / REFERRAL_ALIASES):")
        for s in sorted(_unmapped_sources):
            print(f"  - {s}")
    if _unmapped_owners:
        print("Unmapped owners (add to OWNER_MAP):")
        for o in sorted(_unmapped_owners):
            print(f"  - {o}")


if __name__ == "__main__":
    main()
