"""Tests for live-title schema adaptation in the monday client."""
import os
import sys
import json
import hashlib
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.monday import (  # noqa: E402
    BOARDS, Monday, _decode_payload, _encode_payload, _json_object, _raw_text,
)
from runtime.config import config  # noqa: E402


def client():
    crm = Monday(token="test")
    crm._columns = {
        "account": {
            "Domain": {"id": "link", "type": "link"},
            "Domain (match key)": {"id": "key", "type": "text"},
            "Contacts": {"id": "contacts", "type": "board_relation"},
            "Relationship Owner": {"id": "owner", "type": "people"},
            "Type": {"id": "type", "type": "status"},
            "Review Status": {"id": "review", "type": "status"},
        },
        "contact": {
            "Email": {"id": "email", "type": "email"},
            "Accounts": {"id": "accounts", "type": "board_relation"},
            "Review Status": {"id": "review", "type": "status"},
        },
        "deal": {
            "Accounts": {"id": "mirror", "type": "mirror"},
            "Contacts": {"id": "contacts", "type": "board_relation"},
            "Owner": {"id": "owner", "type": "people"},
            "Stage": {"id": "stage", "type": "status"},
            "Review Status": {"id": "review", "type": "status"},
        },
    }
    return crm


def test_account_uses_match_key_and_populates_enrichment_link():
    crm = client()
    created = []
    crm.create_item = lambda board, name, values: created.append((board, name, values)) or "9"
    assert crm.create_account("acme.com") == "9"
    assert created == [("account", "Acme", {
        "Domain (match key)": "acme.com", "Domain": "https://acme.com",
        "Relationship Owner": ["100000007"], "Type": "Prospect",
        "Review Status": "Verified",
    })]


def test_contact_uses_plural_native_account_relation():
    crm = client()
    created = []
    crm.create_item = lambda board, name, values: created.append((board, name, values)) or "10"
    crm.create_contact("jane@acme.com", "9")
    assert created[0][2]["Accounts"] == ["9"]


def test_contact_creation_can_link_one_exact_deal():
    crm = client()
    crm._columns["contact"]["Deals"] = {
        "id": "contact_deals", "type": "board_relation"}
    created = []
    crm.create_item = lambda board, name, values: created.append(
        (board, name, values)) or "10"
    crm.create_contact("bob@acme.com", "9", deal_ids=["d1"])
    assert created[0][2]["Accounts"] == ["9"]
    assert created[0][2]["Deals"] == ["d1"]


def test_contact_deal_link_merges_without_dropping_existing_contacts():
    crm = client()
    calls = []
    crm._post = lambda query, variables=None: {
        "items": [{"column_values": [{"linked_item_ids": ["c1"]}]}]}
    crm.change_columns = lambda board, item, values, **kwargs: calls.append(
        (board, item, values))
    crm.link_contacts_to_deal(["c2"], "d1")
    assert calls == [("deal", "d1", {"Contacts": ["c1", "c2"]})]


def test_deal_resolution_uses_account_name_when_account_is_mirror():
    crm = client()
    crm.active_deals_for_account = lambda account: [
        {"id": "d1", "name": "Lumen", "stage": "Discovery", "owner_ids": []}]
    assert crm.find_deal_for_account("a1") == "d1"


def test_new_deal_populates_editable_account_relation():
    crm = client()
    crm._columns["deal"]["Account"] = {
        "id": "account", "type": "board_relation"}
    crm._columns["deal"]["link to Meetings"] = {
        "id": "meetings", "type": "board_relation"}
    created = []
    crm.create_item = lambda board, name, values: (
        created.append((board, name, values)) or "d1")
    assert crm.create_deal(
        account_id="a1", contact_ids=["c1"], meeting_id="m1",
        stage="Discovery", name="Acme") == "d1"
    assert created[0][2]["Account"] == ["a1"]
    assert created[0][2]["Review Status"] == "Verified"


