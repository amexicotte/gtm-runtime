"""Deterministic Won Deal -> Client Project -> Account lifecycle reconciliation."""
from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import is_active_client_type, is_completed_past_client_type


@dataclass
class EngagementStats:
    won_deals_scanned: int = 0
    projects_created_or_found: int = 0
    accounts_activated: int = 0
    accounts_completed: int = 0
    blocked: int = 0


def reconcile_won_engagements(crm, *, dry_run: bool = False) -> EngagementStats:
    """Make the post-sale lifecycle true without using transcript semantics.

    A Won Deal owns a Client Project. The Account is Active Client while any
    related project is active and Completed - Past Client once every related
    project is inactive. Re-running is relation-based and idempotent.
    """
    stats = EngagementStats()
    if not hasattr(crm, "won_deals_for_engagements"):
        return stats
    for deal in crm.won_deals_for_engagements():
        result = reconcile_won_engagement(crm, deal=deal, dry_run=dry_run)
        for key in vars(stats):
            setattr(stats, key, getattr(stats, key) + getattr(result, key))
    return stats


def reconcile_won_engagement(crm, *, deal: dict,
                             dry_run: bool = False) -> EngagementStats:
    """Reconcile one exact Won Deal without scanning unrelated Accounts."""
    stats = EngagementStats(won_deals_scanned=1)
    account_id = str(deal["account_id"])
    deal_id = str(deal["id"])
    if dry_run:
        return stats
    try:
        crm.ensure_client_project(
            account_id=account_id,
            deal_id=deal_id,
            contact_ids=[str(value) for value in deal.get("contact_ids") or []],
            name=deal.get("name") or crm.item_name(account_id) or "Client Project",
        )
    except RuntimeError as error:
        if "USER_UNAUTHORIZED" not in str(error):
            raise
        stats.blocked += 1
        if hasattr(crm, "flag_client_project_needed"):
            crm.flag_client_project_needed(deal_id)
        return stats
    stats.projects_created_or_found = 1
    projects = (crm.client_projects_for_account(account_id)
                if hasattr(crm, "client_projects_for_account") else [])
    current_type = crm.account_type(account_id) if hasattr(crm, "account_type") else ""
    if any(project.get("active") for project in projects):
        if not is_active_client_type(current_type):
            crm.set_account_active_client(account_id)
            stats.accounts_activated = 1
    elif projects and not is_completed_past_client_type(current_type):
        if not hasattr(crm, "set_account_completed_past_client"):
            raise RuntimeError(
                "CRM cannot mark an Account Completed - Past Client")
        crm.set_account_completed_past_client(account_id)
        stats.accounts_completed = 1
    return stats
