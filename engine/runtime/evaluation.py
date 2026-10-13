"""Human feedback capture for model-agnostic meeting-reasoning evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class FeedbackStats:
    scanned: int = 0
    saved: int = 0


def ensure_agent_review_posts(slack, crm, *, channel: str = "call-notes",
                              dry_run: bool = False,
                              meeting_ids: set[str] | None = None) -> int:
    """Post one durable quality-feedback prompt for each completed reasoning run."""
    if not hasattr(crm, "save_reasoning_review_ts"):
        return 0
    posted = 0
    meetings = (crm.meetings_for_reasoning_feedback()
                if hasattr(crm, "meetings_for_reasoning_feedback")
                else crm.meetings_for_call_notes())
    for meeting in meetings:
        if meeting_ids is not None and str(meeting["id"]) not in meeting_ids:
            continue
        if (not meeting.get("reasoning_result") or
                not meeting.get("call_notes_prompt_ts") or
                meeting.get("reasoning_review_ts")):
            continue
        if dry_run:
            posted += 1
            continue
        ts = slack.post(
            channel, agent_review_message(meeting["reasoning_result"]),
            thread_ts=meeting["call_notes_prompt_ts"])
        crm.save_reasoning_review_ts(str(meeting["id"]), str(ts))
        posted += 1
    return posted


def agent_review_message(result: dict) -> str:
    """Share the final listener output next to the human's notes in one thread."""
    meeting = result.get("meeting") or {}
    summary = _value(meeting.get("summary")) or "No summary returned."
    actions = _value(meeting.get("action_items")) or []
    if isinstance(actions, str):
        actions = [line.strip(" •-\t") for line in actions.splitlines()
                   if line.strip(" •-\t")]
    action_text = "\n".join(f"• {item}" for item in actions) or "• None returned"
    return (
        "*Agent listening feedback*\n"
        f"{summary}\n\n*Important next steps*\n{action_text}\n\n"
        "Compare this with your own notes. Reply with `score: 1-5` and/or "
        "`feedback: what it missed, misunderstood, or overemphasized`. The "
        "feedback is stored with this model run so prompts and future models "
        "can be benchmarked against the same calls."
    )


def sync_reasoning_feedback(slack, crm, *, channel: str = "call-notes",
                            dry_run: bool = False) -> FeedbackStats:
    stats = FeedbackStats()
    meetings = (crm.meetings_for_reasoning_feedback()
                if hasattr(crm, "meetings_for_reasoning_feedback")
                else crm.meetings_for_call_notes())
    for meeting in meetings:
        if (not meeting.get("reasoning_result") or
                not meeting.get("call_notes_prompt_ts") or
                not meeting.get("reasoning_review_ts")):
            continue
        root = str(meeting["call_notes_prompt_ts"])
        oldest = str(meeting.get("feedback_last_ts") or
                     meeting.get("call_notes_last_ts") or root)
        messages = slack.replies(channel, root, oldest_ts=oldest)
        stats.scanned += len(messages)
        feedback = []
        score = str(meeting.get("reasoning_score") or "")
        highest = oldest
        for message in sorted(messages, key=lambda row: float(row.get("ts") or 0)):
            ts = str(message.get("ts") or "")
            if ts and _newer(ts, highest):
                highest = ts
            if (not ts or ts == root or message.get("bot_id") or
                    message.get("subtype")):
                continue
            text = (message.get("text") or "").strip()
            score_match = re.search(r"(?im)^\s*score\s*:\s*([1-5])\b", text)
            if score_match:
                score = score_match.group(1)
            feedback_match = re.search(
                r"(?is)(?:^|\n)\s*feedback\s*:\s*(.+)$", text)
            if feedback_match:
                feedback.append(feedback_match.group(1).strip())
        if not feedback and score == str(meeting.get("reasoning_score") or ""):
            continue
        combined = "\n\n".join(value for value in [
            (meeting.get("reasoning_feedback") or "").strip(), *feedback] if value)
        if not dry_run:
            crm.save_reasoning_feedback(str(meeting["id"]), combined, score, highest)
        stats.saved += 1
    return stats


def _value(value):
    if not isinstance(value, dict):
        return value
    if str(value.get("status") or "").upper() == "NOT_DISCUSSED":
        return None
    return value.get("value")


def _newer(left: str, right: str) -> bool:
    try:
        return float(left) > float(right)
    except (TypeError, ValueError):
        return left > right
