#!/usr/bin/env python3
"""
writer.py — deterministic monday writer for Harbor's meeting-reasoning output.

Takes the reasoning model's SEMANTIC json and writes final values to monday.
The column map is built LIVE from the boards, so column IDs never live in a
prompt or in config: add a column in monday + one line in config/fields.yaml
and it's picked up. Zero LLM. Runs in your env (MONDAY_API_TOKEN).

Rules baked in:
  - model values replace the corresponding CRM values.
  - changed CRM records are final; no monday review queue is created.
  - legacy Proposed Changes are cleared on records receiving final writes.
  - the meeting links to BOTH companies (prospect + partner).
  - any Deal ties to the PROSPECT only, never the partner.
"""
import json, os, re, time, sys
from pathlib import Path

import requests

try:  # engine runtime path in production; repository-root path in direct tests.
    from runtime.evidence import evidence_problem
    from runtime.lifecycle import listening_may_change_stage
except ModuleNotFoundError:  # pragma: no cover - import layout only
    from engine.runtime.evidence import evidence_problem
    from engine.runtime.lifecycle import listening_may_change_stage

API = "https://api.monday.com/v2"
BOARDS = {"account": 10000000001, "contact": 10000000002, "deal": 10000000003,
          "meeting": 10000000004, "activity": 10000000005}
FINAL_STATUS_BOARDS = {"account", "contact", "deal"}


def _post(query, variables=None):
    if ("mutation" in str(query).casefold() and
            os.environ.get("BUSINESS_LISTENING_WRITE_KILL_SWITCH", "").strip().casefold()
            in {"1", "true", "yes", "on"}):
        raise RuntimeError("business-listening write kill switch is active")
    response = requests.post(API, json={"query": query, "variables": variables or {}}, headers={
        "Authorization": os.environ["MONDAY_API_TOKEN"],
        "API-Version": os.environ.get("MONDAY_API_VERSION", "2026-07"),
        "Content-Type": "application/json"}, timeout=60)
    response.raise_for_status()
    out = response.json()
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def live_columns(board_id):
    """title -> {id, type}, fetched live from monday (self-updating)."""
    q = "query($b:[ID!]){boards(ids:$b){columns{id title type}}}"
    cols = _post(q, {"b": [str(board_id)]})["boards"][0]["columns"]
    return {c["title"]: {"id": c["id"], "type": c["type"]} for c in cols}


def fmt(value, col_type):
    if col_type in ("text", "long_text"):
        if isinstance(value, list):
            return "\n".join(f"• {item}" for item in value if item not in (None, ""))
        return str(value)
    if col_type in ("color", "status"):          return {"label": str(value)}
    if col_type == "dropdown":
        v = value if isinstance(value, list) else [value]
        return {"labels": [str(x) for x in v]}
    if col_type == "date":                       return {"date": str(value)[:10]}
    if col_type == "timeline" and isinstance(value, dict):
        start, end = str(value.get("from") or "")[:10], str(value.get("to") or "")[:10]
        return {"from": start, "to": end} if start and end else None
    if col_type in ("numbers", "numeric"):       return _number_value(value)
    if col_type in ("boolean", "checkbox"):      return {"checked": "true" if value else "false"}
    if col_type == "board_relation":
        v = value if isinstance(value, list) else [value]
        try:
            return {"item_ids": [int(i) for i in v]}
        except (TypeError, ValueError):
            return None
    if col_type == "link":                       return {"url": value, "text": value}
    return str(value)


