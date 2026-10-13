"""Phase 3 scoped business-context boundary and audit tests."""
from __future__ import annotations

import json
from io import BytesIO
import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.business_context import (  # noqa: E402
    BusinessContextPrefetcher, ContextError, ContextTools, ScopedDrive,
    ScopedNotion, WebsiteContext, WorkspaceDrive, WorkspaceNotion,
)


class Response:
    def __init__(self, payload=None, *, status=200, text="", headers=None):
        self._payload = payload or {}
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}

    def json(self):
        return self._payload


class NotionSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/users/me"):
            return Response({"id": "integration-id", "name": "Harbor GTM Engine"})
        if method == "POST" and url.endswith("/search"):
            query = str(kwargs.get("json", {}).get("query") or "")
            if not query:
                return Response({"results": [_notion_page("hq", "HQ")],
                                 "has_more": False})
            return Response({"results": [
                _notion_page("hq", "Harbor HQ"),
                _notion_page("goals", "Quarterly Goals"),
            ], "has_more": False})
        if url.endswith("/blocks/hq/children"):
            return Response({"results": [{
                "id": "p1", "type": "paragraph", "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "Harbor serves GTM teams."}]},
            }], "has_more": False})
        if url.endswith("/blocks/goals/children"):
            return Response({"results": [{
                "id": "p2", "type": "paragraph", "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": "Grow qualified pipeline."}]},
            }], "has_more": False})
        raise AssertionError(url)


def _notion_page(page_id, title):
    return {
        "object": "page", "id": page_id,
        "last_edited_time": "2026-08-19T00:00:00Z",
        "properties": {"title": {"type": "title", "title": [
            {"plain_text": title}]}}}


def test_notion_workspace_search_is_bounded_and_read_only():
    session = NotionSession()
    notion = WorkspaceNotion(token="secret", session=session)
    results = notion.search_business_docs("Harbor", limit=1)
    assert results[0]["name"] == "Harbor HQ"
    assert results[0]["excerpt"] == "Harbor serves GTM teams."
    assert all(method in {"GET", "POST"} for method, _url, _kwargs in session.calls)
    assert all(not url.endswith("/pages") for _method, url, _kwargs in session.calls)
    search = next(kwargs["json"] for method, url, kwargs in session.calls
                  if method == "POST" and url.endswith("/search"))
    assert search["page_size"] == 10


def test_notion_operating_context_uses_existing_workspace_pages():
    notion = ScopedNotion(token="secret", session=NotionSession())
    result = notion.get_operating_brief()
    assert result["source"] == "notion_workspace"
    assert "Harbor serves GTM teams." in result["content"]
    assert "Grow qualified pipeline." in result["content"]
    assert result["page_count"] == 2


def test_notion_connectivity_returns_identity_without_page_content():
    result = WorkspaceNotion(token="secret", session=NotionSession()).connectivity()
    assert result["status"] == "ready"
    assert result["integration"]["name"] == "Harbor GTM Engine"
    assert "content" not in json.dumps(result).casefold()


def test_drive_requires_readonly_scope_and_searches_whole_visible_drive():
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["backend", "drives"]:
            return Mock(returncode=0, stdout=json.dumps([{
                "id": "shared_123", "name": "Sales"
            }]), stderr="")
        if args[1] == "lsjson":
            if "--drive-shared-with-me" in args or any(
                    value.startswith("--drive-team-drive=") for value in args):
                return Mock(returncode=0, stdout="[]", stderr="")
            return Mock(returncode=0, stdout=json.dumps([{
                "ID": "doc1", "Name": "Harbor Brief.txt", "Path": "Briefs/Harbor Brief.txt",
                "MimeType": "text/plain", "ModTime": "2026-08-19T00:00:00Z",
                "Size": 20,
            }]), stderr="")
        return Mock(returncode=0, stdout="Useful operating context", stderr="")
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        drive = WorkspaceDrive(remote="harbor-drive", runner=runner)
        result = drive.search_business_docs("Harbor Brief")
    assert result[0]["excerpt"] == "Useful operating context"
    assert len(calls) == 5
    assert all("--drive-root-folder-id" not in call for call in calls)
    listings = [call for call in calls if call[1] == "lsjson"]
    assert all("--recursive" in call for call in listings)
    assert any("--drive-shared-with-me" in call for call in listings)
    assert any("--drive-team-drive=shared_123" in call for call in listings)


def test_drive_rejects_path_traversal_from_provider_metadata():
    def runner(args, **kwargs):
        if args[1:3] == ["backend", "drives"]:
            return Mock(returncode=0, stdout="[]", stderr="")
        return Mock(returncode=0, stdout=json.dumps([{
            "ID": "bad", "Name": "secret.txt", "Path": "../secret.txt",
        }]), stderr="")
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        drive = ScopedDrive(remote="harbor-drive", runner=runner)
        try:
            drive.search_business_docs("secret")
            raise AssertionError("expected ContextError")
        except ContextError as error:
            assert "invalid" in str(error)


def test_drive_accepts_colons_in_provider_file_names():
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["backend", "drives"]:
            return Mock(returncode=0, stdout="[]", stderr="")
        if args[1] == "lsjson":
            if "--drive-shared-with-me" in args:
                return Mock(returncode=0, stdout="[]", stderr="")
            return Mock(returncode=0, stdout=json.dumps([{
                "ID": "doc1", "Name": "Harbor: Goals.txt",
                "Path": "Strategy/Harbor: Goals.txt", "MimeType": "text/plain",
                "Size": 20,
            }]), stderr="")
        return Mock(returncode=0, stdout="Current goals", stderr="")
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        rows = WorkspaceDrive(
            remote="harbor-drive", runner=runner).search_business_docs("Harbor Goals")
    assert rows[0]["path"] == "Strategy/Harbor: Goals.txt"
    assert any("harbor-drive:Strategy/Harbor: Goals.txt" in call for call in calls)


