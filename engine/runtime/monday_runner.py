"""One complete polling cycle for Fireflies -> Slack notes -> final monday writes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import hashlib

from matcher import parse_domains

from .call_notes import (
    ensure_call_note_prompts,
    release_transcript_fallbacks,
    sync_call_note_threads,
)
from .activities import create_activities_from_result
from .activity_alerts import sync_activity_alerts
from .config import config
from .fireflies import Fireflies, transcript_text
from .fireflies_sync import sync_recent
from .engagements import reconcile_won_engagements
from .evaluation import ensure_agent_review_posts, sync_reasoning_feedback
from .gate import (
    process_meeting, resume_background_from_slack, resume_from_slack,
)
from .manual_notes import (
    ManualIdentityError,
    manual_identity_prompt,
    manual_reason_and_write,
    resume_manual_write,
    sync_manual_note_posts,
)
from .monday import Monday
from .monday_pipeline import reason_and_write, resume_reasoning_write
from .slack import Slack


@dataclass
class PollStats:
    manual_notes_candidates: int = 0
    manual_meetings_created: int = 0
    manual_meetings_reasoned: int = 0
    manual_identity_gated: int = 0
    fireflies_fetched: int = 0
    fireflies_duplicates: int = 0
    meetings_created: int = 0
    meetings_candidates: int = 0
    gates_ready: int = 0
    gates_waiting: int = 0
    meetings_scrapped: int = 0
    notes_prompted: int = 0
    slack_replies_scanned: int = 0
    notes_attached: int = 0
    account_types_updated: int = 0
    fallbacks_released: int = 0
    meetings_reasoned: int = 0
    meetings_resumed: int = 0
    meetings_skipped_no_transcript: int = 0
    meetings_flagged_no_transcript: int = 0
    meeting_errors: int = 0
    meetings_waiting: int = 0
    activities_created: int = 0
    activity_alerts_posted: int = 0
    activities_completed_from_slack: int = 0
    won_deals_scanned: int = 0
    client_projects_reconciled: int = 0
    accounts_activated: int = 0
    accounts_completed: int = 0
    engagement_reconciliation_blocked: int = 0
    agent_reviews_posted: int = 0
    reasoning_feedback_scanned: int = 0
    reasoning_feedback_saved: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def run_monday_poll(*, dry_run: bool = False, crm=None, slack=None,
                    fireflies=None, lookback_days: int | None = None,
                    max_reasoning: int | None = None,
                    reasoner=reason_and_write,
                    resumer=resume_reasoning_write,
                    manual_reasoner=manual_reason_and_write,
                    manual_resumer=resume_manual_write) -> PollStats:
    """Run one idempotent polling/reconciliation cycle."""
    if not dry_run:
        config.require_business_writes()
    crm = crm or Monday()
    slack = slack or Slack()
    fireflies = fireflies or Fireflies()
    lookback_days = lookback_days or config.FIREFLIES_LOOKBACK_DAYS
    max_reasoning = max_reasoning or config.MAX_REASONING_PER_RUN
    stats = PollStats()
    reasoned_ids: set[str] = set()

    # Close completed tasks first so the rest of the run sees current CRM state.
    if not dry_run and hasattr(crm, "activities_for_alerts"):
        activity_alerts = sync_activity_alerts(
            slack, crm, channel=config.DEAL_ALERTS_CHANNEL, dry_run=False)
        stats.activity_alerts_posted += activity_alerts.notified
        stats.activities_completed_from_slack = activity_alerts.completed

    if hasattr(crm, "existing_slack_intake_ids"):
        manual_sync = sync_manual_note_posts(
            slack, crm, channel=config.CALL_NOTES_CHANNEL,
            lookback_days=config.MANUAL_NOTES_LOOKBACK_DAYS, dry_run=dry_run)
        stats.manual_notes_candidates = manual_sync.candidates
        stats.manual_meetings_created = manual_sync.created

    synced = sync_recent(
        fireflies, crm, days=lookback_days, dry_run=dry_run,
        max_meetings=config.MAX_MEETINGS_PER_RUN,
        not_before=config.FIREFLIES_NOT_BEFORE,
    )
    stats.fireflies_fetched = synced.fetched
    stats.fireflies_duplicates = synced.duplicates
    stats.meetings_created = synced.created
    stats.meetings_candidates = len(synced.candidates)
    if dry_run:
        # Gate resolution, prompts, monday writes, and model calls are all
        # intentionally omitted from a dry run.
        return stats

    engagement_stats = reconcile_won_engagements(crm, dry_run=False)
    stats.won_deals_scanned = engagement_stats.won_deals_scanned
    stats.client_projects_reconciled = engagement_stats.projects_created_or_found
    stats.accounts_activated = engagement_stats.accounts_activated
    stats.accounts_completed = engagement_stats.accounts_completed
    stats.engagement_reconciliation_blocked = engagement_stats.blocked

    for meeting in crm.meetings_for_call_notes():
        if meeting.get("status") == "Awaiting":
            outcome = process_meeting(
                crm, slack, meeting_id=meeting["id"], title=meeting["title"],
                attendee_emails=meeting.get("attendee_emails") or "",
                attendee_names=meeting.get("attendee_names") or {},
                alerts_channel=config.DEAL_ALERTS_CHANNEL,
            )
            if outcome.state == "Ready":
                stats.gates_ready += 1
            else:
                stats.gates_waiting += 1
        elif meeting.get("status") == "Gated":
            domains = parse_domains(_email_lines(meeting.get("attendee_emails") or ""))[
                "external"]
            if not domains and meeting.get("call_notes_prompt_ts"):
                outcome = resume_background_from_slack(
                    crm, slack, meeting_id=meeting["id"], title=meeting["title"],
                    attendee_emails=meeting.get("attendee_emails") or "",
                    attendee_names=meeting.get("attendee_names") or {},
                    prompt_ts=meeting["call_notes_prompt_ts"],
                    call_notes_channel=config.CALL_NOTES_CHANNEL,
                    alerts_channel=config.DEAL_ALERTS_CHANNEL,
                )
                if outcome.state == "Ready":
                    stats.gates_ready += 1
                elif outcome.state == "Scrapped":
                    stats.meetings_scrapped += 1
                else:
                    stats.gates_waiting += 1
            elif len(domains) > 1:
                outcome = resume_from_slack(
                    crm, slack, meeting_id=meeting["id"], title=meeting["title"],
                    attendee_emails=meeting.get("attendee_emails") or "",
                    attendee_names=meeting.get("attendee_names") or {},
                    alerts_channel=config.DEAL_ALERTS_CHANNEL,
                )
                if outcome.state == "Ready":
                    stats.gates_ready += 1
                else:
                    stats.gates_waiting += 1

    prompted = ensure_call_note_prompts(
        slack, crm, channel=config.CALL_NOTES_CHANNEL, dry_run=False)
    stats.notes_prompted = prompted.prompted
    collected = sync_call_note_threads(
        slack, crm, channel=config.CALL_NOTES_CHANNEL, dry_run=False)
    stats.slack_replies_scanned = collected.scanned
    stats.notes_attached = collected.attached
    stats.account_types_updated = collected.account_types_updated
    stats.fallbacks_released = len(release_transcript_fallbacks(
        crm, hours=config.NOTES_FALLBACK_HOURS, dry_run=False))

    for meeting in crm.meetings_for_call_notes():
        cached_result = meeting.get("reasoning_result")
        if (meeting.get("status") == "Processing" and not cached_result and
                _processing_claim_is_stale(
                    meeting.get("processing_started_at") or "",
                    config.PROCESSING_STALE_MINUTES)):
            crm.change_columns("meeting", meeting["id"], {
                "Processing Status": "Ready",
                "Processing Started At": "",
            })
            meeting["status"] = "Ready"
        integrity_error = _manual_notes_integrity_error(meeting)
        if (meeting.get("status") == "Ready" and
                meeting.get("notes_status") == "Received" and integrity_error):
            crm.change_columns("meeting", meeting["id"], {
                "Processing Status": "Processed",
            })
            if meeting.get("call_notes_prompt_ts"):
                try:
                    slack.post(
                        config.CALL_NOTES_CHANNEL,
                        f"🚨 I stopped before reasoning because {integrity_error}. "
                        "The model was not called; the original Slack replies remain "
                        "the source of truth.",
                        thread_ts=meeting["call_notes_prompt_ts"],
                    )
                except RuntimeError:
                    pass
            stats.meeting_errors += 1
            continue
        is_manual = bool(meeting.get("slack_intake_ts"))
        manual_recovery = (
            is_manual and isinstance(cached_result, dict) and bool(cached_result)
            and meeting.get("status") in ("Processing", "Ready")
        )
        manual_ready = (
            is_manual and meeting.get("status") == "Ready"
            and meeting.get("notes_status") == "Received"
        )
        if manual_ready or manual_recovery:
            if not manual_recovery and stats.meetings_reasoned >= max_reasoning:
                break
            try:
                if manual_recovery:
                    manual_outcome = manual_resumer(
                        crm, result=cached_result,
                        meeting_title=meeting["title"], meeting=meeting)
                    stats.meetings_resumed += 1
                else:
                    # Count the bounded model slot before downstream identity or
                    # monday writes can fail; the OpenRouter call is already spent.
                    stats.meetings_reasoned += 1
                    stats.manual_meetings_reasoned += 1
                    manual_outcome = manual_reasoner(
                        crm, notes=meeting.get("manual_notes") or "",
                        meeting_title=meeting["title"],
                        meeting_date=meeting.get("date") or "", meeting=meeting)
                result = manual_outcome.result
                ctx = manual_outcome.ctx
                activities = []
                slack_link = ""
                if hasattr(slack, "permalink"):
                    try:
                        slack_link = slack.permalink(
                            config.CALL_NOTES_CHANNEL, meeting["slack_intake_ts"])
                    except RuntimeError:
                        pass
                if (isinstance(result, dict) and result.get("relevant") is True and
                        hasattr(crm, "create_activity")):
                    try:
                        activities = create_activities_from_result(
                            crm, meeting, ctx, result, slack_link=slack_link)
                        stats.activities_created += len(activities)
                    except Exception:
                        # Keep the cached model result recoverable until every
                        # extracted action item has had a chance to materialize.
                        crm.change_columns("meeting", str(meeting["id"]), {
                            "Processing Status": "Processing",
                        })
                        raise
                company = (crm.item_name(ctx["prospect_account_id"])
                           if ctx.get("prospect_account_id") and
                           hasattr(crm, "item_name") else "the matched Account")
                try:
                    slack.post(
                        config.CALL_NOTES_CHANNEL,
                        f"Done — matched to *{company}*, finalized the CRM updates, "
                        f"and created {len(activities)} action item(s). Use the "
                        "listening review below for feedback.",
                        thread_ts=meeting["slack_intake_ts"],
                    )
                except RuntimeError:
                    pass
                reasoned_ids.add(str(meeting["id"]))
            except ManualIdentityError as error:
                crm.set_meeting_gated(str(meeting["id"]), "External")
                try:
                    slack.post(
                        config.CALL_NOTES_CHANNEL, manual_identity_prompt(error),
                        thread_ts=meeting["slack_intake_ts"],
                    )
                except RuntimeError:
                    # Retry the cached resolver/prompt on the next poll instead
                    # of leaving an invisible gate in monday.
                    crm.change_columns("meeting", str(meeting["id"]), {
                        "Processing Status": "Ready",
                    })
                stats.manual_identity_gated += 1
            except Exception as error:
                stats.meeting_errors += 1
                print(
                    f"  ! manual meeting {meeting['id']} ({meeting['title']}) "
                    f"failed; continuing poll: {error}")
            continue
        is_recovery = (meeting.get("status") == "Processing" and
                       isinstance(cached_result, dict) and bool(cached_result))
        is_ready = (meeting.get("status") == "Ready" and
                    meeting.get("notes_status") in ("Received", "Fallback") and
                    not is_manual)
        if not is_ready and not is_recovery:
            if meeting.get("status") in ("Awaiting", "Ready", "Gated"):
                stats.meetings_waiting += 1
            continue
        if not is_recovery and stats.meetings_reasoned >= max_reasoning:
            break
        ctx = _context(meeting, crm)
        if not ctx.get("prospect_account_id"):
            stats.meetings_waiting += 1
            continue
        result = None
        try:
            if is_recovery:
                result = resumer(
                    crm, result=cached_result, meeting_title=meeting["title"], ctx=ctx)
                stats.meetings_resumed += 1
            else:
                if not meeting.get("fireflies_id"):
                    stats.meetings_waiting += 1
                    continue
                detail = fireflies.get_transcript(meeting["fireflies_id"])
                external_names = [
                    str(row.get("name") or "")
                    for row in ((ctx.get("crm_snapshot") or {}).get("contacts") or [])
                    if row.get("name")
                ]
                speaker_identity: dict = {}
                transcript = transcript_text(
                    detail, external_attendee_names=external_names,
                    identity_audit=speaker_identity)
                ctx["speaker_identity"] = speaker_identity
                if not transcript.strip():
                    stats.meetings_skipped_no_transcript += 1
                    expired = _transcript_wait_expired(
                        meeting, config.TRANSCRIPT_WAIT_HOURS)
                    if expired:
                        crm.change_columns("meeting", meeting["id"], {
                            "Processing Status": "Processed",
                        })
                        stats.meetings_flagged_no_transcript += 1
                        if meeting.get("call_notes_prompt_ts"):
                            try:
                                slack.post(
                                    config.CALL_NOTES_CHANNEL,
                                    "⚠️ Fireflies still has no transcript text after "
                                    f"{config.TRANSCRIPT_WAIT_HOURS} hours. I closed this "
                                    "meeting without CRM extraction instead of retrying it forever.",
                                    thread_ts=meeting["call_notes_prompt_ts"],
                                )
                            except RuntimeError:
                                pass
                    print(
                        f"  ! meeting {meeting['id']} ({meeting['title']}) has "
                        "no Fireflies transcript text; "
                        + ("closed without extraction" if expired else "deferred"))
                    continue
                result = reasoner(
                    crm,
                    transcript=transcript,
                    meeting_title=meeting["title"],
                    meeting_date=meeting.get("date") or "",
                    ctx=ctx,
                    manual_notes=meeting.get("manual_notes") or "",
                    notes_status=meeting["notes_status"],
                )
                stats.meetings_reasoned += 1
            if (isinstance(result, dict) and result.get("relevant") is True and
                    hasattr(crm, "create_activity")):
                slack_link = ""
                if meeting.get("call_notes_prompt_ts") and hasattr(slack, "permalink"):
                    try:
                        slack_link = slack.permalink(
                            config.CALL_NOTES_CHANNEL, meeting["call_notes_prompt_ts"])
                    except RuntimeError:
                        pass
                try:
                    activities = create_activities_from_result(
                        crm, meeting, ctx, result, slack_link=slack_link)
                    stats.activities_created += len(activities)
                except Exception:
                    # The cached reasoning result must remain resumable when a
                    # downstream Activity mutation has an ambiguous failure.
                    crm.change_columns("meeting", str(meeting["id"]), {
                        "Processing Status": "Processing",
                    })
                    raise
            if isinstance(result, dict):
                reasoned_ids.add(str(meeting["id"]))
        except Exception as error:
            stats.meeting_errors += 1
            print(
                f"  ! meeting {meeting['id']} ({meeting['title']}) failed; "
                f"continuing poll: {error}")
            continue
    # Sweep after reasoning so newly created tasks are announced in the same run.
    if not dry_run and hasattr(crm, "activities_for_alerts"):
        activity_alerts = sync_activity_alerts(
            slack, crm, channel=config.DEAL_ALERTS_CHANNEL, dry_run=False)
        stats.activity_alerts_posted += activity_alerts.notified
    if not dry_run:
        stats.agent_reviews_posted = ensure_agent_review_posts(
            slack, crm, channel=config.CALL_NOTES_CHANNEL, dry_run=False,
            meeting_ids=reasoned_ids)
        feedback = sync_reasoning_feedback(
            slack, crm, channel=config.CALL_NOTES_CHANNEL, dry_run=False)
        stats.reasoning_feedback_scanned = feedback.scanned
        stats.reasoning_feedback_saved = feedback.saved
    return stats


def _context(meeting: dict, crm=None) -> dict:
    accounts = [str(value) for value in meeting.get("account_ids") or []]
    prospect = str(meeting.get("prospect_account_id") or "")
    if not prospect and len(accounts) == 1:
        prospect = accounts[0]
    partners = [str(value) for value in meeting.get("partner_account_ids") or []]
    if not partners and prospect:
        partners = [value for value in accounts if value != prospect]
    deals = [str(value) for value in meeting.get("deal_ids") or []]
    ctx = {
        "meeting_id": str(meeting["id"]),
        "prospect_account_id": prospect or None,
        "partner_account_id": partners[0] if partners else None,
        "deal_id": deals[0] if deals else None,
        "contact_ids": [str(value) for value in meeting.get("contact_ids") or []],
        "contact_email_ids": {
            str(email).lower(): str(item_id)
            for email, item_id in (meeting.get("contact_email_ids") or {}).items()
        },
        "attendee_emails": _email_lines(meeting.get("attendee_emails") or ""),
    }
    if len(partners) > 1:
        ctx["partner_account_ids"] = partners
    if prospect and crm is not None and hasattr(crm, "reasoning_context"):
        ctx["crm_snapshot"] = crm.reasoning_context(
            prospect, ctx["contact_ids"])
    return ctx


def _email_lines(value: str) -> list[str]:
    return [part.strip() for part in str(value).replace(",", "\n").splitlines()
            if part.strip()]


def _manual_notes_integrity_error(meeting: dict) -> str:
    """Return a blocking message when monday did not preserve the Slack payload."""
    expected_chars = str(meeting.get("manual_notes_chars") or "").strip()
    expected_sha = str(meeting.get("manual_notes_sha256") or "").strip().lower()
    if not expected_chars and not expected_sha:
        return ""  # Legacy row created before lossless integrity metadata.
    notes = str(meeting.get("manual_notes") or "")
    if expected_chars:
        try:
            wanted = int(expected_chars)
        except ValueError:
            return "the saved manual-note character count is invalid"
        if len(notes) != wanted:
            return (f"the manual-note cache contains {len(notes):,} of the "
                    f"expected {wanted:,} characters")
    if expected_sha:
        actual = hashlib.sha256(notes.encode("utf-8")).hexdigest()
        if actual != expected_sha:
            return "the manual-note cache checksum does not match Slack"
    return ""


def _processing_claim_is_stale(value: str, minutes: int, *,
                               now: dt.datetime | None = None) -> bool:
    """Return true when an uncached Processing claim is safe to retry."""
    if not value:
        return True
    try:
        started = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone.utc) - started.astimezone(
        dt.timezone.utc) >= dt.timedelta(minutes=minutes)


def _transcript_wait_expired(meeting: dict, hours: int, *,
                             now: dt.datetime | None = None) -> bool:
    """Stop retrying a permanent Fireflies shell after a bounded wait."""
    value = meeting.get("notes_requested_at") or meeting.get("date") or ""
    if not value:
        return False
    try:
        started = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone.utc) - started.astimezone(
        dt.timezone.utc) >= dt.timedelta(hours=hours)
