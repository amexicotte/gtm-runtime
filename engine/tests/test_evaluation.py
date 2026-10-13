"""Agent review rendering and human feedback capture tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.evaluation import (  # noqa: E402
    agent_review_message, ensure_agent_review_posts, sync_reasoning_feedback,
)


class Slack:
    def __init__(self): self.posts = []
    def post(self, channel, text, thread_ts=None):
        self.posts.append((channel, text, thread_ts)); return "200.0"
    def replies(self, channel, root, oldest_ts=None):
        return [
            {"ts": root, "text": "prompt", "bot_id": "B1"},
            {"ts": "201.0", "text": "score: 3\nfeedback: missed the budget", "user": "U1"},
        ]


class CRM:
    def __init__(self): self.review_ts = []; self.feedback = []
    def meetings_for_reasoning_feedback(self):
        return [{
            "id": "m1", "status": "Processed", "call_notes_prompt_ts": "100.0",
            "call_notes_last_ts": "101.0", "feedback_last_ts": "",
            "reasoning_review_ts": self.review_ts[-1][1] if self.review_ts else "",
            "reasoning_feedback": "", "reasoning_score": "",
            "reasoning_result": {"meeting": {
                "summary": "Useful call", "action_items": ["Send scope"]}},
        }]
    def meetings_for_call_notes(self):
        raise AssertionError("feedback must include terminal Processed meetings")
    def save_reasoning_review_ts(self, meeting, ts): self.review_ts.append((meeting, ts))
    def save_reasoning_feedback(self, *args): self.feedback.append(args)


def test_feedback_message_shares_agent_notes_and_feedback_format():
    message = agent_review_message({"meeting": {
        "summary": "Useful call", "action_items": ["Send scope"]}})
    assert "Agent listening feedback" in message
    assert "Useful call" in message and "• Send scope" in message
    assert "score: 1-5" in message and "feedback:" in message


def test_review_posts_once_then_feedback_is_persisted():
    slack, crm = Slack(), CRM()
    assert ensure_agent_review_posts(slack, crm, meeting_ids={"m1"}) == 1
    assert crm.review_ts == [("m1", "200.0")]
    stats = sync_reasoning_feedback(slack, crm)
    assert stats.saved == 1
    assert crm.feedback == [("m1", "missed the budget", "3", "201.0")]


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests: test()
    print(f"{len(tests)}/{len(tests)} tests passed")