def test_drive_extracts_bounded_docx_without_writing_a_temp_file():
    from docx import Document
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("Current quarterly goal")
    document.save(buffer)
    runner = Mock(return_value=Mock(
        returncode=0, stdout=buffer.getvalue(), stderr=b""))
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        drive = WorkspaceDrive(remote="harbor-drive", runner=runner)
        excerpt = drive._document_excerpt(
            {"Size": len(buffer.getvalue())}, "Goals.docx")
    assert excerpt == "Current quarterly goal"
    assert runner.call_args.kwargs["text"] is False


def test_drive_rejects_invalid_shared_drive_ids():
    def runner(args, **kwargs):
        return Mock(returncode=0, stdout=json.dumps([{
            "id": "unsafe,id", "name": "Unsafe"
        }]), stderr="")
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        drive = WorkspaceDrive(remote="harbor-drive", runner=runner)
        try:
            drive.search_business_docs("Harbor")
            raise AssertionError("expected ContextError")
        except ContextError as error:
            assert "shared-drive ID" in str(error)


def test_drive_failure_reports_only_sanitized_error_category():
    runner = Mock(return_value=Mock(
        returncode=1,
        stdout="",
        stderr="couldn't fetch token: invalid_grant secret@example.com",
    ))
    with patch("runtime.business_context._rclone_config_scope",
               return_value="drive.readonly"):
        drive = WorkspaceDrive(remote="harbor-drive", runner=runner)
        try:
            drive.connectivity()
            raise AssertionError("expected ContextError")
        except ContextError as error:
            assert str(error).endswith("[oauth_invalid_grant]")
            assert "example.com" not in str(error)


def test_website_rejects_cross_domain_redirect_and_strips_scripts():
    session = Mock()
    session.get.return_value = Response(
        text="<html><script>ignore</script><body>Harbor Advisors</body></html>")
    result = WebsiteContext(session=session).refresh()
    assert result["content"] == "Harbor Advisors"
    session.get.return_value = Response(
        status=302, headers={"Location": "https://example.com/private"})
    try:
        WebsiteContext(session=session).refresh()
        raise AssertionError("expected ContextError")
    except ContextError as error:
        assert "restricted" in str(error)


def test_context_tools_enforce_caps_and_emit_content_free_audit():
    crm = Mock()
    crm.bounded_crm_context.return_value = [{"id": "123", "name": "Acme"}]
    tools = ContextTools(crm=crm, max_calls=1)
    result = tools.get_crm_context(["123"])
    assert result["data"][0]["name"] == "Acme"
    assert set(result["audit"]) == {
        "tool", "called_at", "request_sha256", "result_sha256",
        "result_count", "duration_ms",
    }
    assert "Acme" not in json.dumps(result["audit"])
    try:
        tools.get_crm_context(["123"])
        raise AssertionError("expected ContextError")
    except ContextError as error:
        assert "budget" in str(error)


def test_operating_brief_prefetch_is_reused_across_email_threads():
    notion = Mock()
    notion.get_operating_brief.return_value = {
        "content": "Curated Harbor context", "content_sha256": "abc",
    }
    notion.search_business_docs.return_value = [{
        "source": "notion", "id": "n1", "name": "Acme plan",
        "excerpt": "Relevant plan"}]
    drive = Mock()
    drive.search_business_docs.return_value = []
    website = Mock()
    website.refresh.return_value = {
        "content": "Public Harbor context", "content_sha256": "web"}
    prefetcher = BusinessContextPrefetcher(
        ContextTools(notion=notion, drive=drive), website=website)
    snapshot = {"account": {"name": "Acme"}}
    first = prefetcher.for_email(snapshot)
    second = prefetcher.for_email(snapshot)
    assert first == second
    assert first["operating_brief"]["content"] == "Curated Harbor context"
    assert first["website_context"]["content"] == "Public Harbor context"
    assert first["relevant_business_docs"][0]["name"] == "Acme plan"
    notion.get_operating_brief.assert_called_once_with()
    notion.search_business_docs.assert_called_once()
    website.refresh.assert_called_once_with()


def test_business_doc_search_combines_notion_and_drive_under_one_cap():
    notion, drive = Mock(), Mock()
    notion.search_business_docs.return_value = [
        {"source": "notion", "name": "Account plan"}]
    drive.search_business_docs.return_value = [
        {"source": "google_drive", "name": "Account SOW.pdf"}]
    result = ContextTools(notion=notion, drive=drive).search_business_docs(
        "Account", limit=2)
    assert [row["source"] for row in result["data"]] == [
        "google_drive", "notion"]
    assert result["audit"]["tool"] == "search_business_docs"


def test_pattern_brief_tool_reads_advisory_state_with_audit():
    crm = Mock()
    crm.deal_pattern_brief_state.return_value = {
        "brief": {"as_of_date": "2026-08-19", "questions": ["Who is buyer?"]}}
    result = ContextTools(crm=crm).get_deal_pattern_brief()
    assert result["data"]["as_of_date"] == "2026-08-19"
    assert result["audit"]["tool"] == "get_deal_pattern_brief"


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