def test_creation_log_schema_is_date_only_and_system_managed():
    crm = client()
    crm._columns["deal"] = {}
    calls = []

    def post(query, variables=None):
        calls.append((query, variables))
        return {"create_column": {
            "id": "creation_log", "title": "Created Date",
            "type": "creation_log", "settings": {"mode": "dateOnly"},
        }}

    crm._post = post
    column = crm.ensure_creation_log_column("deal")
    assert column["type"] == "creation_log"
    assert "column_type:creation_log" in calls[0][0]
    assert calls[0][1]["defaults"] == json.dumps({"mode": "dateOnly"})

    crm._columns["deal"] = {
        "Created Date": {"id": "wrong", "type": "date"}}
    try:
        crm.ensure_creation_log_column("deal")
    except RuntimeError as error:
        assert "expected creation_log" in str(error)
    else:
        raise AssertionError("creation-log type collision must fail closed")


def test_new_activity_is_final_on_creation():
    crm = client()
    crm.ensure_column = lambda *args, **kwargs: None
    created = []
    crm.create_item = lambda board, name, values: (
        created.append((board, name, values)) or "t1")
    crm.create_activity(
        title="Send scope", task_type="Do it", due_date="2026-08-20",
        owner_ids=["100000007"], related_ids=["a1"],
        source_meeting_id="m1", slack_link="", notes="From meeting",
    )
    assert created[0][2]["Review Status"] == "Verified"


def test_client_project_fallback_writes_relations_not_human_managed_fields():
    crm = client()
    crm.client_project_for_deal = lambda account, deal: None
    created = []
    crm.create_item = lambda board, name, values: (
        created.append((board, name, values)) or "p1")
    assert crm.ensure_client_project(
        account_id="a1", deal_id="d1", contact_ids=["c1"], name="Acme") == "p1"
    assert created == [("client_project", "Acme", {
        "Deals": ["d1"], "Accounts": ["a1"], "Contact": ["c1"],
    })]


def test_completed_client_project_is_reused_instead_of_recreated():
    crm = client()
    crm.client_project_for_deal = lambda account, deal: {
        "id": "p-done", "active": False, "deal_ids": [deal],
    }
    crm.create_item = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("completed project must not be recreated"))
    assert crm.ensure_client_project(
        account_id="a1", deal_id="d1", contact_ids=["c1"], name="Acme",
    ) == "p-done"


def test_narrative_update_writer_and_marker_reader_use_native_updates_api():
    crm = client()
    calls = []

    def post(query, variables=None):
        calls.append((query, variables))
        if "create_update" in query:
            return {"create_update": {"id": "u1"}}
        return {"items": [{"updates": [{"id": "u1", "body": "marker-123"}]}]}

    crm._post = post
    assert crm.post_narrative_update("a1", "<strong>News</strong>") == "u1"
    assert crm.update_has_event_marker("a1", "marker-123")
    assert calls[0][1]["item"] == "a1"


def test_email_timeline_reader_uses_supported_root_query_and_metadata():
    crm = client()
    def post(query, variables=None):
        assert query.count("{") == query.count("}")
        assert "skipConnectedItems:true" in query
        assert "EmailTimelineItemMetadata" in query
        assert "custom_activity_id" in query
        assert "summary" not in query
        return {"timeline": {"timeline_items_page": {
            "cursor": "next", "timeline_items": [{"id": "mail-1"}],
        }}}
    crm._post = post
    assert crm.email_timeline_items("c1") == {
        "cursor": "next", "items": [{"id": "mail-1"}]}


def test_bounded_crm_context_returns_only_allowlisted_fields():
    crm = client()
    crm._columns["deal"]["Notes"] = {"id": "notes", "type": "long_text"}
    crm._columns["deal"]["Secret"] = {"id": "secret", "type": "text"}
    def post(query, variables=None):
        assert variables["ids"] == ["d1"]
        return {"items": [{
            "id": "d1", "name": "Acme", "board": {"id": str(BOARDS["deal"])},
            "column_values": [
                {"id": "stage", "text": "Discovery"},
                {"id": "notes", "text": "Current context"},
                {"id": "secret", "text": "must not escape"},
            ],
        }]}
    crm._post = post
    assert crm.bounded_crm_context(["d1"]) == [{
        "id": "d1", "type": "deal", "name": "Acme",
        "fields": {"Stage": "Discovery", "Notes": "Current context"},
    }]


