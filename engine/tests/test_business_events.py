"""Normalized BusinessEvent and evidence-policy tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.business_events import (  # noqa: E402
    BusinessEvent, TimelineEvent, attach_business_event, retarget_business_event,
)
from runtime.evidence import (  # noqa: E402
    block_nonverbatim_evidence, enrich_reasoning_evidence, evidence_problem,
)


def _ctx():
    return {
        "meeting_id": "m1", "prospect_account_id": "a1", "deal_id": "d1",
        "contact_ids": ["c1"], "attendee_emails": ["JANE@ACME.COM"],
        "crm_snapshot": {
            "contacts": [{"id": "c1", "name": "Jane", "email": "jane@acme.com"}],
        },
    }


def test_meeting_event_is_stable_normalized_and_cached_without_source_content():
    result = {
        "relevant": True,
        "timeline_event": {
            "headline": "  Scope aligned  ",
            "body": " ".join(f"word{i}" for i in range(125)),
            "labels": ["Decision", "Decision", "Unsupported"],
        },
    }
    captured = dt.datetime(2026, 8, 17, 12, tzinfo=dt.timezone.utc)
    event = attach_business_event(
        result, source="meeting", meeting_id="m1",
        occurred_at="2026-08-14", title="Acme scope", content="full transcript",
        ctx=_ctx(), captured_at=captured,
    )
    assert event.event_key.startswith("meeting:m1:")
    assert len(event.content_sha256) == 64
    assert event.participants[0].email == "jane@acme.com"
    assert event.timeline_event.labels == ("Decision",)
    assert event.timeline_event.word_count <= 90
    assert "content" not in result["_event"]
    assert BusinessEvent.from_cache(result["_event"]).event_key == event.event_key
    assert attach_business_event(
        result, source="meeting", meeting_id="m1", occurred_at="2026-08-14",
        title="ignored", content="ignored", ctx=_ctx()).event_key == event.event_key


def test_timeline_event_requires_a_headline_and_body():
    assert TimelineEvent.from_dict({"headline": "", "body": "material"}) is None
    assert TimelineEvent.from_dict({"headline": "News", "body": ""}) is None


def test_evidence_adds_only_deterministic_metadata_and_requires_model_confidence():
    result = {
        "timeline_event": {"headline": "Budget", "body": "Budget was approved."},
        "deal": {"deal_value": {
            "status": "ANSWERED", "value": 10000,
            "evidence": {"excerpt": "Budget is ten thousand", "confidence": 0.94},
        }},
        "account": {"account_notes": "legacy scalar"},
    }
    event = attach_business_event(
        result, source="meeting", meeting_id="m1", occurred_at="2026-08-14",
        title="Budget", content="Budget is ten thousand", ctx=_ctx())
    warnings = enrich_reasoning_evidence(result, event)
    evidence = result["deal"]["deal_value"]["evidence"]
    assert evidence["source_id"] == event.source_id
    assert evidence["occurred_at"] == event.occurred_at
    assert evidence_problem(result["deal"]["deal_value"]) == ""
    assert any("account.account_notes" in warning for warning in warnings)


def test_incomplete_evidence_is_rejected():
    assert "confidence" in evidence_problem({
        "status": "ANSWERED", "value": "x",
        "evidence": {"excerpt": "x", "source_id": "s",
                     "occurred_at": "2026-08-14T00:00:00+00:00"},
    })


def test_only_high_confidence_answered_evidence_is_writable():
    base = {"value": "x", "evidence": {
        "excerpt": "x", "confidence": 0.95, "source_id": "s",
        "occurred_at": "2026-08-14T00:00:00+00:00",
    }}
    assert "status ANSWERED" in evidence_problem(dict(base, status="UNKNOWN"))
    assert "below 0.80" in evidence_problem({
        **base, "status": "ANSWERED", "evidence": {
            **base["evidence"], "confidence": 0.79,
        }})
    assert evidence_problem(dict(base, status="ANSWERED")) == ""


def test_hallucinated_meeting_excerpt_is_downgraded_before_write():
    result = {"deal": {"identified_pain": {
        "status": "ANSWERED", "value": "Pipeline is weak",
        "evidence": {"excerpt": "Our pipeline has collapsed", "confidence": .99},
    }}}
    warnings = block_nonverbatim_evidence(
        result, ["Jane said pipeline coverage should improve next quarter."])
    assert result["deal"]["identified_pain"]["status"] == "PARTIAL"
    assert warnings and "not verbatim" in warnings[0]


def test_cached_event_can_add_a_deterministically_created_deal_target():
    result = {
        "timeline_event": {"headline": "New opportunity", "body": "A new opportunity was explicitly opened for Acme."},
    }
    ctx = _ctx()
    ctx["deal_id"] = None
    attach_business_event(
        result, source="meeting", meeting_id="m1", occurred_at="2026-08-14",
        title="Acme", content="source", ctx=ctx)
    assert result["_event"]["deal_ids"] == ()
    ctx["deal_id"] = "d-new"
    assert retarget_business_event(result, ctx)
    assert result["_event"]["deal_ids"] == ("d-new",)
    assert not retarget_business_event(result, ctx)


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
