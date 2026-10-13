"""Safety tests for semantic normalization in the monday writer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import reasoning.writer as writer  # noqa: E402
from reasoning.writer import (  # noqa: E402
    _champion_id,
    _number_value,
    _semantic_value,
    fmt,
)


def test_long_text_lists_render_as_readable_bullets():
    assert fmt(["First", "Second"], "long_text") == "• First\n• Second"


def test_evidence_wrapper_unwraps_answered_value():
    assert _semantic_value({"status": "ANSWERED", "value": "Pain", "evidence": "quote"}) == "Pain"


def test_unknown_or_partial_wrapper_is_not_written():
    assert _semantic_value({"status": "NOT_DISCUSSED", "value": None}) is None
    assert _semantic_value({"status": "PARTIAL", "value": "maybe"}) is None


def test_single_contact_makes_champion_relationship_unambiguous():
    assert _champion_id("Jane Smith", {"contact_ids": ["123"]}) == "123"


def test_invalid_relation_value_is_skipped_instead_of_crashing():
    assert fmt("Jane Smith", "board_relation") is None


def test_numeric_range_uses_midpoint_accepted_by_monday():
    assert _number_value("$9,000-$11,000") == "10000"
    assert _number_value("$5k–$7k/mo") == "6000"
    assert fmt("9000-11000", "numeric") == "10000"


def test_existing_value_is_overwritten_and_marked_final():
    columns = {
        "Notes": {"id": "notes", "type": "long_text"},
        "Review Status": {"id": "review", "type": "status"},
        "Proposed Changes": {"id": "proposed", "type": "long_text"},
        "Agent Field Provenance": {"id": "provenance", "type": "long_text"},
    }
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        writer.write_item(
            "account", "a1", {"account_notes": "New call context"},
            {"account": {"account_notes": {"column": "Notes"}}},
        )
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    values = __import__("json").loads(calls[0]["v"])
    assert values["notes"] == "New call context"
    assert values["proposed"] == ""
    assert values["review"] == {"label": "Verified"}


def test_later_call_can_update_a_previously_verified_record():
    columns = {
        "Notes": {"id": "notes", "type": "long_text"},
        "Review Status": {"id": "review", "type": "status"},
        "Proposed Changes": {"id": "proposed", "type": "long_text"},
    }
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        writer.write_item(
            "account", "a1", {"account_notes": "New"},
            {"account": {"account_notes": {"column": "Notes"}}},
        )
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    values = __import__("json").loads(calls[0]["v"])
    assert values["notes"] == "New"
    assert values["review"] == {"label": "Verified"}


def test_required_evidence_blocks_unsupported_write():
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: {
            "Deal Value": {"id": "deal_value", "type": "numbers"}}
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        writer.write_item("deal", "d1", {"deal_value": 9000}, {
            "deal": {"deal_value": {
                "column": "Deal Value", "policy": "overwrite", "evidence": "required",
            }},
        })
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert calls == []


def test_executed_contract_provenance_blocks_later_listening_overwrite():
    columns = {
        "Deal Value": {"id": "deal_value", "type": "numbers"},
        "Agent Field Provenance": {"id": "provenance", "type": "long_text"},
    }
    mutations = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        def post(query, variables=None):
            if query.startswith("query"):
                return {"items": [{"column_values": [{
                    "id": "provenance", "text": (
                        '{"deal_value":{"occurred_at":"2026-08-23T00:00:00+00:00",'
                        '"source_type":"docusign","authoritative":true}}'),
                }]}]}
            mutations.append(variables)
            return {}
        writer._post = post
        changed = writer.write_item("deal", "d1", {"deal_value": {
            "status": "ANSWERED", "value": 30000,
            "evidence": {"excerpt": "new contract value", "confidence": 0.99,
                         "source_id": "email-1",
                         "occurred_at": "2026-08-24T00:00:00+00:00"},
        }}, {"deal": {"deal_value": {
            "column": "Deal Value", "policy": "overwrite", "evidence": "required",
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert changed is False
    assert mutations == []


def test_bounded_stage_cannot_write_won():
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: {
            "Stage": {"id": "stage", "type": "status"}}
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        writer.write_item("deal", "d1", {"stage": {
            "status": "ANSWERED", "value": "Won",
            "evidence": {"excerpt": "we signed", "confidence": 0.99,
                         "source_id": "m1", "occurred_at": "2026-08-17T00:00:00+00:00"},
        }}, {"deal": {"stage": {
            "column": "Stage", "policy": "bounded", "evidence": "required",
            "allowed_values": ["New", "Discovery", "Proposal", "Negotiation", "Lost"],
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert calls == []


def test_paused_stage_cannot_be_automatically_reopened():
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: {
            "Stage": {"id": "stage", "type": "status"}}
        def post(query, variables=None):
            calls.append((query, variables))
            if query.startswith("query"):
                return {"items": [{"column_values": [
                    {"id": "stage", "text": "Paused"},
                ]}]}
            return {}
        writer._post = post
        changed = writer.write_item("deal", "d1", {"stage": {
            "status": "ANSWERED", "value": "Proposal",
            "evidence": {"excerpt": "proposal", "confidence": 0.99,
                         "source_id": "m1",
                         "occurred_at": "2026-08-24T00:00:00+00:00"},
        }}, {"deal": {"stage": {
            "column": "Stage", "policy": "bounded", "evidence": "required",
            "allowed_values": ["New", "Discovery", "Proposal", "Negotiation", "Lost"],
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert changed is False
    assert len(calls) == 1 and calls[0][0].startswith("query")


def test_merge_policy_appends_new_durable_context_without_repeating_history():
    columns = {
        "Notes": {"id": "notes", "type": "long_text"},
        "Review Status": {"id": "review", "type": "status"},
        "Proposed Changes": {"id": "proposed", "type": "long_text"},
        "Agent Field Provenance": {"id": "provenance", "type": "long_text"},
    }
    mutations = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns

        def post(query, variables=None):
            if query.startswith("query"):
                return {"items": [{"column_values": [
                    {"id": "notes", "text": "Existing durable fact"},
                    {"id": "provenance", "text": "{}"},
                ]}]}
            mutations.append(variables)
            return {}

        writer._post = post
        writer.write_item("account", "a1", {"account_notes": {
            "status": "ANSWERED", "value": "New durable fact",
            "evidence": {"excerpt": "new fact", "confidence": 0.9,
                         "source_id": "m1", "occurred_at": "2026-08-17T00:00:00+00:00"},
        }}, {"account": {"account_notes": {
            "column": "Notes", "policy": "merge", "evidence": "required",
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    values = __import__("json").loads(mutations[0]["v"])
    assert values["notes"] == "Existing durable fact\nNew durable fact"


def test_stale_event_cannot_overwrite_newer_accepted_field():
    columns = {
        "Notes": {"id": "notes", "type": "long_text"},
        "Agent Field Provenance": {"id": "provenance", "type": "long_text"},
    }
    mutations = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        def post(query, variables=None):
            if query.startswith("query"):
                return {"items": [{"column_values": [{
                    "id": "provenance", "text": (
                        '{"account_notes":{"occurred_at":'
                        '"2026-08-18T00:00:00+00:00","source_id":"new"}}'),
                }]}]}
            mutations.append(variables)
            return {}
        writer._post = post
        changed = writer.write_item("account", "a1", {"account_notes": {
            "status": "ANSWERED", "value": "Old delayed fact",
            "evidence": {"excerpt": "old", "confidence": .99,
                         "source_id": "old",
                         "occurred_at": "2026-08-17T00:00:00+00:00"},
        }}, {"account": {"account_notes": {
            "column": "Notes", "policy": "overwrite", "evidence": "required",
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert changed is False and mutations == []


def test_enum_write_cannot_create_model_supplied_labels():
    columns = {
        "Source": {"id": "source", "type": "status"},
        "Agent Field Provenance": {"id": "provenance", "type": "long_text"},
    }
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        writer._post = lambda query, variables=None: (
            {"items": [{"column_values": [{"id": "provenance", "text": "{}"}]}]}
            if query.startswith("query") else calls.append((query, variables)) or {})
        writer.write_item("account", "a1", {"source": {
            "status": "ANSWERED", "value": "Referral",
            "evidence": {"excerpt": "referred", "confidence": .99,
                         "source_id": "m1",
                         "occurred_at": "2026-08-17T00:00:00+00:00"},
        }}, {"account": {"source": {
            "column": "Source", "type": "status", "policy": "explicit_only",
            "allowed_values": ["Referral", "Inbound"], "evidence": "required",
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert len(calls) == 1 and "create_labels_if_missing:false" in calls[0][0]


def test_bounded_dropdown_accepts_only_declared_label_lists():
    columns = {"Service Type": {"id": "service", "type": "dropdown"}}
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        writer.write_item("deal", "d1", {"service_type": ["Project", "RevOps"]}, {
            "deal": {"service_type": {
                "column": "Service Type", "type": "dropdown",
                "policy": "explicit_only", "allowed_values": ["Project", "RevOps"],
            }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    values = __import__("json").loads(calls[0]["v"])
    assert values["service"] == {"labels": ["Project", "RevOps"]}


def test_pre_schema_meeting_writer_remains_backward_compatible():
    columns = {"Summary": {"id": "summary", "type": "long_text"}}
    calls = []
    original_columns, original_post = writer.live_columns, writer._post
    try:
        writer.live_columns = lambda board: columns
        writer._post = lambda query, variables=None: calls.append(variables) or {}
        assert writer.write_item("meeting", "m1", {"summary": {
            "status": "ANSWERED", "value": "Call summary",
            "evidence": {"excerpt": "summary", "confidence": .99,
                         "source_id": "m1",
                         "occurred_at": "2026-08-17T00:00:00+00:00"},
        }}, {"meeting": {"summary": {
            "column": "Summary", "type": "long_text",
            "policy": "overwrite", "evidence": "required",
        }}})
    finally:
        writer.live_columns, writer._post = original_columns, original_post
    assert len(calls) == 1


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
