"""Pipeline orchestrator — one scheduled run does the whole flow in sequence.

No native Notion agents: this code is the orchestration. State lives in Notion
(`Processing Status`), so runs are idempotent and safe to repeat.

State machine per Meeting (Processing Status):
  (empty)      new Fireflies row → dedupe, match, then process or gate
  Gated        held for a human company answer in #deal-alerts → check for reply
  Processed    done → skip

CONVENTIONS (see runtime/README.md):
  #deal-alerts gate reply — reply IN-THREAD to the bot's question with the number,
    optionally "2 on behalf of <Company>".
  #call-notes — post notes with the client/company name (or a meeting-title word)
    in the message; matched to the most recent unprocessed meeting for it.

LIVE-VALIDATION NOTE: the I/O layers haven't been run against real credentials.
The Slack parsers (_attach_notes_pass, _resolve_gate) follow the conventions
above; verify against a real message/reply on first run. `--dry-run` performs no
writes / LLM / mutating Slack calls.
"""
from __future__ import annotations

import datetime as _dt
import re

from matcher import (CompanyMatch, classify_meeting, dedupe_meeting_rows,
                     parse_domains, GATE)

from . import notion as N
from .config import config
from .reasoning import extract
from .writes import plan_opportunity, new_tasks


def run(dry_run: bool = False, log=print) -> dict:
    nc = N.Notion()
    from .slack import Slack
    sc = Slack()
    stats = {"processed": 0, "gated": 0, "duplicates": 0,
             "notes_attached": 0, "skipped": 0}

    meetings = _load_unprocessed(nc)
    log(f"loaded {len(meetings)} unprocessed meeting(s)")

    # 1) collapse Fireflies duplicates; mark dropped ones Processed
    rows = [_row(m) for m in meetings]
    canonical, dup_groups = dedupe_meeting_rows(rows)
    canon_ids = {r["url"] for r in canonical}
    for group in dup_groups:
        for r in group:
            if r["url"] not in canon_ids:
                stats["duplicates"] += 1
                log(f"  duplicate → mark Processed: {r['title']}")
                if not dry_run:
                    nc.update_page(r["url"], {"Processing Status": N.select("Processed")})

    by_id = {m["id"]: m for m in meetings}
    canon_meetings = [by_id[r["url"]] for r in canonical]

    # 2) attach any Slack notes to their meetings (before reasoning)
    _attach_notes_pass(nc, sc, canon_meetings, stats, dry_run, log)

    # 3) process
    for m in canon_meetings:
        if stats["processed"] >= config.MAX_MEETINGS_PER_RUN:
            log(f"  hit MAX_MEETINGS_PER_RUN={config.MAX_MEETINGS_PER_RUN}; rest next run")
            break
        _process_one(nc, sc, m, stats, dry_run, log)

    log(f"done: {stats}")
    return stats


def _process_one(nc, sc, page, stats, dry_run, log):
    pid = page["id"]
    props = page.get("properties", {})
    title = N.read_text(props.get("Meeting title")) or "(untitled)"
    status = N.read_select(props.get("Processing Status"))
    invited = N.read_multi_select(props.get("Invited"))

    # --- determine the company (gate resolution, or fresh classification) ---
    if status == "Gated":
        company_id = _resolve_gate(nc, sc, page, invited, title, dry_run, log)
        if company_id is None:
            stats["skipped"] += 1
            log(f"  still gated: {title}")
            return
        meeting_type, create_domain = "External", None
    else:
        result = classify_meeting(invited, _lookup(nc), title=title)
        if result.decision == GATE:
            stats["gated"] += 1
            log(f"  GATE (ambiguous company): {title}")
            if not dry_run:
                sc.post(config.DEAL_ALERTS_CHANNEL, result.slack_prompt)
                nc.update_page(pid, {
                    "Internal/External": N.select(result.meeting_type),
                    "Processing Status": N.select("Gated"),
                })
            return
        company_id = result.company_url
        meeting_type = result.meeting_type
        create_domain = result.create_company_domain

    # --- create company if the matcher flagged it (cold start) ---
    if create_domain and not dry_run:
        company_id = _create_company(nc, create_domain)
    meeting_updates = {"Internal/External": N.select(meeting_type)}
    if company_id:
        meeting_updates["Company"] = N.relation([company_id])

    # --- assemble content: the Fireflies transcript syncs into the page body
    # (native integration toggle), alongside any appended ## Notes ---
    body_text = nc.block_children_text(pid) if not dry_run else ""
    content = N.meeting_content(page, body_text=body_text)
    meeting_date = _meeting_date(props)

    if dry_run:
        log(f"  DRY: would reason on '{title}' ({len(content)} chars)")
        stats["processed"] += 1
        return

    extracted = extract(content, title, meeting_date)

    if company_id:
        _enrich_opportunity(nc, company_id, extracted, meeting_date, log)
    _create_tasks(nc, extracted.get("tasks", []), company_id, pid, meeting_date, sc, log)

    nc.update_page(pid, {**meeting_updates, "Processing Status": N.select("Processed")})
    stats["processed"] += 1
    log(f"  processed '{title}'")


