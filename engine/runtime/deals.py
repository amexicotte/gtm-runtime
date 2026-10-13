"""Deterministic Deal resolution/creation after the single reasoning pass."""
from __future__ import annotations

from .evidence import evidence_problem
from .lifecycle import is_active_client_type, is_won_deal_stage


DEAL_CREATE_CALL_TYPES = {"Intro", "Sales"}
POST_SALE_CALL_TYPES = {"Client", "Project"}
def ensure_deal_from_result(crm, ctx: dict, result: dict, *,
                            meeting_title: str = "") -> str | None:
    """Resolve or create the prospect Deal when the call type warrants one."""
    if ctx.get("deal_id"):
        return str(ctx["deal_id"])
    account_id = str(ctx.get("prospect_account_id") or "")
    if not account_id:
        return None
    call_type = str(_value((result.get("meeting") or {}).get("call_type")) or "")
    preferred = (crm.preferred_deal_for_account(account_id)
                 if hasattr(crm, "preferred_deal_for_account") else None)
    active = (crm.active_deals_for_account(account_id)
              if hasattr(crm, "active_deals_for_account") else [])
    if len(active) > 1:
        raise RuntimeError(
            f"account {account_id} has multiple active Deals; refusing to guess")
    is_won = False
    if preferred:
        deal_id = str(preferred)
        all_deals = (crm.deals_for_account(account_id)
                     if hasattr(crm, "deals_for_account") else [])
        is_won = any(
            str(row.get("id")) == deal_id and
            is_won_deal_stage(row.get("stage"))
            for row in all_deals)
    elif active:
        deal_id = str(active[0]["id"])
    elif call_type in DEAL_CREATE_CALL_TYPES:
        deal_id = str(crm.create_deal(
            account_id=account_id,
            contact_ids=[str(value) for value in ctx.get("contact_ids") or []],
            meeting_id=str(ctx["meeting_id"]),
            stage="Discovery",
            name=crm.item_name(account_id) or meeting_title or "Untitled Deal",
        ))
    elif call_type in POST_SALE_CALL_TYPES:
        all_deals = (crm.deals_for_account(account_id)
                     if hasattr(crm, "deals_for_account") else [])
        won = [deal for deal in all_deals
               if is_won_deal_stage(deal.get("stage"))]
        if len(won) > 1:
            raise RuntimeError(
                f"account {account_id} has multiple Won Deals; refusing to guess")
        if won:
            deal_id = str(won[0]["id"])
            is_won = True
        else:
            # Won remains human/SOW-owned. A Client/Project classification may
            # reuse one exact Won Deal but can never manufacture one.
            return None
    else:
        return None
    crm.link_meeting_deal(str(ctx["meeting_id"]), deal_id)
    ctx["deal_id"] = deal_id
    account_is_client = (
        hasattr(crm, "account_type") and
        is_active_client_type(crm.account_type(account_id)))
    if ((call_type in POST_SALE_CALL_TYPES or account_is_client) and is_won and
            hasattr(crm, "ensure_client_project")):
        try:
            crm.ensure_client_project(
                account_id=account_id,
                deal_id=deal_id,
                contact_ids=[str(value) for value in ctx.get("contact_ids") or []],
                name=crm.item_name(account_id) or meeting_title or
                "Untitled Client Project",
            )
        except RuntimeError as error:
            # monday CRM can provision Client Projects as owner-edit-only. Keep
            # the confirmed Won Deal usable and surface the blocked delivery
            # diagnostic instead of stranding the Meeting.
            if "USER_UNAUTHORIZED" not in str(error):
                raise
            if hasattr(crm, "flag_client_project_needed"):
                crm.flag_client_project_needed(deal_id)
        else:
            if hasattr(crm, "set_account_active_client"):
                crm.set_account_active_client(account_id)
    return deal_id


def _value(value):
    if not isinstance(value, dict):
        return None
    if str(value.get("status") or "").strip().upper() != "ANSWERED":
        return None
    if evidence_problem(value):
        return None
    return value.get("value")
