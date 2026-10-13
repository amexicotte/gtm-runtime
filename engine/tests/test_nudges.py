"""Deterministic nudge thresholds, reply clearing, and idempotency tests."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.nudges import followup_condition, stalled_condition, sync_nudges  # noqa: E402


NOW = dt.datetime(2026, 8, 17, 16, tzinfo=dt.timezone.utc)


def deal(**changes):
    row = {"id": "d1", "name": "Acme", "active": True,
           "owner": "Alex", "owner_ids": ["111"],
           "last_interaction_at": "2026-08-15T12:00:00+00:00",
           "last_inbound_email_at": "", "nudge_ts": "", "nudge_key": ""}
    row.update(changes)
    return row


def activity(**changes):
    row = {"id": "t1", "title": "Send SOW", "owner": "Alex", "owner_ids": ["111"],
           "deal_ids": ["d1"], "completed_at": "2026-08-15T12:00:00+00:00",
           "nudge_ts": "", "nudge_key": ""}
    row.update(changes)
    return row


def test_thresholds_and_new_inbound_reply_clear_conditions():
    assert stalled_condition(deal(), now=NOW, hours=36)
    assert not stalled_condition(deal(last_interaction_at="2026-08-16"), now=NOW, hours=36)
    assert followup_condition(activity(), deal=deal(), now=NOW, hours=48)
    assert not followup_condition(
        activity(), deal=deal(last_inbound_email_at="2026-08-16T12:00:00+00:00"),
        now=NOW, hours=48)


class Slack:
    def __init__(self): self.posts, self.deleted = [], []
    def post(self, channel, text, client_msg_id=None):
        self.posts.append((channel, text, client_msg_id)); return "100.0"
    def permalink(self, channel, ts): return f"https://slack/{channel}/{ts}"
    def delete(self, channel, ts): self.deleted.append((channel, ts))


class CRM:
    def __init__(self): self.saved, self.cleared = [], []
    def save_deal_nudge(self, item, **kwargs): self.saved.append(("deal", item, kwargs))
    def clear_deal_nudge(self, item): self.cleared.append(("deal", item))
    def save_followup_nudge(self, item, **kwargs): self.saved.append(("activity", item, kwargs))
    def clear_followup_nudge(self, item): self.cleared.append(("activity", item))


def test_shadow_is_read_only_and_live_posts_each_condition_once():
    crm, slack = CRM(), Slack()
    shadow = sync_nudges(
        slack, crm, deals={"d1": deal()}, activities=[activity()],
        channel="deal-alerts", shadow=True, now=NOW)
    assert shadow.stalled_proposed == 1 and shadow.followup_proposed == 1
    assert not slack.posts and not crm.saved
    live = sync_nudges(
        slack, crm, deals={"d1": deal()}, activities=[activity()],
        channel="deal-alerts", shadow=False, now=NOW)
    assert live.posted == 2 and len(slack.posts) == 2 and len(crm.saved) == 2


def test_failed_nudge_state_save_compensates_slack_post():
    crm, slack = CRM(), Slack()
    def fail(*_args, **_kwargs): raise RuntimeError("monday unavailable")
    crm.save_deal_nudge = fail
    try:
        sync_nudges(
            slack, crm, deals={"d1": deal()}, activities=[],
            channel="deal-alerts", shadow=False, now=NOW)
    except RuntimeError as error:
        assert "monday unavailable" in str(error)
    else:
        raise AssertionError("expected monday save failure")
    assert slack.deleted == [("deal-alerts", "100.0")]
    assert slack.posts[0][2]


def test_followup_owner_falls_back_to_deal_owner():
    crm, slack = CRM(), Slack()
    sync_nudges(
        slack, crm, deals={"d1": deal(owner="Deal Alex", owner_ids=[])},
        activities=[activity(owner="", owner_ids=[])],
        channel="deal-alerts", shadow=False, now=NOW)
    followup = next(text for _channel, text, _key in slack.posts
                    if "no client response" in text)
    assert followup.startswith("Deal Alex:")


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