def test_recent_updates_reader_caps_body_and_uses_exact_ids():
    crm = client()
    def post(query, variables=None):
        assert variables == {"ids": ["d1"], "limit": 2}
        return {"items": [{"id": "d1", "name": "Acme", "updates": [{
            "id": "u1", "created_at": "2026-08-19T00:00:00Z",
            "body": "x" * 9000,
        }]}]}
    crm._post = post
    result = crm.recent_updates_for_items(["d1"], limit=2)
    assert len(result[0]["updates"][0]["body"]) == 8000


def test_pattern_brief_read_is_readonly_and_requires_configured_private_board():
    crm = client()
    original = BOARDS.pop("agent_event", None)
    try:
        try:
            crm.deal_pattern_brief_state()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as error:
            assert "MONDAY_AGENT_EVENTS_BOARD_ID" in str(error)
    finally:
        if original is not None:
            BOARDS["agent_event"] = original


def test_pattern_brief_save_targets_only_private_state_row():
    crm = client()
    BOARDS["agent_event"] = 999
    crm._agent_events_ready = True
    crm._items_named = lambda board, name: []
    created = []
    crm.create_item = lambda board, name, values: (
        created.append((board, name, values)) or "state-1")
    state = {"generated_at": "2026-08-19T00:00:00Z",
             "input_sha256": "abc", "brief": {"executive_summary": "Patterns"}}
    assert crm.save_deal_pattern_brief_state(state) == "state-1"
    assert created[0][0:2] == ("agent_event", "state:deal-pattern-brief")
    assert json.loads(created[0][2]["Effects/Audit JSON"])["deal_writes"] == 0


def test_terminal_deal_portfolio_reads_only_won_and_lost_with_updates():
    crm = client()
    calls = []
    def post(query, variables=None):
        calls.append(query)
        if "items_page" in query:
            return {"boards": [{"items_page": {"cursor": None, "items": [
                {"id": "d1", "name": "Won deal", "created_at": "2026-01-01",
                 "updated_at": "2026-02-01", "column_values": [
                     {"id": "stage", "text": "Won", "linked_item_ids": []}]},
                {"id": "d2", "name": "Open deal", "created_at": "2026-01-01",
                 "updated_at": "2026-02-01", "column_values": [
                     {"id": "stage", "text": "Discovery", "linked_item_ids": []}]},
            ]}}]}
        return {"items": [{"id": "d1", "name": "Won deal", "updates": [{
            "id": "u1", "created_at": "2026-02-01", "body": "Won context"}]}]}
    crm._post = post
    result = crm.terminal_deal_portfolio(max_deals=10, updates_limit=3)
    assert [row["id"] for row in result] == ["d1"]
    assert result[0]["facts"]["Stage"] == "Won"
    assert result[0]["updates"][0]["body"] == "Won context"
    assert all("mutation" not in query for query in calls)


def test_datetime_column_values_are_written_in_crm_timezone():
    crm = client()
    assert crm._format("2026-08-18T02:30:00+00:00", "date") == {
        "date": "2026-08-17", "time": "22:30:00"}


def test_multiple_active_deals_are_never_guessed():
    crm = client()
    crm.active_deals_for_account = lambda account: [
        {"id": "d1"}, {"id": "d2"}]
    assert crm.find_deal_for_account("a1") is None


def test_active_deal_live_query_is_balanced_and_matches_mirror():
    crm = client()
    crm.item_name = lambda item_id: "Acme"

    def post(query, variables=None):
        assert query.count("{") == query.count("}")
        return {"boards": [{"items_page": {"cursor": None, "items": [{
            "id": "d1", "name": "Acme Discovery", "column_values": [
                {"id": "mirror", "text": "", "value": None,
                 "display_value": "Acme"},
                {"id": "stage", "text": "Discovery", "value": None},
                {"id": "owner", "text": "", "value": None},
            ],
        }]}}]}

    crm._post = post
    assert crm.active_deals_for_account("a1") == [{
        "id": "d1", "name": "Acme Discovery", "stage": "Discovery",
        "owner_ids": [],
    }]


