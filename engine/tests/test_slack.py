"""Slack client pagination tests without network calls."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.slack import Slack  # noqa: E402


def test_thread_replies_use_oldest_cursor_and_follow_pagination():
    slack = Slack(token="test")
    slack.channel_id = lambda name: "C1"
    calls = []
    pages = [
        {"ok": True, "messages": [{"ts": "11.0"}],
         "response_metadata": {"next_cursor": "next"}},
        {"ok": True, "messages": [{"ts": "12.0"}],
         "response_metadata": {"next_cursor": ""}},
    ]

    def fake_call(method, **params):
        calls.append((method, params))
        return pages.pop(0)

    slack._call = fake_call
    assert slack.replies("call-notes", "10.0", oldest_ts="10.5") == [
        {"ts": "11.0"}, {"ts": "12.0"},
    ]
    assert calls[0] == ("conversations.replies", {
        "channel": "C1", "ts": "10.0", "limit": 200, "oldest": "10.5",
    })
    assert calls[1][1]["cursor"] == "next"


def test_update_and_delete_resolve_channel_and_use_bot_message_timestamp():
    slack = Slack(token="test")
    slack.channel_id = lambda name: "C1"
    calls = []
    slack._call = lambda method, **params: calls.append((method, params)) or {"ok": True}
    slack.update("deal-alerts", "10.0", "Acme: Send proposal (Aug 5)")
    slack.delete("deal-alerts", "10.0")
    assert calls == [
        ("chat.update", {"http": "POST", "channel": "C1", "ts": "10.0",
                         "text": "Acme: Send proposal (Aug 5)"}),
        ("chat.delete", {"http": "POST", "channel": "C1", "ts": "10.0"}),
    ]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
