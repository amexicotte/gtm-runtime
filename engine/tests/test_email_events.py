"""monday E&A normalization, filtering, threading, and matching tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.email_events import (  # noqa: E402
    discover_missing_contacts, exclusion_reason, group_thread_messages,
    normalize_timeline_item, rank_activity_candidates, resolve_thread,
)


def raw(**changes):
    value = {
        "id": "mail-1", "type": "email", "title": "Re: Onsite prep",
        "created_at": "2026-08-17T12:00:00Z",
        "content": ("<p>Attached is the prep document: "
                    "<a href='https://docs.example/prep'>open</a></p>"
                    "<blockquote>old promise</blockquote><p>--</p><p>Signature</p>"),
        "metadata": {"from": "Alex <alex@harboradvisors.example>",
                     "to": ["Jane <jane@acme.com>"], "cc": [], "bcc": []},
    }
    value.update(changes)
    return value


def test_html_normalization_keeps_new_text_link_and_drops_quote_signature():
    message = normalize_timeline_item(raw())
    assert message.direction == "outbound"
    assert "Attached is the prep document" in message.body
    assert "old promise" not in message.body and "Signature" not in message.body
    assert message.links == ("https://docs.example/prep",)


def test_signature_links_are_not_delivery_evidence():
    message = normalize_timeline_item(raw(content=(
        "<p>I sent the document yesterday.</p><p>--</p>"
        "<p><a href='https://linkedin.example/profile'>LinkedIn</a></p>")))
    assert message.body == "I sent the document yesterday."
    assert message.links == ()

    generic_signoff = normalize_timeline_item(raw(content=(
        "<p>I sent it yesterday.</p><p>Best,<br>Alex</p>"
        "<p>Website: <a href='https://harboradvisors.example'>Harbor</a></p>")))
    assert "I sent it yesterday" in generic_signoff.body
    assert generic_signoff.links == ()

    leading_thanks = normalize_timeline_item(raw(content=(
        "<p>Thanks,</p><p>The SOW looks good—let’s proceed.</p>"), metadata={
            "from": "Jane <jane@acme.com>",
            "to": ["Alex <alex@harboradvisors.example>"],
        }))
    assert "SOW looks good" in leading_thanks.body


def test_internal_and_automated_email_is_excluded_deterministically():
    assert normalize_timeline_item(raw(metadata={
        "from": "Alex <alex@harboradvisors.example>",
        "to": ["Jordan <jordan@harboradvisors.example>"],
    })) is None
    automated = normalize_timeline_item(raw(
        metadata={"from": "no-reply@vendor.com", "to": ["alex@harboradvisors.example"]}))
    assert exclusion_reason(automated) == "automated_sender"


def test_api_activity_is_email_only_for_exact_bounded_verification_id():
    fixture = raw(
        type="custom", custom_activity_id="verify-activity",
        content=('HARBOR_VERIFY_EMAIL:{"from":"alex@harboradvisors.example",'
                 '"to":["jane@acme.com"]}\nFixture content.'), metadata=None,
    )
    assert normalize_timeline_item(fixture) is None
    assert normalize_timeline_item(
        fixture, verification_custom_activity_id="wrong") is None
    message = normalize_timeline_item(
        fixture, verification_custom_activity_id="verify-activity")
    assert message.direction == "outbound"
    assert message.external_emails == ("jane@acme.com",)


def test_malformed_verification_activity_summary_fails_closed():
    fixture = raw(
        type="activity", custom_activity_id="verify-activity",
        content="HARBOR_VERIFY_EMAIL:not-json\nFixture content.", metadata=None,
    )
    assert normalize_timeline_item(
        fixture, verification_custom_activity_id="verify-activity") is None


def test_thread_dedupe_and_multiple_deals_block_only_deal_target():
    first = normalize_timeline_item(raw())
    second = normalize_timeline_item(raw(
        id="mail-2", title="Onsite prep", created_at="2026-08-17T13:00:00Z",
        metadata={"from": "Jane <jane@acme.com>",
                  "to": ["Alex <alex@harboradvisors.example>"]},
        content="Thanks, I will review it."))
    first.observed_contact_ids.add("c1")
    second.observed_contact_ids.add("c1")
    groups = group_thread_messages([second, first])
    assert len(groups) == 1 and [row.timeline_id for row in groups[0]] == ["mail-1", "mail-2"]
    resolved = resolve_thread(
        groups[0],
        contact_index={"jane@acme.com": {
            "id": "c1", "account_ids": ["a1"], "deal_ids": ["d1", "d2"]}},
        deal_index={
            "d1": {"id": "d1", "active": True, "account_ids": ["a1"]},
            "d2": {"id": "d2", "active": True, "account_ids": ["a1"]},
        },
    )
    assert resolved.event.account_ids == ("a1",)
    assert resolved.event.contact_ids == ("c1",)
    assert resolved.event.deal_ids == ()
    assert "multiple plausible active Deals" in resolved.ambiguities[0]


def test_activity_candidates_are_lexically_bounded_and_delivery_aware():
    ranked = rank_activity_candidates(
        "Attached is the onsite prep document",
        [{"id": "t1", "title": "Send onsite prep document"},
         {"id": "t2", "title": "Book lunch"}],
    )
    assert [row["id"] for row in ranked] == ["t1"]
    assert ranked[0]["unique_match"] and ranked[0]["requires_delivery_evidence"]


def test_competing_activity_candidates_are_all_nonunique():
    ranked = rank_activity_candidates(
        "I sent the prep scope document",
        [{"id": "t1", "title": "Send prep document"},
         {"id": "t2", "title": "Send scope document"}],
    )
    assert len(ranked) == 2 and not any(row["unique_match"] for row in ranked)


def test_domain_fallback_resolves_account_and_ambiguity_blocks_deal():
    message = normalize_timeline_item(raw())
    resolved = resolve_thread(
        [message], contact_index={},
        account_index={"acme.com": {"id": "a1", "name": "Acme"}},
        deal_index={"d1": {"id": "d1", "active": True,
                            "account_ids": ["a1"]}},
    )
    assert resolved.event.account_ids == ("a1",)
    assert resolved.event.deal_ids == ("d1",)
    ambiguous = resolve_thread(
        [normalize_timeline_item(raw(metadata={
            "from": "Alex <alex@harboradvisors.example>",
            "to": ["Jane <jane@acme.com>", "Bob <bob@other.com>"]}))],
        contact_index={},
        account_index={"acme.com": {"id": "a1"}, "other.com": {"id": "a2"}},
        deal_index={"d1": {"id": "d1", "active": True,
                            "account_ids": ["a1"]}},
    )
    assert ambiguous.event.account_ids == () and ambiguous.event.deal_ids == ()

    freemail = resolve_thread(
        [normalize_timeline_item(raw(metadata={
            "from": "Alex <alex@harboradvisors.example>",
            "to": ["Jane <jane@gmail.com>"]}))],
        contact_index={}, account_index={"gmail.com": {"id": "bad"}},
        deal_index={},
    )
    assert freemail.event.account_ids == ()


def test_missing_participant_discovery_requires_exact_existing_account_domain():
    message = normalize_timeline_item(raw(metadata={
        "from": "Alex <alex@harboradvisors.example>",
        "to": ["Jane <jane@acme.com>", "Personal <person@gmail.com>",
               "Unknown <user@unknown.example>"],
    }))
    discoveries = discover_missing_contacts(
        [message],
        contact_index={"jane@acme.com": {"id": "c1"}},
        account_index={"acme.com": {"id": "a1"}},
        deal_index={"d1": {"id": "d1", "active": True,
                            "account_ids": ["a1"]}},
    )
    assert discoveries == ()

    message = normalize_timeline_item(raw(metadata={
        "from": "Alex <alex@harboradvisors.example>",
        "to": ["Jane <jane@acme.com>", "Bob Buyer <bob@acme.com>"],
    }))
    discoveries = discover_missing_contacts(
        [message],
        contact_index={"jane@acme.com": {"id": "c1"}},
        account_index={"acme.com": {"id": "a1"}},
        deal_index={"d1": {"id": "d1", "active": True,
                            "account_ids": ["a1"]}},
    )
    assert len(discoveries) == 1
    assert discoveries[0].email == "bob@acme.com"
    assert discoveries[0].name == "Bob Buyer"
    assert discoveries[0].account_id == "a1"
    assert discoveries[0].deal_ids == ("d1",)


def test_missing_participant_discovery_never_guesses_between_active_deals():
    message = normalize_timeline_item(raw(metadata={
        "from": "Alex <alex@harboradvisors.example>",
        "to": ["Bob Buyer <bob@acme.com>"],
    }))
    discoveries = discover_missing_contacts(
        [message], contact_index={},
        account_index={"acme.com": {"id": "a1"}},
        deal_index={
            "d1": {"id": "d1", "active": True, "account_ids": ["a1"]},
            "d2": {"id": "d2", "active": True, "account_ids": ["a1"]},
        },
    )
    assert len(discoveries) == 1
    assert discoveries[0].account_id == "a1"
    assert discoveries[0].deal_ids == ()


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