def test_relation_lookup_live_query_is_balanced():
    crm = client()

    def post(query, variables=None):
        assert query.count("{") == query.count("}")
        return {"boards": [{"items_page": {"cursor": None, "items": [{
            "id": "c1", "column_values": [{"linked_item_ids": ["a1"]}],
        }]}}]}

    crm._post = post
    assert crm._find_item_with_relation("contact", "Accounts", {"a1"}) == "c1"


def test_scrap_meeting_preserves_item_as_processed_tombstone():
    crm = client()
    ensured = []
    changed = []
    crm.ensure_text_column = lambda board, title: ensured.append((board, title))
    crm.change_columns = lambda board, item, values: changed.append(
        (board, item, values))

    crm.scrap_meeting("m1")

    assert ensured == [("meeting", "Notes Status")]
    assert changed == [("meeting", "m1", {
        "Processing Status": "Processed",
        "Notes Status": "Scrapped",
    })]


def test_table_view_resolves_status_text_to_numeric_label_id():
    crm = client()
    crm._columns["account"]["Review Status"] = {"id": "review", "type": "status"}
    crm.views = lambda board: []
    crm.ensure_status_label = lambda board, title, label: 7
    calls = []
    crm._post = lambda query, variables=None: (
        calls.append((query, variables)) or
        {"create_view_table": {"id": "v1", "name": "Needs Review", "type": "TABLE"}}
    )
    crm.ensure_table_view("account", "Needs Review", [{
        "column": "Review Status", "operator": "contains_terms",
        "compare_value": "Needs Review",
    }])
    rule = calls[0][1]["filter"]["rules"][0]
    assert rule == {
        "column_id": "review", "compare_value": [7], "operator": "any_of"}


def test_missing_status_label_is_added_without_hardcoded_id():
    crm = client()
    crm._columns["account"]["Review Status"] = {"id": "review", "type": "status"}
    calls = []

    def post(query, variables=None):
        calls.append((query, variables))
        if query.startswith("query"):
            return {"boards": [{"columns": [{
                "id": "review", "type": "status", "revision": "r1",
                "settings": {"labels": [{
                    "id": 0, "label": "Working on it", "color": 0,
                    "index": 0, "is_done": True,
                }]},
            }]}]}
        return {"update_status_column": {
            "id": "review", "revision": "r2", "settings": {"labels": [
                {"id": 0, "label": "Working on it"},
                {"id": 7, "label": "Needs Review"},
            ]},
        }}

    crm._post = post
    assert crm.ensure_status_label("account", "Review Status", "Needs Review") == 7
    labels = calls[1][1]["settings"]["labels"]
    assert labels[0]["color"] == "working_orange"
    assert labels[-1]["label"] == "Needs Review"
    assert labels[-1]["color"] == "bright_blue"
    assert labels[-1]["index"] == 1


def test_account_type_setter_allowlists_ai_investment_and_ensures_label():
    crm = client()
    ensured = []
    changed = []
    crm.ensure_status_label = lambda board, title, label, **kwargs: ensured.append(
        (board, title, label, kwargs)) or 7
    crm.change_columns = lambda board, item, values: changed.append(
        (board, item, values))

    crm.set_account_type("a1", "AI Investment")

    assert ensured == [("account", "Type", "AI Investment", {"color": "navy"})]
    assert changed == [("account", "a1", {"Type": "AI Investment"})]

    try:
        crm.set_account_type("a1", "Partner")
    except ValueError as error:
        assert "unsupported deterministic Account Type" in str(error)
    else:
        raise AssertionError("non-allowlisted Account Type was accepted")


def test_reasoning_cache_uses_unbounded_text_column():
    crm = client()
    crm._columns["meeting"] = {
        "Reasoning Cache": {"id": "cache", "type": "text"},
        "Processing Started At": {"id": "started", "type": "text"},
    }
    changes = []
    crm.change_columns = lambda board, item, values: changes.append(
        (board, item, values))
    result = {"meeting": {"summary": "x" * 4000}}
    crm.save_reasoning_result("m1", result)
    assert changes[0][0:2] == ("meeting", "m1")
    cached = changes[0][2]["Reasoning Cache"]
    assert len(cached) > 2000
    assert json.loads(cached) == result