# --- notes intake -------------------------------------------------------------

def _attach_notes_pass(nc, sc, meetings, stats, dry_run, log):
    """Match #call-notes messages to meetings by company/title keyword and append
    to the meeting page body. Convention: the note text contains the client name
    or a distinctive word from the meeting title."""
    try:
        msgs = sc.history(config.CALL_NOTES_CHANNEL, oldest_ts=_days_ago_ts(3))
    except Exception as e:  # noqa: BLE001
        log(f"  notes pass skipped (Slack read failed): {e}")
        return
    # build keyword → meeting index from title words + linked company names
    for msg in msgs:
        text = (msg.get("text") or "").strip()
        if not text or msg.get("bot_id") or msg.get("subtype"):
            continue
        match = _match_meeting(text, meetings, nc)
        if not match:
            continue
        log(f"  note → {N.read_text(match['properties'].get('Meeting title'))}")
        stats["notes_attached"] += 1
        if not dry_run:
            nc.append_notes(match["id"], text)


def _match_meeting(text: str, meetings, nc) -> dict | None:
    low = text.lower()
    best = None
    for m in meetings:
        p = m.get("properties", {})
        title = N.read_text(p.get("Meeting title"))
        # distinctive title words (skip generic/common tokens)
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", title)
                 if w.lower() not in _STOP]
        if any(w.lower() in low for w in words):
            return m  # first confident title-word hit wins
    return best


_STOP = {"harbor", "fireflies", "weekly", "standup", "meeting", "call", "sync",
         "intro", "with", "zoom", "notetaker"}


# --- gate resolution ----------------------------------------------------------

def _resolve_gate(nc, sc, page, invited, title, dry_run, log) -> str | None:
    """Find the bot's gate question for this meeting in #deal-alerts, read the
    human's in-thread reply, and map the chosen number back to a company.
    Returns the company page id, or None if not yet answered."""
    try:
        msgs = sc.history(config.DEAL_ALERTS_CHANNEL, oldest_ts=_days_ago_ts(7))
    except Exception as e:  # noqa: BLE001
        log(f"  gate check skipped (Slack read failed): {e}")
        return None
    root = next((m for m in msgs if title in (m.get("text") or "")), None)
    if not root:
        return None
    replies = sc.replies(config.DEAL_ALERTS_CHANNEL, root["ts"])
    human = next((r for r in replies
                  if r.get("ts") != root["ts"] and not r.get("bot_id")), None)
    if not human:
        return None  # question posted, not answered yet

    reply = human.get("text") or ""
    choice = _first_int(reply)
    external = parse_domains(invited)["external"]  # same sorted order as the prompt
    if choice is None or not (1 <= choice <= len(external)):
        log(f"  gate reply not understood: {reply!r}")
        return None

    chosen_domain = external[choice - 1]
    company = nc.find_company_by_domain(chosen_domain)
    company_id = company["id"] if company else (
        None if dry_run else _create_company(nc, chosen_domain))
    if not company_id:
        return None

    if not dry_run:
        # learn-once: chosen = Client; the others = Partner (correct later if wrong)
        nc.update_page(company_id, {"Domain Role": N.select("Client")})
        for d in external:
            if d == chosen_domain:
                continue
            other = nc.find_company_by_domain(d)
            if other:
                updates = {"Domain Role": N.select("Partner")}
                behalf = _on_behalf(reply)
                if behalf:
                    updates["Domain Role"] = N.select("Investor")
                    updates["Represents"] = N.relation([company_id])
                nc.update_page(other["id"], updates)
    log(f"  gate resolved → {chosen_domain}")
    return company_id


def _first_int(text: str):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _on_behalf(text: str) -> str | None:
    m = re.search(r"on behalf of\s+(.+)", text or "", re.IGNORECASE)
    return m.group(1).strip() if m else None


# --- Notion helpers -----------------------------------------------------------

def _load_unprocessed(nc):
    return nc.query(config.DS_MEETINGS, filter_={
        "or": [
            {"property": "Processing Status", "select": {"is_empty": True}},
            {"property": "Processing Status", "select": {"does_not_equal": "Processed"}},
        ]
    })


def _row(m):
    p = m.get("properties", {})
    return {
        "url": m["id"],
        "title": N.read_text(p.get("Meeting title")),
        "date": (_meeting_date(p) or "")[:10],
        "invited": N.read_multi_select(p.get("Invited")),
        "attendees": N.read_multi_select(p.get("Attendees")),
        "transcript": (p.get("Transcript") or {}).get("url"),
        "created": m.get("created_time", ""),
    }