def write_item(board_key, item_id, semantic, fields_cfg):
    """Write the extracted values as final CRM state."""
    cols = live_columns(BOARDS[board_key])
    mapping = fields_cfg.get(board_key, {})
    resolved = {}                 # col_id -> (value, type, title, policy, spec)
    for key, val in semantic.items():
        spec = mapping.get(key)
        if not spec:
            continue                                  # unknown key -> skip
        policy = str(spec.get("policy") or "overwrite").strip().lower()
        if policy == "never":
            continue
        if spec.get("evidence") == "required" and evidence_problem(val):
            print(f"  ! {board_key}.{key} lacks complete evidence; skipped")
            continue
        val = _semantic_value(val)
        if val is None or val == "":
            continue
        allowed = spec.get("allowed_values") or []
        if spec.get("type") in ("status", "dropdown") and not allowed:
            print(f"  ! {board_key}.{key} has no declared allowed values; skipped")
            continue
        if allowed:
            requested = val if isinstance(val, list) else [val]
            normalized = []
            for requested_value in requested:
                wanted = str(requested_value).strip().casefold()
                matches = [item for item in allowed
                           if str(item).strip().casefold() == wanted]
                if not matches:
                    normalized = []
                    break
                normalized.append(matches[0])
            if not normalized:
                print(f"  ! {board_key}.{key} is outside its allowed values; skipped")
                continue
            val = normalized if isinstance(val, list) else normalized[0]
        col = cols.get(spec["column"])
        if not col:
            print(f"  ! column '{spec['column']}' not on {board_key}; skipped")
            continue
        resolved[col["id"]] = (
            key, val, col["type"], spec["column"], policy, spec,
            semantic[key].get("evidence", {})
            if isinstance(semantic[key], dict) else {})
    if not resolved:
        return False
    review = cols.get("Review Status") if board_key in FINAL_STATUS_BOARDS else None
    proposed = cols.get("Proposed Changes") if board_key in FINAL_STATUS_BOARDS else None
    provenance_col = cols.get("Agent Field Provenance")
    if not any(row[6].get("occurred_at") for row in resolved.values()):
        provenance_col = None
    col_values = {}
    merge_ids = [cid for cid, row in resolved.items() if row[4] == "merge"]
    stage_ids = [cid for cid, row in resolved.items()
                 if board_key == "deal" and row[0] == "stage"]
    read_ids = [*merge_ids, *stage_ids,
                *([provenance_col["id"]] if provenance_col else [])]
    read_ids = list(dict.fromkeys(read_ids))
    existing = _existing_text_values(item_id, read_ids) if read_ids else {}
    provenance = _json_dict(existing.get(provenance_col["id"], "")) if provenance_col else {}
    accepted_provenance = dict(provenance)
    for cid, (semantic_key, value, col_type, title, policy, spec, evidence) in resolved.items():
        if (board_key == "deal" and semantic_key == "stage" and
                not listening_may_change_stage(existing.get(cid, ""))):
            print(
                f"  ! deal.stage is protected while current Stage is "
                f"{existing.get(cid, '')!r}; skipped")
            continue
        if (provenance.get(semantic_key) or {}).get("authoritative"):
            print(
                f"  ! {board_key}.{semantic_key} is locked by executed-contract "
                "provenance; skipped")
            continue
        incoming_at = str(evidence.get("occurred_at") or "")
        prior_at = str((provenance.get(semantic_key) or {}).get("occurred_at") or "")
        if prior_at and _is_older(incoming_at, prior_at):
            print(f"  ! {board_key}.{semantic_key} is older than accepted provenance; skipped")
            continue
        if policy == "merge":
            value = _merge_value(existing.get(cid, ""), value)
        formatted = fmt(value, col_type)
        if formatted is None:
            continue
        col_values[cid] = formatted
        if provenance_col and incoming_at:
            accepted_provenance[semantic_key] = {
                "occurred_at": incoming_at,
                "source_id": str(evidence.get("source_id") or ""),
            }
    if not col_values:
        return False
    if review:
        col_values[review["id"]] = {"label": "Verified"}
    if proposed:
        col_values[proposed["id"]] = ""
    if provenance_col and accepted_provenance != provenance:
        col_values[provenance_col["id"]] = json.dumps(
            accepted_provenance, sort_keys=True, separators=(",", ":"))
    _post("mutation($b:ID!,$i:ID!,$v:JSON!){change_multiple_column_values("
          "board_id:$b,item_id:$i,column_values:$v,create_labels_if_missing:false){id}}",
          {"b": str(BOARDS[board_key]), "i": str(item_id), "v": json.dumps(col_values)})
    time.sleep(0.2)
    return True


def _existing_text_values(item_id, column_ids):
    query = ("query($ids:[ID!],$columns:[String!]){items(ids:$ids){"
             "column_values(ids:$columns){id text}}}")
    items = _post(query, {"ids": [str(item_id)], "columns": column_ids})["items"]
    if not items:
        return {}
    return {value["id"]: str(value.get("text") or "")
            for value in items[0].get("column_values") or []}


def _merge_value(existing, proposed):
    """Append durable facts without blanking or repeating existing context."""
    old = str(existing or "").strip()
    if isinstance(proposed, list):
        new = "\n".join(str(item).strip() for item in proposed if str(item).strip())
    else:
        new = str(proposed or "").strip()
    if not old:
        return new
    if not new or new.casefold() in old.casefold():
        return old
    return f"{old}\n{new}"