def test_reasoning_cache_reader_preserves_nested_types():
    result = {
        "meeting": {"summary": "Clear next steps"},
        "deal": {"deal_value": {"status": "ANSWERED", "value": 9000}},
        "account": {"notes": "Context"},
        "contacts": [{"email": "jane@example.com"}],
    }
    assert _json_object(json.dumps(result)) == result


def test_raw_text_preserves_slack_links_hidden_by_monday_rendering():
    original = "Website: <http://dreamborn.ai|dreamborn.ai>"
    assert _raw_text({
        "text": "Website: ", "value": json.dumps(original),
    }) == original


def test_encoded_payload_roundtrips_slack_markup_losslessly():
    original = "Website: <http://dreamborn.ai|dreamborn.ai> — café"
    encoded = _encode_payload(original)
    assert "<" not in encoded and "|" not in encoded
    assert _decode_payload({"value": json.dumps(encoded), "text": encoded}) == original


def test_full_manual_notes_use_lossless_cache_and_bounded_preview():
    crm = client()
    crm._columns["meeting"] = {
        "Manual Notes": {"id": "preview", "type": "long_text"},
        "Manual Notes Cache": {"id": "full", "type": "text"},
        "Manual Notes Payload": {"id": "payload", "type": "text"},
        "Manual Notes Chars": {"id": "chars", "type": "text"},
        "Manual Notes SHA256": {"id": "sha", "type": "text"},
        "Slack Note IDs": {"id": "notes", "type": "text"},
        "Notes Status": {"id": "status", "type": "text"},
        "Call Notes Last TS": {"id": "last", "type": "text"},
    }
    changes = []
    crm.change_columns = lambda board, item, values: changes.append(values)
    notes = "Important context and todos.\n" * 300
    crm.save_call_notes("m1", notes, ["101.0"], last_ts="101.0")
    assert changes[0]["Manual Notes Cache"] == notes
    assert _decode_payload({"text": changes[0]["Manual Notes Payload"]}) == notes
    assert changes[0]["Manual Notes Chars"] == str(len(notes))
    assert len(changes[0]["Manual Notes SHA256"]) == 64
    assert len(changes[0]["Manual Notes"]) < 2_000
    assert changes[0]["Manual Notes"].endswith("[Full notes stored in cache]")


def test_phase2_numeric_and_checkbox_columns_are_creatable_and_typed():
    crm = client()
    created = []
    def post(query, variables=None):
        kind = "numbers" if "column_type:numbers" in query else "checkbox"
        created.append(kind)
        return {"create_column": {"id": kind, "title": variables["title"],
                                  "type": kind}}
    crm._post = post
    crm._columns["activity"] = {}
    assert crm.ensure_column("activity", "Completion Confidence", "numbers")["type"] == "numbers"
    crm._columns["activity"] = {}
    assert crm.ensure_column("activity", "Awaiting Client Reply", "checkbox")["type"] == "checkbox"
    assert created == ["numbers", "checkbox"]

    crm._columns["activity"] = {
        "Completion Confidence": {"id": "wrong", "type": "text"}}
    try:
        crm.ensure_column("activity", "Completion Confidence", "numbers")
    except RuntimeError as error:
        assert "expected numbers" in str(error)
    else:
        raise AssertionError("managed schema type collision must fail closed")


def test_phase2_schema_declares_every_managed_addition():
    crm = client()
    requested = []
    crm.optional_column = lambda board, title: None
    crm.ensure_column = lambda board, title, kind: (
        requested.append((board, title, kind)) or
        {"id": title, "title": title, "type": kind})
    created = crm.prepare_phase2_schema()
    assert len(requested) == 17 and len(created["columns"]) == 17
    assert ("activity", "Completion Confidence", "numbers") in requested
    assert ("activity", "Awaiting Client Reply", "checkbox") in requested
    assert ("deal", "Nudge Condition Key", "text") in requested


