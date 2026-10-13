"""Slack REST client — read channel history + thread replies, post messages,
resolve channels, DM task owners by email. Uses the bot token (SLACK_BOT_TOKEN).

Scopes needed (see docs/SLACK-SETUP.md): channels:history, channels:read,
chat:write, users:read, users:read.email.

LIVE-VALIDATION NOTE: not run against a real token yet. Behavior follows Slack's
documented Web API. The bot must be invited to both channels to read/post.
"""
from __future__ import annotations

import time

import requests

from .config import config

API = "https://slack.com/api"


class Slack:
    def __init__(self, token: str | None = None):
        self.token = token or config.SLACK_BOT_TOKEN
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}"})
        self._channel_cache: dict[str, str] = {}

    def _call(self, method: str, http="GET", **params) -> dict:
        for attempt in range(4):
            if http == "GET":
                r = self.s.get(f"{API}/{method}", params=params, timeout=30)
            else:
                r = self.s.post(f"{API}/{method}", data=params, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            data = r.json()
            if not data.get("ok"):
                # rate_limited handled above; surface everything else
                raise RuntimeError(f"Slack {method} error: {data.get('error')}")
            return data
        raise RuntimeError(f"Slack {method} failed after retries")

    def channel_id(self, name: str) -> str:
        name = name.lstrip("#")
        if name in self._channel_cache:
            return self._channel_cache[name]
        cursor = None
        while True:
            resp = self._call("conversations.list",
                              types="public_channel", limit=1000,
                              **({"cursor": cursor} if cursor else {}))
            for c in resp.get("channels", []):
                self._channel_cache[c["name"]] = c["id"]
            if name in self._channel_cache:
                return self._channel_cache[name]
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        raise RuntimeError(f"Slack channel not found (is the bot invited?): #{name}")

    def history(self, channel_name: str, oldest_ts: str | None = None) -> list[dict]:
        """Messages in a channel, newest-first, following Slack pagination."""
        ch = self.channel_id(channel_name)
        params = {"channel": ch, "limit": 200}
        if oldest_ts:
            params["oldest"] = oldest_ts
        return self._paginated_messages("conversations.history", params)

    def replies(self, channel_name: str, thread_ts: str,
                oldest_ts: str | None = None) -> list[dict]:
        """Thread messages after ``oldest_ts``, following Slack pagination."""
        ch = self.channel_id(channel_name)
        params = {"channel": ch, "ts": thread_ts, "limit": 200}
        if oldest_ts:
            params["oldest"] = oldest_ts
        return self._paginated_messages("conversations.replies", params)

    def _paginated_messages(self, method: str, params: dict) -> list[dict]:
        messages: list[dict] = []
        cursor = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            response = self._call(method, **page_params)
            messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                return messages

    def post(self, channel_name: str, text: str, thread_ts: str | None = None,
             client_msg_id: str | None = None) -> str:
        ch = self.channel_id(channel_name)
        params = {"channel": ch, "text": text}
        if thread_ts:
            params["thread_ts"] = thread_ts
        if client_msg_id:
            params["client_msg_id"] = client_msg_id
        return self._call("chat.postMessage", http="POST", **params)["ts"]

    def update(self, channel_name: str, message_ts: str, text: str) -> None:
        ch = self.channel_id(channel_name)
        self._call(
            "chat.update", http="POST", channel=ch, ts=message_ts, text=text)

    def delete(self, channel_name: str, message_ts: str) -> None:
        ch = self.channel_id(channel_name)
        try:
            self._call("chat.delete", http="POST", channel=ch, ts=message_ts)
        except RuntimeError as error:
            # The desired state is already reached if a prior run deleted the
            # post but died before clearing the saved monday timestamp.
            if "message_not_found" not in str(error):
                raise

    def permalink(self, channel_name: str, message_ts: str) -> str:
        ch = self.channel_id(channel_name)
        return self._call("chat.getPermalink", channel=ch,
                          message_ts=message_ts)["permalink"]

    def user_id_by_email(self, email: str) -> str | None:
        try:
            return self._call("users.lookupByEmail", email=email)["user"]["id"]
        except RuntimeError:
            return None  # users_not_found etc. — caller falls back to a channel ping

    def dm(self, user_id: str, text: str) -> str:
        # posting to a user id opens/uses the IM channel
        return self._call("chat.postMessage", http="POST", channel=user_id, text=text)["ts"]
