# Monday meeting orchestration — audited design

## What the Notion reference actually contains

The attached `harbor-gtm-engine` describes two variants:

1. A native-agent design: Fireflies creates the Meeting, a new Slack message
   attaches manual notes and releases the Meeting, and a 12-hour sweep releases
   transcript-only meetings.
2. An external GitHub Actions runtime: every run rereads three days of
   `#call-notes` history and immediately reasons over each ungated Meeting.

The second variant's code does not implement the first variant's full wait and
release state machine. It also has no transcript-arrival Slack prompt, no stored
channel high-water cursor, no Slack pagination, and no durable protection against
appending the same Slack note again.

## Monday flow

1. Fireflies transcript becomes ready.
2. Ingest and deduplicate team copies before creating one Monday Meeting.
3. Run the deterministic attendee-domain gate.
4. Post one `#call-notes` message containing `[meeting:<monday item id>]` and
   store its root Slack timestamp on the Meeting.
5. The user replies in that exact thread. Read only replies newer than the
   Meeting's stored `Call Notes Last TS`; also retain every consumed reply in
   `Slack Note IDs` for retry idempotency.
6. Store the combined human notes and mark `Notes Status = Received`.
7. If no notes arrive within 12 hours, mark `Notes Status = Fallback`.
8. Permit the single reasoning call only when the company gate is clear and
   Notes Status is `Received` or `Fallback`.
9. Claim the Meeting as `Processing` before calling the model, write final CRM
   values, and set `Processed`. Post the listening summary in the same Slack
   thread for scoring and feedback. Never automatically run the model again
   after a successful extraction.

Thread timestamps eliminate channel-history rereads and remove heuristic
company/title matching from the normal path.

## Event delivery versus polling

- Fireflies supports a signed `meeting.transcribed` webhook, but a live HTTPS
  receiver must be deployed. Ordinary webhooks cover meetings owned by the user
  who configured them; team-wide webhooks require Fireflies Enterprise/Super
  Admin. A reconciliation poll is still recommended for missed deliveries.
- Slack can deliver `message.channels` events to a public HTTPS receiver. The
  currently installed bot was also live-verified to read channel threads, so a
  scheduled fallback can poll only new replies per stored thread timestamp.
- The repository currently has no deployed receiver and its sole GitHub Actions
  workflow still invokes the legacy Notion runtime. Therefore Monday is not yet
  receiving live events or running on a schedule.

## Polling budget

Fireflies documents 50 API requests per day for Free/Pro and 60 per minute for
Business/Enterprise. A 30-minute business-hours reconciliation uses about 26
list requests per day, plus one detail request per processed meeting. A five-
minute Fireflies poll would exceed the Free/Pro daily quota. Polling itself does
not call the LLM; OpenRouter cost is incurred only by the single released
meeting reasoning pass.