def test_deal_fee_schema_creates_human_inputs_in_card_order():
    crm = Monday(token="test")
    created = []

    def post(query, variables=None):
        if query.startswith("query"):
            return {"boards": [{"columns": [
                {"id": "deal_owner", "title": "Owner", "type": "people",
                 "settings": {}},
            ]}]}
        created.append((query, variables))
        settings = {}
        if "column_type:formula" in query:
            settings = json.loads(variables["defaults"])["settings"]
        return {"create_column": {
            "id": variables["id"], "title": variables["title"],
            "type": ("formula" if "column_type:formula" in query else "numbers"),
            "settings": settings,
        }}

    crm._post = post
    result = crm.prepare_deal_fee_schema()

    assert result["columns"] == [
        "Fixed Fee", "Monthly Retainer", "Hourly Rate", "Estimated Hours",
        "Calculated Deal Value",
    ]
    assert [variables["after"] for _, variables in created] == [
        "deal_owner", "fixed_fee", "monthly_retainer", "hourly_rate",
        "estimated_hours",
    ]
    assert created[-1][1]["defaults"] == json.dumps({"settings": {"formula": (
        "SUM({fixed_fee},MULTIPLY({monthly_retainer},3),"
        "MULTIPLY({hourly_rate},{estimated_hours}))"
    )}})
    assert result["retainer_projection_months"] == 3


def test_deal_fee_schema_is_idempotent():
    crm = Monday(token="test")
    expected = [
        ("fixed_fee", "Fixed Fee", "numbers"),
        ("monthly_retainer", "Monthly Retainer", "numbers"),
        ("hourly_rate", "Hourly Rate", "numbers"),
        ("estimated_hours", "Estimated Hours", "numbers"),
        ("calc_deal_value", "Calculated Deal Value", "formula"),
    ]
    formula = (
        "SUM({fixed_fee},MULTIPLY({monthly_retainer},3),"
        "MULTIPLY({hourly_rate},{estimated_hours}))"
    )
    crm._post = lambda query, variables=None: {"boards": [{"columns": [
        {"id": column_id, "title": title, "type": kind,
         "settings": ({"formula": formula} if kind == "formula" else {})}
        for column_id, title, kind in expected
    ]}]}

    assert crm.prepare_deal_fee_schema()["columns"] == []


def test_deal_fee_schema_fails_closed_on_formula_drift():
    crm = Monday(token="test")
    crm._post = lambda query, variables=None: {"boards": [{"columns": [
        {"id": "fixed_fee", "title": "Fixed Fee", "type": "numbers"},
        {"id": "monthly_retainer", "title": "Monthly Retainer",
         "type": "numbers"},
        {"id": "hourly_rate", "title": "Hourly Rate", "type": "numbers"},
        {"id": "estimated_hours", "title": "Estimated Hours",
         "type": "numbers"},
        {"id": "calc_deal_value", "title": "Calculated Deal Value",
         "type": "formula", "revision": "r1",
         "settings": {"formula": "{monthly_retainer}"}},
    ]}]}

    try:
        crm.prepare_deal_fee_schema()
    except RuntimeError as error:
        assert "different formula" in str(error)
    else:
        raise AssertionError("formula drift must require explicit authorization")


def test_deal_fee_schema_explicitly_updates_formula_and_description():
    crm = Monday(token="test")
    calls = []
    formula = (
        "SUM({fixed_fee},MULTIPLY({monthly_retainer},3),"
        "MULTIPLY({hourly_rate},{estimated_hours}))"
    )

    def post(query, variables=None):
        calls.append((query, variables))
        if query.startswith("query"):
            return {"boards": [{"columns": [
                {"id": "fixed_fee", "title": "Fixed Fee", "type": "numbers"},
                {"id": "monthly_retainer", "title": "Monthly Retainer",
                 "type": "numbers", "description": "one month"},
                {"id": "hourly_rate", "title": "Hourly Rate", "type": "numbers"},
                {"id": "estimated_hours", "title": "Estimated Hours",
                 "type": "numbers"},
                {"id": "calc_deal_value", "title": "Calculated Deal Value",
                 "type": "formula", "description": "one month",
                 "revision": "r1", "settings": {"formula": "old"}},
            ]}]}
        if "update_column" in query:
            return {"update_column": {
                "id": "calc_deal_value", "title": "Calculated Deal Value",
                "type": "formula", "description": variables["description"],
                "revision": "r2", "settings": {"formula": formula},
            }}
        return {"change_column_metadata": {
            "id": "monthly_retainer", "title": "Monthly Retainer",
            "description": variables["description"],
        }}

    crm._post = post
    result = crm.prepare_deal_fee_schema(update_formula=True)

    assert result["formula_updated"] is True
    assert result["description_updated"] is True
    assert calls[1][1]["settings"] == {"formula": formula}
    assert "3 months" in calls[2][1]["description"]


