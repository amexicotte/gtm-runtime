"""Matcher tests — all four classification branches + the gate + dedup.

Cases are built from REAL rows observed in the live Meetings DB on 2026-07-28,
so the tests double as a record of what production data actually looks like.

Run: python -m pytest tests/ -q   (or: python tests/test_matcher.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matcher import (  # noqa: E402
    CompanyMatch, classify_meeting, dict_lookup, dedupe_meeting_rows,
    parse_domains, title_core, PROCEED, GATE,
)


# --- domain parsing / drop-lists ---

def test_harbor_typo_domain_is_dropped():
    # Real Pebble onboarding row: andy@pebble.example + harbor + the harboradvisers typo.
    emails = ["andy@pebble.example", "sam@harboradvisors.example",
              "alex@harboradvisors.example", "alex@harboradvisers.example"]
    b = parse_domains(emails)
    assert b["external"] == ["pebble.example"]      # exactly one external
    assert b["harbor"] == ["harboradvisers.example", "harboradvisors.example"]  # both dropped


def test_notetaker_and_freemail_bucketed():
    emails = ["fred@fireflies.ai", "clay@brightreach.example",
              "pat.contractor@gmail.com", "alex@harboradvisors.example"]
    b = parse_domains(emails)
    assert b["external"] == ["brightreach.example"]
    assert b["notetaker"] == ["fireflies.ai"]
    assert b["freemail"] == ["gmail.com"]


# --- branch 1: single external domain ---

def test_single_external_existing_company_links():
    # Corvid standup: one external domain corvid.example, company exists.
    emails = ["sandra@corvid.example", "max@corvid.example",
              "alex@harboradvisors.example", "jordan@harboradvisors.example"]
    lookup = dict_lookup({
        "corvid.example": CompanyMatch("corvid.example", exists=True,
                                  company_url="notion://corvid", name="Corvid"),
    })
    r = classify_meeting(emails, lookup, title="Corvid x Harbor Weekly Standup")
    assert r.meeting_type == "External"
    assert r.decision == PROCEED
    assert r.company_url == "notion://corvid"


def test_single_external_missing_company_flags_create():
    emails = ["cfr@sixty.example", "sam@harboradvisors.example"]
    lookup = dict_lookup({})   # sixty.example not in CRM yet
    r = classify_meeting(emails, lookup, title="Meeting w/ Christopher Ryan")
    assert r.decision == PROCEED
    assert r.create_company_domain == "sixty.example"
    assert r.company_url is None


def test_company_comes_from_email_domain_not_meeting_title():
    # A Lumen call can be titled for the tool being configured (Attio).
    emails = ["alex@harboradvisors.example", "customer@lumenhq.example"]
    lookup = dict_lookup({
        "lumenhq.example": CompanyMatch(
            "lumenhq.example", exists=True,
            company_url="monday://lumen", name="Lumen",
        ),
    })
    r = classify_meeting(emails, lookup, title="Attio Set Up Alignment")
    assert r.decision == PROCEED
    assert r.external_domains == ["lumenhq.example"]
    assert r.company_url == "monday://lumen"


# --- branch 2: all internal ---

def test_all_internal_is_gated_without_reasoning():
    emails = ["alex@harboradvisors.example", "sam@harboradvisors.example",
              "jordan@harboradvisors.example"]
    r = classify_meeting(emails, dict_lookup({}), title="Harbor internal sync")
    assert r.meeting_type == "Internal"
    assert r.decision == GATE


def test_unity_treated_internal_adjacent_and_gated():
    # Unity is the parent PE firm; an Harbor<>Unity call is not a fresh prospect.
    emails = ["hans@ridgeline.example", "alex@harboradvisors.example"]
    r = classify_meeting(emails, dict_lookup({}), title="Harbor / Unity")
    assert r.meeting_type == "Internal"
    assert r.decision == GATE


# --- branch 3: free-mail only external ---

def test_freemail_only_external_is_gated():
    emails = ["someone@gmail.com", "alex@harboradvisors.example"]
    r = classify_meeting(emails, dict_lookup({}), title="Intro call")
    assert r.meeting_type == "External"
    assert r.decision == GATE
    assert r.company_url is None


# --- branch 4: ambiguous multi-domain → THE GATE ---

def test_ambiguous_gate_when_roles_unknown():
    emails = ["partner@acme.com", "gp@summitcapital.example", "alex@harboradvisors.example"]
    lookup = dict_lookup({
        "acme.com": CompanyMatch("acme.com", exists=True,
                                 company_url="notion://acme", name="Acme"),
        "summitcapital.example": CompanyMatch("summitcapital.example", exists=True,
                                        company_url="notion://summit", name="SummitCapital"),
    })
    r = classify_meeting(emails, lookup, title="Q3 Strategy")
    assert r.decision == GATE
    assert r.company_url is None          # THE GATE: nothing linked until human replies
    assert "Q3 Strategy" in r.slack_prompt
    assert "Acme" in r.slack_prompt and "SummitCapital" in r.slack_prompt


def test_known_roles_still_require_human_prospect_pick():
    # Roles describe the companies, not their role in this particular meeting.
    emails = ["ceo@portfolioco.com", "gp@investor.com", "alex@harboradvisors.example"]
    lookup = dict_lookup({
        "portfolioco.com": CompanyMatch("portfolioco.com", exists=True,
                                        company_url="notion://pco", name="PortfolioCo",
                                        domain_role="Client"),
        "investor.com": CompanyMatch("investor.com", exists=True,
                                     company_url="notion://inv", name="Investor",
                                     domain_role="Investor",
                                     represents_url="notion://pco"),
    })
    r = classify_meeting(emails, lookup, title="Board sync")
    assert r.decision == GATE
    assert r.company_url is None
    assert "PortfolioCo" in r.slack_prompt and "Investor" in r.slack_prompt


# --- Fireflies duplicate-row collapse ---

def test_dedupe_collapses_same_title_same_day():
    # Corvid standup appeared 3x — same title-core + date, different transcripts.
    inv = ["sandra@corvid.example", "jordan@harboradvisors.example", "max@corvid.example",
           "alex@harboradvisors.example", "sam@harboradvisors.example"]
    rows = [
        {"url": "n://1", "title": "Corvid x Harbor Weekly Standup - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": inv, "transcript": "ff://a", "created": "2026-07-27 18:05:45Z"},
        {"url": "n://2", "title": "Corvid x Harbor Weekly Standup - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": inv, "transcript": "ff://b", "created": "2026-07-27 18:04:44Z"},
        {"url": "n://3", "title": "Corvid x Harbor Weekly Standup - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": inv, "transcript": "ff://c", "created": "2026-07-27 17:26:27Z"},
    ]
    canonical, dups = dedupe_meeting_rows(rows)
    assert len(canonical) == 1
    assert len(dups) == 1 and len(dups[0]) == 3


def test_dedupe_collapses_same_invites_different_title():
    # "Sam / Clay / Joey" vs "...+ Alex": titles differ, invited set identical.
    inv = ["clay@brightreach.example", "pat.contractor@gmail.com",
           "sam@harboradvisors.example", "alex@harboradvisors.example"]
    rows = [
        {"url": "n://a", "title": "Sam / Clay / Joey / Alex - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": inv, "transcript": "ff://x"},
        {"url": "n://b", "title": "Sam / Clay / Joey - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": list(reversed(inv)), "transcript": None},
    ]
    canonical, dups = dedupe_meeting_rows(rows)
    assert len(canonical) == 1
    # canonical prefers the row WITH a transcript
    assert canonical[0]["url"] == "n://a"


def test_dedupe_keeps_distinct_single_attendee_background_recordings():
    rows = [
        {"url": "n://a", "title": "Partner recruiting", "date": "2026-08-05",
         "invited": ["alex@harboradvisors.example"], "transcript": "ff://a"},
        {"url": "n://b", "title": "Beacon recruiting", "date": "2026-08-05",
         "invited": ["alex@harboradvisors.example"], "transcript": "ff://b"},
    ]
    canonical, duplicates = dedupe_meeting_rows(rows)
    assert {row["url"] for row in canonical} == {"n://a", "n://b"}
    assert duplicates == []


def test_dedupe_keeps_distinct_meetings_apart():
    rows = [
        {"url": "n://1", "title": "Acme Kickoff - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": ["x@acme.com", "alex@harboradvisors.example"]},
        {"url": "n://2", "title": "Beta Intro - Jul 27, 2026 (Fireflies)",
         "date": "2026-07-27", "invited": ["y@beta.com", "alex@harboradvisors.example"]},
    ]
    canonical, dups = dedupe_meeting_rows(rows)
    assert len(canonical) == 2
    assert dups == []


def test_title_core_strips_ff_and_date():
    assert title_core("Corvid x Harbor Weekly Standup - Jul 27, 2026 (Fireflies)") == \
        "corvid x harbor weekly standup"


# --- allow running without pytest ---
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
