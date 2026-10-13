"""Won Deal lifecycle reconciliation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.engagements import reconcile_won_engagements  # noqa: E402


class CRM:
    def __init__(self, *, blocked=False, account_type="Prospect",
                 project_active=True):
        self.blocked = blocked
        self._account_type = account_type
        self.projects = []
        self.activated = []
        self.completed = []
        self.flagged = []
        self.project_active = project_active

    def won_deals_for_engagements(self):
        return [{"id": "d1", "name": "Acme", "account_id": "a1",
                 "contact_ids": ["c1"]}]
    def ensure_client_project(self, **kwargs):
        if self.blocked:
            raise RuntimeError("USER_UNAUTHORIZED")
        self.projects.append(kwargs)
        return "p1"
    def account_type(self, account): return self._account_type
    def client_projects_for_account(self, account):
        return [{"id": "p1", "active": self.project_active}]
    def set_account_active_client(self, account): self.activated.append(account)
    def set_account_completed_past_client(self, account): self.completed.append(account)
    def flag_client_project_needed(self, deal): self.flagged.append(deal)


def test_won_deal_ensures_project_before_activating_account():
    crm = CRM()
    stats = reconcile_won_engagements(crm)
    assert crm.projects[0]["deal_id"] == "d1"
    assert crm.activated == ["a1"]
    assert stats.projects_created_or_found == 1 and stats.accounts_activated == 1


def test_blocked_project_never_marks_account_active():
    crm = CRM(blocked=True)
    stats = reconcile_won_engagements(crm)
    assert crm.activated == [] and crm.flagged == ["d1"]
    assert stats.blocked == 1


def test_all_inactive_projects_complete_the_account_without_changing_won_deals():
    crm = CRM(account_type="Active Client", project_active=False)
    stats = reconcile_won_engagements(crm)
    assert crm.activated == [] and crm.completed == ["a1"]
    assert stats.accounts_completed == 1


def test_completed_past_client_reconciliation_is_idempotent():
    crm = CRM(account_type="Completed - Past Client", project_active=False)
    stats = reconcile_won_engagements(crm)
    assert crm.activated == [] and crm.completed == []
    assert stats.accounts_completed == 0


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