def _lookup(nc):
    def _fn(domain: str) -> CompanyMatch:
        page = nc.find_company_by_domain(domain)
        if not page:
            return CompanyMatch(domain=domain, exists=False)
        p = page.get("properties", {})
        represents = N.read_relation_ids(p.get("Represents"))
        return CompanyMatch(
            domain=domain, exists=True, company_url=page["id"],
            name=N.read_text(p.get("Company name")),
            domain_role=N.read_select(p.get("Domain Role")),
            represents_url=represents[0] if represents else None,
        )
    return _fn


def _create_company(nc, domain: str) -> str:
    name = domain.split(".")[0].capitalize()
    page = nc.create_page(config.DS_COMPANIES, {
        "Company name": N.title(name),
        "Domain": N.rich_text(domain),
        "Review Status": N.select("Needs Review"),
    })
    return page["id"]


def _enrich_opportunity(nc, company_id, extracted, meeting_date, log):
    opp = _find_or_none_opportunity(nc, company_id)
    if not opp:
        log("    no open opportunity for company; MEDDICC enrichment skipped")
        return
    p = opp.get("properties", {})
    verified = N.read_select(p.get("Review Status")) == "Verified"
    current = {
        "Expected revenue": N.read_number(p.get("Expected revenue")),
        "Service Type": N.read_text(p.get("Service Type")),
        "Stage": N.read_select(p.get("Stage")),
        **{name: N.read_text(p.get(name)) for name in
           ("Metrics", "Economic Buyer", "Decision Criteria", "Decision Process",
            "Identified Pain", "Champion", "Competition")},
    }
    plan = plan_opportunity(current, extracted.get("opportunity", {}),
                            verified=verified, meeting_date=meeting_date,
                            qualification_gaps=extracted.get("qualification_gaps"))
    if plan.skipped_verified:
        log("    opportunity is Verified — untouched")
        return
    updates = {}
    for prop, value in plan.set.items():
        if prop == "Expected revenue":
            updates[prop] = N.number(value)
        elif prop == "Stage":
            updates[prop] = N.select(value)
        else:
            updates[prop] = N.rich_text(str(value))
    if plan.gaps:
        updates["Qualification Gaps"] = N.rich_text("\n".join(plan.gaps))
    if plan.propose:
        existing = N.read_text(p.get("Proposed Changes"))
        updates["Proposed Changes"] = N.rich_text(
            (existing + "\n" + "\n".join(plan.propose)).strip())
    updates["Review Status"] = N.select("Needs Review")
    nc.update_page(opp["id"], updates)
    log(f"    opportunity enriched: {len(plan.set)} set, {len(plan.propose)} proposed")


def _find_or_none_opportunity(nc, company_id):
    opps = nc.query(config.DS_OPPORTUNITIES, filter_={
        "property": "Company", "relation": {"contains": company_id}
    })
    for o in opps:
        if N.read_select(o.get("properties", {}).get("Stage")) not in ("Completed", "Closed Lost"):
            return o
    return opps[0] if opps else None


def _create_tasks(nc, tasks, company_id, meeting_id, meeting_date, sc, log):
    existing = nc.find_tasks_for_meeting(meeting_id)
    existing_titles = {" ".join(N.read_text(t["properties"].get("Task")).lower().split())
                       for t in existing}
    for t in new_tasks(tasks, existing_titles):
        props = {
            "Task": N.title(t["title"]),
            "Task Type": N.select("chase" if t.get("type") == "chase" else "do-it"),
            "Source meeting": N.relation([meeting_id]),
            "Due date": N.date(_due(t.get("due_hint"), meeting_date)),
            "Review Status": N.select("Needs Review"),
        }
        if company_id:
            props["Company"] = N.relation([company_id])
        nc.create_page(config.DS_TASKS, props)
        log(f"    task created: {t['title']}")
        try:
            sc.post(config.DEAL_ALERTS_CHANNEL,
                    f"New task: {t['title']} ({t.get('type', 'do-it')})")
        except Exception as e:  # noqa: BLE001 - notify must never break the run
            log(f"    notify failed (non-fatal): {e}")


# --- misc ---------------------------------------------------------------------

def _meeting_date(props) -> str:
    d = props.get("Date") or {}
    return d.get("date", {}).get("start", "") if d.get("date") else ""


def _due(hint, meeting_date: str) -> str:
    if hint and len(hint) >= 10 and hint[4] == "-" and hint[7] == "-":
        return hint[:10]
    base = meeting_date[:10] if meeting_date else _dt.date.today().isoformat()
    try:
        d = _dt.date.fromisoformat(base)
    except ValueError:
        d = _dt.date.today()
    return (d + _dt.timedelta(days=7)).isoformat()


def _days_ago_ts(days: int) -> str:
    return str(int((_dt.datetime.now() - _dt.timedelta(days=days)).timestamp()))
