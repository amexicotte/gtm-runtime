# Harbor GTM Runtime

Production polling runtime for Harbor's Fireflies → Slack → monday CRM meeting
engine. It uses deterministic matching and state management around exactly one
reasoning call per meeting.

## What it does

- Polls Fireflies every 15 minutes and deduplicates team-member copies.
- Matches Accounts from external attendee email domains and Contacts from exact
  attendee email addresses.
- Prompts in `#call-notes` with Fireflies' summary, then combines replies from
  that exact Slack thread with the transcript.
- Accepts unrecorded calls as top-level `manual:` posts in `#call-notes`.
  DeepSeek extracts the company/contact identity, writes the same final CRM updates,
  and creates the same monday Activities without requiring a transcript.
- Creates or reuses at most one active Deal per Account.
- Writes extracted CRM values as final and marks affected records `Verified`;
  no monday review queue is part of the flow.
- Normalizes new meeting/manual-note reasoning into a durable Agent Event and
  posts one retry-safe narrative Update to each matched CRM record.
- Posts the agent's listening summary and next steps back to the originating
  Slack thread, where `score:` and `feedback:` replies are captured for evaluation.
- Creates assigned monday Activities for meeting commitments and posts one
  concise line per open task to `#deal-alerts`. A ✅ reaction marks the already-final
  Activity Done in monday, then deletes the bot post from Slack.
- Runs a separate 2:00 a.m. ET monday E&A listener for email-thread narrative,
  exact-evidence Activity reconciliation, last-interaction stamps, and 36-hour /
  two-day follow-up nudges. It defaults to read-only ingestion until email
  reasoning is explicitly approved, then remains shadow-only until three real
  nightly reports are reviewed and the live-write gates are set.

## Run locally

```bash
python -m venv engine/.venv
engine/.venv/bin/pip install -r engine/requirements.txt
set -a; source engine/.env; set +a
PYTHONPATH=engine engine/.venv/bin/python -m runtime.schema_main
PYTHONPATH=engine engine/.venv/bin/python -m runtime.monday_main --dry-run
```

Never commit `engine/.env`; it and all `.env.*` variants are ignored.

## Deployment

The GitHub Actions workflow is in
`.github/workflows/fireflies-monday-poller.yml`. Configure these repository secrets:

- `MONDAY_API_KEY` (provided to the runtime as `MONDAY_API_TOKEN`)
- `FIREFLIES_API_KEY`
- `SLACK_BOT_TOKEN`
- `OPENROUTER_API_KEY`

Optional repository variables are `OPENROUTER_MODEL`,
`MONDAY_DEFAULT_OWNER_ID` (Alex is the default), and
`MONDAY_AGENT_EVENTS_BOARD_ID`. `BUSINESS_EVENTS_ACTIVATED_AT` is an explicit
timezone-aware cutover and remains blank/disabled until Phase 1 write validation.

After adding secrets, manually run the workflow once with `setup_schema=true`
and `dry_run=true`. That creates the managed columns and validates
connectivity without posting to Slack or calling the model. Scheduled runs then
reconcile new transcripts and Slack replies idempotently. Ingestion is capped
separately from model work: a poll can ingest up to `MAX_MEETINGS_PER_RUN` new
transcripts while only `MAX_REASONING_PER_RUN` released meetings call the model.
This keeps the Fireflies → Slack notification path from being starved by a
reasoning backlog.

The Phase 2 workflow is `.github/workflows/email-listening-nightly.yml`. Its
activation and approval sequence is documented in
`docs/phase-2-email-listening-activity-reconciliation-and-nudges.md`.

## Unrecorded calls from Slack

Post a new root message in `#call-notes` beginning with `manual:` (do not put it
inside an existing Fireflies thread). Everything after the prefix is free-form:

```text
manual: Acme check-in
Spoke with Jane. Budget is approved and she wants a proposal next Friday.
I owe her the scope; she will send the current pipeline export.
```

The bot acknowledges the note in-thread, creates an idempotent manual Meeting,
and uses one DeepSeek call to extract identity, final CRM updates, and action items. It
matches Accounts by exact domain/name and Contacts by exact email/name; missing
records are created as final, `Verified` records. If the company cannot be identified
safely, the bot asks for a domain/contact in that same thread and reuses the
cached model result after the answer.
