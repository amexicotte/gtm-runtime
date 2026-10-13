"""Slack-to-monday Activity notification and completion tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.activity_alerts import _same_message, sync_activity_alerts  # noqa: E402


class FakeCRM:
    def __init__(self, rows):
        self.rows = rows
        self.alerts, self.cursors, self.completed, self.cleared = [], [], [], []
    def activities_for_alerts(self): return self.rows
    def ensure_activity_owner(self, item, owners): return owners or ["100000007"]
    def save_activity_alert(self, item, ts, link): self.alerts.append((item, ts, link))
    def save_activity_alert_cursor(self, item, ts): self.cursors.append((item, ts))
    def complete_activity_from_slack(self, item, ts): self.completed.append((item, ts))
    def clear_activity_alert(self, item): self.cleared.append(item)


class FakeSlack:
    def __init__(self, replies=None):
        self.messages, self.thread = [], replies or {}
        self.deleted, self.updated = [], []
    def post(self, channel, text, thread_ts=None):
        self.messages.append((channel, text, thread_ts))
        return "100.0"
    def permalink(self, channel, ts): return f"https://slack/{channel}/{ts}"
    def replies(self, channel, root, oldest_ts=None): return self.thread.get(root, [])
    def delete(self, channel, ts): self.deleted.append((channel, ts))
    def update(self, channel, ts, text): self.updated.append((channel, ts, text))


def activity(**updates):
    row = {"id": "a1", "title": "Send proposal", "owner": "Alex Mexicotte",
           "task_type": "Do it", "due_date": "2026-08-05",
           "related_names": ["Jane", "Acme"], "company_name": "Acme",
           "url": "https://monday/a1", "status": "Open",
           "slack_notification_ts": "", "slack_last_ts": ""}
    row.update(updates)
    return row


def test_unnotified_activity_posts_once_and_persists_thread():
    crm, slack = FakeCRM([activity()]), FakeSlack()
    stats = sync_activity_alerts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 8, 5, 12, tzinfo=dt.timezone.utc))
    assert stats.notified == 1
    assert slack.messages[0][0] == "deal-alerts"
    assert slack.messages[0][1] == "Acme: Send proposal (Aug 5)"
    assert crm.alerts == [("a1", "100.0", "https://slack/deal-alerts/100.0")]


def test_future_activity_is_deferred_until_due_date_in_et():
    row = activity(due_date="2026-09-08")
    crm, slack = FakeCRM([row]), FakeSlack()
    stats = sync_activity_alerts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 9, 8, 3, 59, tzinfo=dt.timezone.utc))
    assert stats.deferred == 1 and stats.notified == 0
    assert slack.messages == [] and crm.alerts == []


def test_activity_posts_on_due_date_in_et():
    row = activity(due_date="2026-09-08")
    crm, slack = FakeCRM([row]), FakeSlack()
    stats = sync_activity_alerts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 9, 8, 12, tzinfo=dt.timezone.utc))
    assert stats.deferred == 0 and stats.notified == 1
    assert slack.messages == [(
        "deal-alerts", "Acme: Send proposal (Sep 8)", None)]


def test_overdue_and_undated_activities_post_on_next_poll():
    rows = [activity(id="overdue", due_date="2026-09-07"),
            activity(id="undated", due_date="")]
    crm, slack = FakeCRM(rows), FakeSlack()
    stats = sync_activity_alerts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 9, 8, 12, tzinfo=dt.timezone.utc))
    assert stats.notified == 2 and stats.deferred == 0
    assert [item for item, _ts, _link in crm.alerts] == ["overdue", "undated"]


def test_premature_existing_alert_is_retracted_for_due_date_repost():
    row = activity(due_date="2026-09-08",
                   slack_notification_ts="100.0", slack_last_ts="100.0")
    crm, slack = FakeCRM([row]), FakeSlack()
    stats = sync_activity_alerts(
        slack, crm, dry_run=False,
        now=dt.datetime(2026, 8, 23, 12, tzinfo=dt.timezone.utc))
    assert stats.deferred == 1 and stats.deleted == 1
    assert slack.deleted == [("deal-alerts", "100.0")]
    assert crm.cleared == ["a1"] and slack.messages == []


def test_done_thread_reply_completes_activity():
    row = activity(slack_notification_ts="100.0", slack_last_ts="100.0")
    slack = FakeSlack({"100.0": [
        {"ts": "100.0", "text": "root", "bot_id": "B1"},
        {"ts": "101.0", "text": "done", "user": "U1"},
    ]})
    crm = FakeCRM([row])
    stats = sync_activity_alerts(slack, crm, dry_run=False)
    assert stats.completed == 1 and crm.completed == [("a1", "101.0")]
    assert slack.deleted == [("deal-alerts", "100.0")]
    assert crm.cleared == ["a1"] and slack.messages == []


def test_check_reaction_completes_activity():
    row = activity(slack_notification_ts="100.0", slack_last_ts="100.0")
    slack = FakeSlack({"100.0": [
        {"ts": "100.0", "text": "root", "bot_id": "B1",
         "reactions": [{"name": "white_check_mark", "count": 1}]},
    ]})
    crm = FakeCRM([row])
    stats = sync_activity_alerts(slack, crm, dry_run=False)
    assert stats.completed == 1
    assert crm.completed == [("a1", "100.0")]
    assert slack.deleted == [("deal-alerts", "100.0")]
    assert crm.cleared == ["a1"] and slack.messages == []


def test_completed_activity_retries_deletion_then_clears_saved_timestamp():
    row = activity(status="Done", slack_notification_ts="100.0")
    crm, slack = FakeCRM([row]), FakeSlack()
    stats = sync_activity_alerts(slack, crm, dry_run=False)
    assert stats.deleted == 1
    assert slack.deleted == [("deal-alerts", "100.0")]
    assert crm.cleared == ["a1"] and crm.completed == []


def test_existing_verbose_post_is_rewritten_as_one_line():
    row = activity(slack_notification_ts="100.0", slack_last_ts="100.0")
    slack = FakeSlack({"100.0": [
        {"ts": "100.0", "text": "Old verbose task post", "bot_id": "B1"},
    ]})
    crm = FakeCRM([row])
    stats = sync_activity_alerts(slack, crm, dry_run=False)
    assert stats.updated == 1
    assert slack.updated == [(
        "deal-alerts", "100.0", "Acme: Send proposal (Aug 5)")]


def test_slack_auto_link_does_not_trigger_rewrite_every_poll():
    assert _same_message(
        "Monday: Build package for <http://monday.com|monday.com> (Aug 10)",
        "Monday: Build package for monday.com (Aug 10)",
    )


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