def test_meeting_identity_preserves_live_long_text_contact_email_map():
    crm = client()
    crm._columns["meeting"] = {
        title: {"id": title, "type": "text"}
        for title in (
            "Prospect Account ID", "Partner Account IDs", "Contact IDs",
            "Resolved Deal ID",
        )
    }
    crm._columns["meeting"]["Contact Email IDs"] = {
        "id": "contact_emails", "type": "long_text"}
    changes = []
    crm.change_columns = lambda board, item, values: changes.append(
        (board, item, values))

    crm.set_meeting_ready(
        "m1", ["a1"], ["c1"], "d1",
        contact_email_ids={"jane@acme.com": "c1"})
    crm.save_manual_meeting_context(
        "m2", account_id="a1", contact_ids=["c1"],
        contact_email_ids={"jane@acme.com": "c1"}, deal_id="d1")

    assert len(changes) == 2
    assert changes[0][2]["Contact Email IDs"] == '{"jane@acme.com": "c1"}'
    assert changes[1][2]["Contact Email IDs"] == '{"jane@acme.com": "c1"}'


def test_agent_events_board_must_be_private():
    crm = client()
    prior = BOARDS.pop("agent_event", None)
    try:
        crm._post = lambda query, variables=None: {"boards": [{
            "id": "999", "name": "Agent Events", "board_kind": "public"}]}
        try:
            crm.prepare_agent_events_schema()
        except RuntimeError as error:
            assert "not private" in str(error)
        else:
            raise AssertionError("public Agent Events board must be rejected")
    finally:
        BOARDS.pop("agent_event", None)
        if prior is not None:
            BOARDS["agent_event"] = prior


def test_activity_completion_revalidates_human_state_before_write():
    crm = client()
    crm._columns["activity"] = {
        "Status": {"id": "status", "type": "status"},
        "Related item": {"id": "related", "type": "board_relation"},
    }
    crm.prepare_phase2_schema = lambda: None
    crm._post = lambda query, variables=None: {"items": [{
        "id": "t1", "name": "Send prep", "column_values": [
            {"id": "status", "text": "Done"},
            {"id": "related", "text": "", "linked_item_ids": ["d1"]},
        ]}]}
    changes = []
    crm.change_columns = lambda *args, **kwargs: changes.append((args, kwargs))
    assert not crm.complete_activity_from_event(
        "t1", event_key="event", occurred_at="2026-08-17T12:00:00+00:00",
        source_type="email", evidence="sent", confidence=.99,
        awaiting_client_reply=True, source_timeline_id="mail-1",
        expected_title="Send prep", expected_related_ids=["d1"])
    assert changes == []


def test_contact_last_interaction_is_monotonic():
    crm = client()
    crm._columns["contact"]["Last Interaction"] = {
        "id": "last", "type": "date"}
    crm.prepare_phase2_schema = lambda: None
    crm._post = lambda query, variables=None: {"items": [{
        "id": "c1", "column_values": [{
            "id": "last", "text": "2026-08-18", "value": None}]}]}
    changes = []
    crm.change_columns = lambda *args, **kwargs: changes.append((args, kwargs))
    crm.stamp_last_interaction(
        deal_ids=[], contact_ids=["c1"],
        occurred_at="2026-08-17T12:00:00+00:00", event_key="old")
    assert changes == []


