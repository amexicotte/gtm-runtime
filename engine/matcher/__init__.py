"""Deterministic GTM matcher package."""
from .domains import parse_domains, domain_of, classify_domain
from .matcher import (
    CompanyMatch,
    MatchResult,
    classify_meeting,
    dict_lookup,
    dedupe_meeting_rows,
    title_core,
    PROCEED,
    GATE,
    FLAG_NO_COMPANY,
)

__all__ = [
    "parse_domains", "domain_of", "classify_domain",
    "CompanyMatch", "MatchResult", "classify_meeting", "dict_lookup",
    "dedupe_meeting_rows", "title_core",
    "PROCEED", "GATE", "FLAG_NO_COMPANY",
]