def _json_dict(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_older(incoming, prior):
    from datetime import datetime, timezone
    def parse(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        return parse(incoming) < parse(prior)
    except ValueError:
        return True


def _link_meeting_companies(meeting_id, prospect_id, partner_ids):
    """Meeting -> prospect plus every partner company present."""
    col = live_columns(BOARDS["meeting"]).get("Account")
    if not col:
        return
    partners = partner_ids if isinstance(partner_ids, list) else [partner_ids]
    ids = [int(x) for x in [prospect_id, *partners] if x]
    _post("mutation($b:ID!,$i:ID!,$v:JSON!){change_multiple_column_values("
          "board_id:$b,item_id:$i,column_values:$v){id}}",
          {"b": str(BOARDS["meeting"]), "i": str(meeting_id),
           "v": json.dumps({col["id"]: {"item_ids": ids}})})


def apply(result, ctx, fields_path=None):
    """
    result : model's semantic json {deal, meeting, account, contacts:[...]}
    ctx    : ids from the matcher {meeting_id, prospect_account_id,
             partner_account_id, deal_id, contact_ids:[...]}
    """
    import yaml
    fields_path = fields_path or Path(__file__).with_name("config") / "fields.yaml"
    with open(fields_path) as fields_file:
        cfg = yaml.safe_load(fields_file)
    changed = []
    if ctx.get("meeting_id"):
        partner_ids = ctx.get("partner_account_ids", ctx.get("partner_account_id"))
        _link_meeting_companies(ctx["meeting_id"], ctx.get("prospect_account_id"),
                                partner_ids)                            # ALL companies
        if write_item("meeting", ctx["meeting_id"], result.get("meeting", {}), cfg):
            changed.append(f"meeting:{ctx['meeting_id']}")
    if ctx.get("deal_id") and result.get("deal"):
        deal = dict(result["deal"])
        if deal.get("champion_name") is not None:
            champion_id = _champion_id(deal["champion_name"], ctx)
            if champion_id:
                deal["champion_name"] = champion_id
            else:
                deal.pop("champion_name", None)
                print("  ! champion name did not resolve unambiguously; skipped")
        if write_item("deal", ctx["deal_id"], deal, cfg):
            changed.append(f"deal:{ctx['deal_id']}")                 # deal = PROSPECT only
    if ctx.get("prospect_account_id") and result.get("account"):
        if write_item("account", ctx["prospect_account_id"], result["account"], cfg):
            changed.append(f"account:{ctx['prospect_account_id']}")
    for i, c in enumerate(result.get("contacts", [])):
        ids = ctx.get("contact_ids") or []
        email = str(c.get("email") or "").strip().lower()
        cid = ((ctx.get("contact_email_ids") or {}).get(email) or c.get("_id") or
               (ids[i] if i < len(ids) else None))
        if cid:
            if write_item("contact", cid, {
                k: v for k, v in c.items() if k not in ("_id", "email")},
                    cfg):
                changed.append(f"contact:{cid}")
    return {"records_changed": list(dict.fromkeys(changed))}


def _semantic_value(value):
    """Accept raw values or evidence wrappers without writing dict reprs."""
    if not isinstance(value, dict):
        return value
    status = str(value.get("status") or "").strip().upper()
    if status != "ANSWERED":
        return None
    return value.get("value") if "value" in value else None


def _number_value(value):
    """Return one monday-safe number; use the midpoint of an explicit range."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return f"{value:g}"
    raw = str(value or "").strip().lower().replace(",", "")
    if not raw:
        return None
    matches = re.findall(r"\d+(?:\.\d+)?\s*[km]?", raw)
    if not matches or len(matches) > 2:
        return None
    numbers = []
    for match in matches:
        unit = match[-1] if match[-1] in "km" else ""
        number = float(match[:-1] if unit else match)
        numbers.append(number * ({"k": 1_000, "m": 1_000_000}.get(unit, 1)))
    number = sum(numbers) / len(numbers)
    return f"{number:g}"


def _champion_id(value, ctx):
    """Resolve a champion only when the Contact relationship is unambiguous."""
    value = _semantic_value(value)
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        return str(value)
    contact_ids = [str(item) for item in (ctx.get("contact_ids") or [])]
    if len(contact_ids) == 1:
        return contact_ids[0]
    if not value or not contact_ids:
        return None
    q = "query($ids:[ID!]){items(ids:$ids){id name}}"
    items = _post(q, {"ids": contact_ids})["items"]
    wanted = str(value).strip().casefold()
    matches = [str(item["id"]) for item in items
               if (item.get("name") or "").strip().casefold() == wanted]
    return matches[0] if len(matches) == 1 else None


if __name__ == "__main__":
    apply(json.load(open(sys.argv[1])), json.load(open(sys.argv[2])))
    print("done")
