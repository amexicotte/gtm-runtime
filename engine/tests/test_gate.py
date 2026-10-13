"""Offline tests for the monday resolve-or-create gate."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.gate import (  # noqa: E402
    parse_background_identity,
    process_meeting,
    resume_background_from_slack,
    resume_from_slack,
)


class FakeMonday:
    def __init__(self, accounts=None, contacts=None, deals=None):
        self.accounts = accounts or {}
        self.contacts = contacts or {}
        self.deals = deals or {}
        self.next_id = 100
        self.gated = []
        self.ready = []
        self.saved_attendees = []
        self.scrapped = []
        self.deal_contact_links = []

    def find_account_by_domain(self, domain):
        return self.accounts.get(domain)

    def create_account(self, domain):
        self.next_id += 1
        item = {"id": str(self.next_id), "name": domain}
        self.accounts[domain] = item
        return item["id"]

    def find_contact_by_email(self, email):
        return self.contacts.get(email)

    def create_contact(self, email, account_id, name=None):
        self.next_id += 1
        self.contacts[email] = {"id": str(self.next_id), "account_id": account_id,
                                "name": name}
        return str(self.next_id)

    def find_deal_for_account(self, account_id):
        return self.deals.get(account_id)

    def set_meeting_gated(self, meeting_id, meeting_type):
        self.gated.append((meeting_id, meeting_type))

    def set_meeting_ready(self, meeting_id, account_ids, contact_ids, deal_id,
                          meeting_type="External", contact_email_ids=None):
        self.ready.append((meeting_id, account_ids, contact_ids, deal_id, meeting_type,
                           contact_email_ids))

    def save_meeting_attendees(self, meeting_id, emails, names):
        self.saved_attendees.append((meeting_id, emails, names))

    def scrap_meeting(self, meeting_id):
        self.scrapped.append(meeting_id)

    def link_contacts_to_deal(self, contact_ids, deal_id):
        self.deal_contact_links.append((contact_ids, deal_id))


class FakeSlack:
    def __init__(self):
        self.posts = []
        self.messages = []
        self.thread_replies = {}

    def post(self, channel, text, thread_ts=None):
        self.posts.append((channel, text, thread_ts))
        return "123.456"

    def history(self, channel):
        return self.messages

    def replies(self, channel, ts):
        return self.thread_replies.get(ts, [])


def test_zero_companies_gates_and_stops():
    crm, slack = FakeMonday(), FakeSlack()
    out = process_meeting(
        crm, slack, meeting_id="m1", title="Internal",
        attendee_emails=["alex@harboradvisors.example", "bot@fireflies.ai"],
    )
    assert out.state == "Gated" and out.ctx is None
    assert crm.gated == [("m1", "Internal")]
    assert crm.accounts == {} and slack.posts == []


def test_one_company_resolves_or_creates_contacts_and_marks_ready():
    crm = FakeMonday(deals={"42": "d7"})
    crm.accounts["acme.com"] = {"id": "42", "name": "Acme"}
    slack = FakeSlack()
    out = process_meeting(
        crm, slack, meeting_id="m2", title="Acme intro",
        attendee_emails="alex@harboradvisors.example, jane@acme.com; joe@acme.com",
        attendee_names={"jane@acme.com": "Jane Smith"},
    )
    assert out.state == "Ready"
    assert out.ctx == {
        "meeting_id": "m2", "prospect_account_id": "42",
        "partner_account_id": None, "deal_id": "d7",
        "contact_ids": ["101", "102"],
        "contact_email_ids": {"jane@acme.com": "101", "joe@acme.com": "102"},
    }
    assert crm.ready[0][1] == ["42"]
    assert crm.ready[0][5]["jane@acme.com"] == "101"
    assert crm.contacts["jane@acme.com"]["name"] == "Jane Smith"
    assert crm.deal_contact_links == [(["101", "102"], "d7")]
    assert slack.posts == []


def test_multiple_companies_waits_then_resolves_every_company():
    crm, slack = FakeMonday(), FakeSlack()
    args = dict(
        meeting_id="m3", title="Partner intro",
        attendee_emails=["a@prospect.com", "b@partner.com", "alex@harboradvisors.example"],
    )
    gated = process_meeting(crm, slack, **args)
    assert gated.state == "Gated" and gated.slack_ts == "123.456"
    assert "[meeting:m3]" in slack.posts[0][1]
    assert "partner.com" in slack.posts[0][1] and "prospect.com" in slack.posts[0][1]

    ready = process_meeting(crm, slack, prospect_choice="prospect.com", **args)
    assert ready.state == "Ready"
    assert ready.ctx["prospect_account_id"] == crm.accounts["prospect.com"]["id"]
    assert ready.ctx["partner_account_id"] == crm.accounts["partner.com"]["id"]
    assert crm.ready[-1][1] == [
        crm.accounts["prospect.com"]["id"], crm.accounts["partner.com"]["id"]
    ]


def test_gated_meeting_resumes_from_numbered_slack_reply_without_reposting():
    crm, slack = FakeMonday(), FakeSlack()
    slack.messages = [{"ts": "9.0", "text": "[meeting:m4]\n1. partner.com\n2. prospect.com",
                       "bot_id": "B1"}]
    slack.thread_replies["9.0"] = [
        slack.messages[0], {"ts": "9.1", "text": "2", "user": "U1"},
    ]
    out = resume_from_slack(
        crm, slack, meeting_id="m4", title="Intro",
        attendee_emails=["p@partner.com", "x@prospect.com"],
    )
    assert out.state == "Ready"
    assert out.ctx["prospect_account_id"] == crm.accounts["prospect.com"]["id"]
    assert slack.posts == []


def test_background_mapping_creates_contact_and_marks_meeting_ready():
    crm, slack = FakeMonday(), FakeSlack()
    slack.thread_replies["9.0"] = [
        {"ts": "9.0", "text": "bot", "bot_id": "B1"},
        {"ts": "9.1", "text": (
            "Company domain: acme.com\n"
            "Contact: Jane Smith <jane@acme.com>\n"
            "Notes:\nBudget approved."
        ), "user": "U1"},
    ]
    out = resume_background_from_slack(
        crm, slack, meeting_id="m5", title="Background recording",
        attendee_emails="alex@harboradvisors.example", prompt_ts="9.0",
    )
    assert out.state == "Ready"
    assert out.ctx["prospect_account_id"] == crm.accounts["acme.com"]["id"]
    assert crm.contacts["jane@acme.com"]["name"] == "Jane Smith"
    assert crm.saved_attendees[0][1] == [
        "alex@harboradvisors.example", "jane@acme.com",
    ]
    assert crm.ready[-1][4] == "External"


def test_background_scrap_tombstones_meeting_and_confirms_in_thread():
    crm, slack = FakeMonday(), FakeSlack()
    slack.thread_replies["9.0"] = [
        {"ts": "9.1", "text": "scrap", "user": "U1"},
    ]
    out = resume_background_from_slack(
        crm, slack, meeting_id="m6", title="Do not keep",
        attendee_emails="alex@harboradvisors.example", prompt_ts="9.0",
    )
    assert out.state == "Scrapped" and crm.scrapped == ["m6"]
    assert slack.posts[-1][2] == "9.0"
    assert "not be processed or imported again" in slack.posts[-1][1]


def test_background_identity_accepts_company_domain_with_freemail_contact():
    parsed = parse_background_identity(
        "Company domain: acme.com\nContact: Jane Smith <jane@gmail.com>")
    assert parsed == ("acme.com", ["jane@gmail.com"], {"jane@gmail.com": "Jane Smith"})


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
