# Phase 2 — Email listening, Activity reconciliation, and nudges

**Contract:** `docs/harbor-business-listening-agent-prd-v2.md`
**Dependencies:** `docs/system-inventory-and-ownership.md` and
`docs/phase-1-shared-event-and-narrative-foundation.md`
**Implementation status:** complete, offline-verified, and deployed in scheduled
full-reasoning shadow mode. Two live read-only ingestion shadows passed on
2026-08-17, including one after final code validation. Automatic CRM/Slack effects
remain disabled behind the approval sequence below.

## Implemented

- `runtime/email_events.py` reads only monday Emails & Activities, normalizes
  HTML, removes quoted history/signatures, extracts supported links, filters
  internal/noise/automated mail, deduplicates timeline UUIDs across Contacts, and
  groups new messages by normalized subject plus exact external participants.
- Exact lowercase email resolves Contacts; their declared relations resolve at
  most one Account and one active Deal. Multiple Accounts are not guessed.
  Multiple active Deals block only Deal effects and remain visible in the report.
- A participant newly added to an already tracked email thread is now discovered
  deterministically. If the exact email does not exist and its business domain
  matches one existing Account, the live runner creates one verified Contact and
  relates it to that Account. It also relates the Contact to the Deal only when
  that Account has exactly one active Deal. The per-run index makes subsequent
  threads and retries reuse the same Contact; a final live exact-email lookup
  blocks duplicates. Shadow reports expose only aggregate proposed/created/
  conflict counts, never participant addresses.
- `runtime/email_runner.py` applies an explicit cutover plus a 72-hour overlap,
  bounded source/thread/reasoning caps, a durable live high-water/seen-ID state,
  cached Agent Event reasoning, and access-controlled shadow review reports that
  omit raw subjects, bodies, addresses, and evidence excerpts. Shadow mode never
  writes monday or Slack.
- The dedicated email prompt receives one thread's normalized new messages,
  current exact CRM context, and at most eight lexically related open Activities.
  Older quoted content cannot authorize effects.
- Account/Contact/Deal fields reuse the Phase 1 field-aware writer. Evidence must
  be a verbatim new-message excerpt; the writer still prohibits Won and rejects
  partial, unsupported, unbounded, or incomplete values.
- New Activities require an explicit, evidenced commitment/chase and dedupe by
  normalized title, source event, and related CRM records.
- Open-Activity Slack alerts wait until the Activity Due Date in
  America/New_York. The first poll on that date posts one alert; overdue and
  undated legacy tasks use the next-poll fallback. Premature alerts from the old
  immediate-post behavior are deleted and reset before their due date. A narrow
  daily workflow runs this accountability pass twice in redundant UTC slots, so
  it does not depend on the Fireflies/model poller; persisted Slack timestamps
  prevent duplicate posts.
- Automatic completion requires `completed`, an exact retrieved Activity ID, a
  unique lexical candidate, verbatim evidence from an outbound message, at least
  `0.90` confidence, and a link when the obligation implies delivery. Intent,
  partial progress, inbound claims, competing matches, and missing delivery
  evidence remain open.
- Completion writes Status, exact message timestamp, source type/event, source
  timeline UUID, evidence,
  confidence, and whether a client reply is expected. Retry effects are keyed in
  Agent Events, and email-created Activities join the existing `#deal-alerts`
  accountability loop.
- Every exact call/email event monotonically stamps Deal `Last interaction` and
  Contact `Last Interaction`. Incoming email also stamps an exact text timestamp
  used to suppress/clear unanswered-follow-up nudges.
- `runtime/nudges.py` proposes/posts one stable Slack condition per stale Deal or
  unanswered completed commitment. A newer interaction/reply deletes and clears
  the saved Slack nudge. Optional monday-user → Slack-user JSON enables mentions;
  otherwise the owner name is shown in `#deal-alerts`.
- `.github/workflows/email-listening-nightly.yml` is eligible to run at 02:00
  America/New_York only after explicit enablement and uploads an access-controlled
  30-day intended-output report plus a deterministic acceptance-review artifact.
  The review fails closed on runtime gaps, incomplete reasoning, prohibited Won,
  unsafe Activity confidence, ambiguity leakage, shadow effects, narrative hard
  limits, or exposed email addresses. Because GitHub
  cron uses UTC, it schedules both possible UTC offsets and an ET-hour guard makes
  the daylight-saving non-match a no-op. It defaults to ingestion-only shadow and
  cannot silently enable full reasoning or live effects.
- `engine/tests/fixtures/email-phase2-golden.json` is the versioned Phase 2
  email/task golden set. It asserts exact entity targets, field proposals,
  Activity creation/completion, narrative output, and prohibited ambiguous,
  inbound-only, unmatched-Contact, and Won actions without exposing live email.

## Live read-only shadow record

Two ad-hoc ingestion-only passes ran on 2026-08-17 with a lower bound of
2026-08-16T00:00:00-04:00. The final pass reported:

