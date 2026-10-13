"""Regression tests for the Phase 0 read-only discovery CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import discovery


class DiscoveryTests(unittest.TestCase):
    def test_load_env_file_reports_names_and_preserves_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\nMONDAY_API_TOKEN=file-token\nexport EMPTY=\nQUOTED='value'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"MONDAY_API_TOKEN": "process-token"}, clear=True):
                configured = discovery.load_env_file(path)
                self.assertEqual(configured, ["MONDAY_API_TOKEN", "QUOTED"])
                self.assertEqual(os.environ["MONDAY_API_TOKEN"], "process-token")
                self.assertEqual(os.environ["QUOTED"], "value")

    def test_monday_query_rejects_mutations_before_reading_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(discovery.DiscoveryError, "refuses GraphQL mutations"):
                discovery._monday_query("mutation { delete_item(item_id: 1) { id } }")

    @patch("runtime.discovery._monday_query")
    def test_monday_schema_resolves_relation_targets(self, query):
        query.return_value = {
            "boards": [{
                "id": "10000000003",
                "name": "Deals",
                "state": "active",
                "board_kind": "public",
                "permissions": "everyone",
                "items_count": 1,
                "workspace_id": "10000012",
                "groups": [],
                "views": [],
                "columns": [{
                    "id": "deal_contact",
                    "title": "Contacts",
                    "type": "board_relation",
                    "settings": json.dumps({"boardIds": [10000000002]}),
                }],
            }],
        }
        result = discovery.monday_schema()
        self.assertEqual(result["boards"][0]["key"], "deal")
        self.assertEqual(
            result["boards"][0]["columns"][0]["target_board_ids"],
            ["10000000002"],
        )

    @patch("runtime.discovery.subprocess.run")
    def test_drive_connectivity_requires_readonly_scope_without_listing_content(self, run):
        about = Mock(returncode=0, stdout=json.dumps({"total": 100}), stderr="")
        run.return_value = about
        env = {
            "GOOGLE_DRIVE_RCLONE_REMOTE": "harbor-drive",
            "RCLONE_CONFIG_HARBOR-DRIVE_SCOPE": "drive.readonly",
        }
        with patch.dict(os.environ, env, clear=True):
            result = discovery.drive_connectivity()
        self.assertEqual(result["scope"], "drive.readonly")
        self.assertEqual(run.call_args_list[0].args[0][1], "about")
        self.assertNotIn("lsjson", run.call_args_list[0].args[0])
        self.assertNotIn("token", json.dumps(result).lower())

    @patch("runtime.discovery.subprocess.run")
    def test_rclone_scope_falls_back_to_redacted_file_config(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout="[harbor-drive]\nscope = drive.readonly\n",
            stderr="",
        )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                discovery._rclone_config_scope("harbor-drive"),
                "drive.readonly",
            )

    def test_drive_connectivity_blocks_non_readonly_scope(self):
        with patch.dict(
            os.environ,
            {"GOOGLE_DRIVE_RCLONE_REMOTE": "harbor-drive"},
            clear=True,
        ), patch("runtime.discovery.subprocess.run", return_value=Mock(
                returncode=0, stdout="[harbor-drive]\nscope = drive\n", stderr="")):
            with self.assertRaisesRegex(discovery.DiscoveryError, "drive.readonly"):
                discovery.drive_connectivity()

    @patch("runtime.discovery.requests.post")
    @patch("runtime.discovery.requests.get")
    def test_notion_connectivity_probes_workspace_without_root(self, get, post):
        get.return_value = Mock(ok=True)
        get.return_value.json.return_value = {
            "id": "integration-id", "name": "Harbor GTM Engine"}
        post.return_value = Mock(ok=True)
        post.return_value.json.return_value = {
            "results": [{"object": "page", "id": "sample"}]}
        with patch.dict(os.environ, {
                "NOTION_API_TOKEN": "secret",
                "NOTION_VERSION": "2025-09-03"}, clear=True):
            result = discovery.notion_connectivity()
        self.assertEqual(result["access"], "workspace-visible pages are readable")
        self.assertEqual(result["accessible_page_sample_count"], 1)
        self.assertNotIn("root", json.dumps(result).casefold())
        self.assertEqual(post.call_args.kwargs["json"]["page_size"], 1)


if __name__ == "__main__":
    unittest.main()
