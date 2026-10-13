"""Canonical, token-free CRM lifecycle semantics.

These values are business policy, not facts to infer from monday label names.
All routing, nudges, reporting, and deterministic writers import this module so
that a manual status-label edit cannot silently acquire new behavior.
"""
from __future__ import annotations


OPEN_DEAL_STAGES = ("New", "Discovery", "Proposal", "Negotiation")
PAUSED_DEAL_STAGES = ("Paused",)
WON_DEAL_STAGES = ("Won",)
LOST_DEAL_STAGES = ("Lost",)
DEAL_STAGE_LABELS = (
    *OPEN_DEAL_STAGES,
    *PAUSED_DEAL_STAGES,
    *WON_DEAL_STAGES,
    *LOST_DEAL_STAGES,
)

# The ordinary listening writer can move open Deals and explicitly close Lost.
# Paused and terminal stages remain human/contract controlled.
LISTENING_WRITABLE_DEAL_STAGES = (*OPEN_DEAL_STAGES, *LOST_DEAL_STAGES)

ACTIVE_CLIENT_TYPE = "Active Client"
COMPLETED_PAST_CLIENT_TYPE = "Completed - Past Client"
AI_INVESTMENT_TYPE = "AI Investment"
ACCOUNT_TYPE_LABELS = (
    ACTIVE_CLIENT_TYPE,
    "Partner",
    "Vendor",
    "Prospect",
    COMPLETED_PAST_CLIENT_TYPE,
    AI_INVESTMENT_TYPE,
)


def _normalized(value: object) -> str:
    return str(value or "").strip().casefold()


def _in(value: object, choices: tuple[str, ...]) -> bool:
    wanted = _normalized(value)
    return wanted in {_normalized(choice) for choice in choices}


def is_open_deal_stage(value: object) -> bool:
    return _in(value, OPEN_DEAL_STAGES)


def is_paused_deal_stage(value: object) -> bool:
    return _in(value, PAUSED_DEAL_STAGES)


def is_won_deal_stage(value: object) -> bool:
    return _in(value, WON_DEAL_STAGES)


def is_lost_deal_stage(value: object) -> bool:
    return _in(value, LOST_DEAL_STAGES)


def is_closed_deal_stage(value: object) -> bool:
    return is_won_deal_stage(value) or is_lost_deal_stage(value)


def is_routable_deal_stage(value: object) -> bool:
    return is_open_deal_stage(value) or is_paused_deal_stage(value)


def listening_may_change_stage(current: object) -> bool:
    """Only blank/open stages may be moved by meeting/email reasoning."""
    return not _normalized(current) or is_open_deal_stage(current)


def is_active_client_type(value: object) -> bool:
    # Customer is retained as a read-compatible legacy alias while writes use
    # the exact live label Active Client.
    return _normalized(value) in {
        _normalized(ACTIVE_CLIENT_TYPE), "customer",
    }


def is_completed_past_client_type(value: object) -> bool:
    # Past Client is accepted during the one-time label migration.
    return _normalized(value) in {
        _normalized(COMPLETED_PAST_CLIENT_TYPE), "past client",
    }