def test_duplicate_contact_email_index_excludes_ambiguous_identity():
    crm = client()
    crm._post = lambda query, variables=None: {"boards": [{"items_page": {
        "cursor": None, "items": [
            {"id": "c1", "name": "Jane One", "column_values": [
                {"id": "email", "text": "jane@acme.com"},
                {"id": "accounts", "text": "", "linked_item_ids": ["a1"]},
            ]},
            {"id": "c2", "name": "Jane Two", "column_values": [
                {"id": "email", "text": "jane@acme.com"},
                {"id": "accounts", "text": "", "linked_item_ids": ["a1"]},
            ]},
        ]}}]}
    assert crm.email_contact_index() == {}
    assert crm.email_contact_ambiguity_count == 1
    assert [[row["id"] for row in group]
            for group in crm.email_contact_ambiguities] == [["c1", "c2"]]


def test_explicit_contact_identity_resolution_is_non_destructive_and_exact():
    crm = client()
    crm._post = lambda query, variables=None: {"boards": [{"items_page": {
        "cursor": None, "items": [
            {"id": "c1", "name": "Jane", "column_values": [
                {"id": "email", "text": "jane@acme.com"},
                {"id": "accounts", "text": "", "linked_item_ids": ["a1"]},
            ]},
            {"id": "c2", "name": "jane@acme.com", "column_values": [
                {"id": "email", "text": "jane@acme.com"},
                {"id": "accounts", "text": "", "linked_item_ids": ["a1"]},
            ]},
        ]}}]}
    identity_hash = hashlib.sha256(b"jane@acme.com").hexdigest()
    original = config.EMAIL_CONTACT_IDENTITY_RESOLUTIONS
    try:
        config.EMAIL_CONTACT_IDENTITY_RESOLUTIONS = json.dumps({identity_hash: "c1"})
        result = crm.email_contact_index()
    finally:
        config.EMAIL_CONTACT_IDENTITY_RESOLUTIONS = original
    assert result["jane@acme.com"]["id"] == "c1"
    assert result["jane@acme.com"]["timeline_contact_ids"] == ["c1", "c2"]
    assert crm.email_contact_ambiguity_count == 0
    assert crm.email_contact_identity_resolutions == [{
        "identity_sha256": identity_hash,
        "canonical_contact_id": "c1",
        "quarantined_contact_ids": ["c2"],
    }]


def test_transport_retries_queries_but_not_ambiguous_mutations():
    crm = client()
    original_sleep = __import__("runtime.monday", fromlist=["time"]).time.sleep
    monday_module = __import__("runtime.monday", fromlist=["time"])
    calls = []

    class Response:
        status_code = 200
        ok = True
        headers = {}
        text = ""
        def json(self): return {"data": {"ok": True}}

    def retrying_post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.Timeout("response lost")
        return Response()

    try:
        monday_module.time.sleep = lambda *_args: None
        crm.session.post = retrying_post
        assert crm._post("query{me{id}}") == {"ok": True}
        assert len(calls) == 2

        calls.clear()
        crm.session.post = retrying_post
        try:
            crm._post("mutation{create_update(item_id:1,body:\"x\"){id}}")
        except RuntimeError as error:
            assert "outcome is unknown" in str(error)
        else:
            raise AssertionError("ambiguous mutation transport failure must surface")
        assert len(calls) == 1
    finally:
        monday_module.time.sleep = original_sleep


def test_transport_retries_transient_graphql_query_errors_only():
    crm = client()
    original_sleep = __import__("runtime.monday", fromlist=["time"]).time.sleep
    monday_module = __import__("runtime.monday", fromlist=["time"])
    calls = []

    class Response:
        status_code = 200
        ok = True
        headers = {}
        text = ""
        def json(self):
            calls.append(1)
            if len(calls) == 1:
                return {"errors": [{
                    "message": "Internal Server Error",
                    "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                }]}
            return {"data": {"ok": True}}

    try:
        monday_module.time.sleep = lambda *_args: None
        crm.session.post = lambda *args, **kwargs: Response()
        assert crm._post("query{me{id}}") == {"ok": True}
        assert len(calls) == 2

        calls.clear()
        try:
            crm._post("mutation{create_update(item_id:1,body:\"x\"){id}}")
        except RuntimeError as error:
            assert "INTERNAL_SERVER_ERROR" in str(error)
        else:
            raise AssertionError("GraphQL mutation errors must never be retried")
        assert len(calls) == 1
    finally:
        monday_module.time.sleep = original_sleep


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