- 297 Contacts with email addresses scanned.
- 3,170 latest-page E&A timeline items read.
- Three post-cutover threads normalized and exactly matched.
- Four calendar/notification items excluded.
- Zero ambiguity, normalization failure, page-overlap gap, or cap reached.
- One 36-hour stalled-Deal nudge condition proposed.
- Zero model calls, monday mutations, Slack posts, or high-water writes.

Both passes produced the same counts. This proves the live reader/query shape,
normalization boundary, entity relations, Activity candidate retrieval, and
deterministic nudge scan. Neither was a full-reasoning production validation.

## Immediate production-validation status

The owner replaced the earlier three-night calendar wait with the complete
deterministic suite, one exact disposable-record live canary, and an identical
retry that must make zero reasoning calls and no additional changes. The legacy
three-night collector remains available but is not required for promotion.

The Harbor owner approved OpenRouter with `deepseek/deepseek-v4-pro` for this
processing on 2026-08-19. `EMAIL_REASONING_APPROVED=true` is now configured, so
scheduled production runs use full reasoning.

## Activation and handoff

Scheduled email runs are fail-closed behind the explicit repository variable
`EMAIL_NIGHTLY_ENABLED=true`. The schema, private Agent Events board, approved
cutover, provider/model, and shadow-mode variables were configured on 2026-08-19.
The remaining steps are the deterministic release suite, reversible live canary,
identical zero-call retry, cleanup, and immediate activation.

The reversible-write gate is complete. The repository setup script created one
temporary `[TEST - SAFE TO ARCHIVE]` Account (`10000000045`), Contact
(`10000000011`), Deal (`10000000046`), and open Activity (`10000000047`). Live
verification is allowlisted to that Contact and its linked test records only;
rollback archives those four exact IDs.

A deletable synthetic E&A custom activity and timeline item are also attached to
the test Contact. The adapter accepts that fixture only in explicit verification
mode with both the configured custom-activity ID and exact Contact allowlist;
scheduled runs ignore it. A non-counting DeepSeek shadow resolved only the test
Account/Contact/Deal, proposed test Deal `Discovery`, validated the exact test
Activity completion at `1.0`, and produced one narrative with no warnings.

1. Review this Phase 2 diff and the access-controlled ingestion report. Confirm the
   configured OpenRouter provider/model and data handling are approved for live
   client email content; only then set `EMAIL_REASONING_APPROVED=true`.
2. Satisfy the outstanding Phase 0 reversible-write gate. An Harbor CRM owner must
   designate disposable/reversible records before schema or write validation.
3. Manually run the nightly workflow with `setup_schema=true`, `live=false`.
   Record the returned private Agent Events board ID in
   `MONDAY_AGENT_EVENTS_BOARD_ID`. This installs only deterministic audit/nudge
   columns; it does not approve business-record writes.
4. Set `EMAIL_LISTENING_ACTIVATED_AT` to an approved timezone-aware cutover. Do
   not use a historical value merely to create test volume.
5. Pass the complete deterministic release validation and required CI.
6. Dispatch `phase2-rollout-supervisor.yml` with
   `immediate_validation=true`. It creates the fixture, resets only the four
   named disposable records, and captures preflight.
7. Run the exact allowlisted live canary. The runtime limits Contact scans and
   derived Account/Deal/nudge effects to Contact `10000000011` and fails if it is
   missing. Verify Agent Event resume/dedupe, one narrative Update, exact Activity
   audit fields, monotonic interaction timestamps, and Slack post cleanup.
8. Run the identical retry, require zero reasoning calls and no state change,
   clean the fixture, and commit `validation_mode: bounded_live_canary`. The
   nightly 02:00 ET workflow then consumes that committed live activation.

The runtime refuses live mode unless reasoning consent, explicit write approval,
the activation timestamp, and either the legacy shadow approval or the explicit
bounded-canary rollout approval are present.

## Conservative limitations to review in shadow

- monday's current API returns a latest E&A page and a cursor but exposes no
  supported timeline cursor-continuation argument. If all 25 returned items fall
  inside the overlap, the adapter reports a saturation gap and refuses high-water
  advancement rather than silently skipping mail.
- The supported timeline metadata exposes sender/recipient fields but no separate
  attachment object. Links present in the new-message HTML count as delivery
  evidence; an attachment without an exposed link is conservatively treated as
  missing and will not auto-complete a delivery Activity.
- Discovery depends on a tracked monday E&A timeline: it covers coworkers added
  to a thread already visible on an existing Contact. A completely standalone
  first email that monday does not associate with any existing CRM timeline is
  not visible to this reader and therefore cannot authorize Contact creation.
- A date-only legacy `Last interaction` is treated as end-of-day ET so the
  36-hour nudge never fires early. A blank anchor produces no stalled nudge.
- Any inbound email on the exact Deal after completion suppresses the two-day
  nudge. This intentionally favors avoiding false-positive chases over assuming
  an unrelated reply should be ignored.

## Phase boundary

No Notion operating brief, Drive search/export, website refresh, DocuSign/SOW
ingestion, shared Slack/Teams ingestion, Muthur integration, or portfolio pattern
learning was added. Phase 3 has not begun.
