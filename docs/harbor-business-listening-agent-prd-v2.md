# Harbor Business Listening and CRM Memory Engine

**Status:** Draft for approval
**Date:** 2026-08-16
**Owner:** Harbor Advisors
**System of record:** monday CRM

> **What changed in v2 (2026-08-16 working session):** Client Projects is now an
> Update target with a record auto-created on Closed Won (its fields and
> Deliverables stay human-managed); a new Follow-up & Nudge Engine was added;
> DocuSign SOW ingestion and any auto-advance to Closed Won are deferred to future
> scope; the field dictionary was completed and is referenced by the write policy;
> Muthur — and the feedback loop it will host — is deferred; reasoning never runs
> on Monday AI, while simple deterministic rules prefer Monday-native automations;
> the Deal Stage guardrail was corrected to the live board (there is no "Verbal
> Yes" stage); and Microsoft Teams was added as a deferred source.

> **Access decision update (2026-08-19):** Harbor authorized read-only access
> across the existing Notion workspace and the existing account's visible Google
> Drive. No dedicated user, workspace, page, or folder is created for the agent.
> Retrieval remains bounded and query-driven, and provider mutation methods are
> not exposed.

> **Phase 2 rollout decision update (2026-08-19):** Harbor approved immediate
> production activation after the complete deterministic suite, one exact
> disposable-record live canary, and an identical retry proving zero additional
> reasoning and zero duplicate effects. This replaces the earlier three-night
> calendar wait; ongoing email processing still runs nightly at 02:00 ET.

> **Phase 4 authorization update (2026-08-23):** Harbor authorized the nightly
> executed-contract path. After the 02:00 ET listening flow, it polls completed
> DocuSign envelopes, resolves an existing Account and one active Deal from
> recipient domains, downloads the signed MSA + SOW, locally isolates only Fees
> and approved named sections, and sends those bounded excerpts to the existing
> DeepSeek/OpenRouter reasoning provider. High-confidence executed terms become
> authoritative, the Deal becomes `Won`, the Account becomes `Active Client`, and an
> upload-only Drive writer archives the PDF in the Account's exact client folder.
> The existing `#deal-alerts` collaboration-onboarding prompt remains
> idempotent after the exact Client Project is reconciled.

> **CRM lifecycle and schema-contract update (2026-08-24):** `Paused` is a
> human-controlled Deal hold: it remains available for exact routing but is
> excluded from open pipeline, stalled-Deal nudges, and automatic stage
> movement. `Completed` is not a Deal outcome; Won Deals remain Won. When every
> Client Project for an Account is inactive, deterministic reconciliation sets
> Account Type to the exact label `Completed - Past Client`; any active Client
> Project sets it to `Active Client`. Production jobs compare live monday schema
> with the reviewed repository snapshot using a zero-token preflight and fail
> closed on drift.

## 1. Executive summary

Harbor needs one business-listening system that turns material signals from calls
and email (and later Slack and Teams) into a current, explainable CRM. It must
update Accounts, Contacts, Deals, and Activities; create a Client Project when a
Deal is won and keep its narrative current; complete existing Activities when the
source proves the work was done; proactively nudge on stalled deals and unanswered
follow-ups; and maintain a concise, reverse-chronological narrative on every
affected record through monday Updates.

The system extends the existing Fireflies -> Slack notes -> monday meeting engine.
It does not create a second CRM pipeline. All sources normalize into one durable
`BusinessEvent`, use the same deterministic entity resolution and policy layer,
and write through one monday adapter.

The implementation follows five rules:

1. **Scripts before models.** Code performs ingestion, deduplication, matching,
   candidate retrieval, validation, idempotency, and writes.
2. **The model listens; it does not hold write credentials.** It returns structured
   facts, proposed field changes, Activities, completion decisions, and a narrative.
3. **Fresh context is retrieved at run time.** monday is live CRM context, the
   existing Notion workspace holds operating knowledge, Drive holds source
   documents and signed SOWs, and
   the Harbor website supplies public positioning.
4. **Every mutation is explainable.** It carries a source event, timestamp,
   evidence, confidence, and the affected record IDs.
5. **Native backends only.** Tooling prefers repository CLIs, then direct APIs,
   then MCP/connectors. The system never drives a browser or simulates a user,
   and Harbor does not create extra human/bot user accounts for automation.

## 2. Product goals

- Keep monday Accounts, Contacts, Deals, and Activities current without manual
  transcription after every interaction.
- Make monday Updates the shared, scan-friendly narrative memory for email,
  calls, manual notes, and later Slack and Teams.
- Let a human or agent answer “What is happening with this Account/Deal?” from
  current CRM fields, open Activities, and the latest five to ten material Updates.
- Create Activities for explicit new commitments and complete existing Activities
  when a source proves the exact obligation was fulfilled.
- Give the listening agent relevant, current business context without loading the
  entire company corpus into every prompt.
- Create a Client Project when a Deal reaches Closed Won and attach the same
  call/email/Slack/Teams narrative to it; its fields and Deliverables remain
  human-managed.
- Proactively surface follow-up nudges: a general nudge when a Deal has no activity
  for 36 hours, and a specific nudge when a completed commitment gets no client
  response within two days.
- Preserve the existing meeting engine’s durable claims, cached model output,
  deterministic relationships, and retry-safe processing.

## 3. Non-goals for the first release

- Reading shared Slack or Microsoft Teams channels. Those adapters integrate later
  through the source-adapter contract.
- Sending or replying to email.
- Reading Gmail directly. monday Emails & Activities is the email source.
- Writing to Notion, or loading either full corpus into a model prompt.
  Workspace-wide read access is authorized, but every retrieval remains
  query-driven, capped, and audited.
- General Drive mutation. Phase 4 alone may list/upload one executed PDF inside
  the exact folder ID stored on the matched Account; it exposes no delete, move,
  rename, sharing, or arbitrary-search operation.
- Sending a whole DocuSign agreement to a model. Local code finds Fees, Payment
  Terms, Scope/Services, Term/Renewal/Termination, and first-page
  party/effective-date excerpts; only those bounded sections may be reasoned over.
- Modifying Client Project fields, Deliverables, or billing/delivery boards.
  Auto-creating a Client Project on Closed Won and posting narrative Updates to it
  ARE in scope.
- Guessing among multiple plausible Accounts or Deals.
- Automatically marking Closed Won from calls/emails or any source other than
  the separately gated Phase 4 completed-envelope writer.
- Treating portfolio correlations as facts about an individual Deal.

## 4. Decisions already made

### 4.1 Existing working-tree direction

The current uncommitted work represents one coherent product decision:

- The monday review queues are retired.
- Meeting and manual-note extractions write final supported values.
- Changed Account, Contact, Deal, and Activity records are marked `Verified`.
- Meetings finish as `Processed`.
- Model output is saved before downstream writes so a retry does not spend a
  second reasoning call.
- Slack `score:` and `feedback:` replies evaluate listening quality but do not gate
  final writes.
- Open Activities remain actionable through `#deal-alerts`; a check reaction or
  `done` reply closes the Activity and removes its alert.
- Deal resolution never guesses among multiple active Deals. A call or email may
  set any open pipeline Stage (New, Discovery, Proposal, Negotiation) on a clear
  signal; Closed Won stays prohibited to that writer and is owned only by the
  separately gated Phase 4 DocuSign writer; terminal-stage
  rules live in §9 (Stage policy).

This PRD preserves those decisions. It adds field-specific evidence policies so
that “final writes” does not mean an email or weak signal can overwrite arbitrary
CRM state.

### 4.2 Email source

Harbor uses monday CRM **Emails & Activities** connected to Gmail. The engine reads
email timeline items from monday; it does not request separate Gmail credentials.
Incoming and outgoing timeline items are deduplicated by monday timeline-item ID.

### 4.3 Narrative memory

The canonical play-by-play is the native monday Updates section on Accounts,
Contacts, and Deals. A `Latest Update` preview column may be added later for board
views, but it is not the canonical store.

### 4.4 Scheduling

- The email/business-event reconciliation runs nightly at **2:00 a.m.
  America/New_York**.
- The existing business-hours meeting poll remains separate so call processing
  does not wait until the next morning.
- Both workflows use the same event, policy, and writer modules.
- Scheduled execution remains GitHub Actions initially. It runs Harbor’s custom
  Python system; GitHub supplies only the ephemeral runner and schedule.
- Reasoning never runs on Monday AI (too limiting); it stays in the external Python
  engine. Simple deterministic rules — Closed Won → create Client Project, the
  stalled-deal and unanswered-follow-up nudges, last-interaction stamping — are
  built as Monday-native automations wherever possible; only the complex
  multi-field slotting stays in the external deterministic writer.

## 5. User experience

### 5.1 Narrative Update format

Each material event produces one short, rich-text monday Update:

> **Aug 14, 2026 — Email · Onsite prep delivered**
> Alex sent Quillr the requested prep document for the Aug 18 onsite.
> **Task completed:** Send Quillr prep document before onsite.

Formatting requirements:

- 30–70 words by default; 100 words is a hard maximum.
- Source date, not processing date.
- One clear headline and at most two short paragraphs or bullets.
- Use labels such as `Decision`, `Risk`, `Next step`, or `Task completed` only
  when they materially improve scanning.
- Consolidate one email thread or meeting into one event rather than posting one
  Update per message or fact.
- Exclude pleasantries, signatures, repeated history, and unchanged context.
- Use an invisible/source metadata marker or Agent Event link for idempotency.

The same event may be posted to an Account, Contact, and Deal, but wording should
be relevant to the target record and should not create duplicate Updates on the
same item.

### 5.2 Agent answers about a Deal

An agent answering a status question reads:

1. Current Deal fields and stage.
2. Linked Account and key Contacts.
3. Open and recently completed Activities.
4. The most recent five to ten material Updates.
5. The signed SOW when scope, pricing, or terms are relevant.

The answer should separate **current state**, **recent movement**, **next steps**,
and **risks**. Current fields describe the present; Updates explain the path.

## 6. Source adapters

Every adapter returns the same normalized event and performs no CRM mutation.

### 6.1 Meetings and manual notes — existing, extended

- Reuse Fireflies ingestion, attendee identity, company gate, cached reasoning,
  Deal resolution, and Activity creation.
- Extend the reasoning result with one `timeline_event` object.
- After successful CRM writes, post the event to the matched Account, external
  Contacts, and Deal.
- The stable source key is the monday Meeting item ID plus reasoning-result hash.

### 6.2 monday Emails & Activities — new

- Enumerate relevant CRM records and read their E&A timelines.
- Read timeline items newer than the durable high-water mark, with a rolling
  reconciliation overlap.
- Keep only email activities; deduplicate the same timeline item associated with
  multiple CRM records.
- Normalize HTML, quoted history, signatures, recipients, subject, timestamp,
  attachments, and links.
- Deterministically exclude newsletters, receipts, automated notifications,
  calendar noise, spam, and internal-only mail.
- Match Contacts by exact lowercase email, Accounts by existing Contact relation
  or exact business domain, and Deals by existing relations.
- When a new external participant appears on an email thread, create one Contact
  only when the exact lowercase email is absent and its non-free-mail domain
  resolves to one existing Account. Link it to that Account and, only when the
  Account has exactly one active Deal, to that Deal. Duplicate emails, unknown or
  free-mail domains, and multiple active Deals are never guessed; an ambiguous
  Deal leaves an otherwise exact new Contact related to the Account only.
- Zero plausible Deals allows Account/Contact updates only. One plausible active
  Deal allows Deal reasoning. Two or more plausible Deals blocks Deal writes and
  records an ambiguity for the nightly digest.
- Retrieve related open Activities before the reasoning call.
- Process the thread as one event. Older quoted content supplies context but only
  new messages authorize new mutations.

### 6.3 DocuSign — executed-document reasoning and onboarding authorized

> **Authorized boundary.** Phase 4 reads completed-envelope metadata and the
> combined executed PDF. It does not require DocuSign templates or custom fields.
> Recipient email domains must resolve to one existing Account with exactly one
> open Deal before contract reasoning or any external effect.

- Authenticate the unattended job with DocuSign JWT Grant using an existing
  authorized Harbor user, an integration key, and a locally generated RSA key.
  This requires one-time user consent but no dedicated user and no refresh-token
  maintenance.
- Poll completed envelopes in bounded batches using a durable completion watermark.
- Exclude Harbor recipient domains, match the remaining signer/recipient domain to
  the Account `Domain (match key)`, and require exactly one active Deal. Never
  create or guess an Account or Deal from a contract.
- Download the combined executed MSA + SOW/certificate. Local PDF parsing scans
  text without model tokens, requires an unambiguous `Fees` heading, and extracts
  only Fees, Payment Terms, Scope/Services, Term/Renewal/Termination, and bounded
  first-page party/effective-date context. Image-only or malformed PDFs go to review.
- Use a separate stateless contract prompt through the same approved DeepSeek
  model. Exact excerpts, pages, and minimum confidence are required for every
  accepted field. The model has no DocuSign, Drive, Slack, or monday credential.
- Archive the executed PDF in the exact Account `Drive Folder`, named with signed
  date, Account, and envelope key. A separate `drive`-scope rclone credential is
  rooted to that folder ID for list/upload only; no delete/move API exists.
- monday file-column upload remains deferred; save the Account folder link and
  DocuSign envelope ID on the Deal.
- Extract fee components, explicit total value, billing model/cadence, payment
  terms, services, scope, engagement/effective dates, renewal/termination terms,
  and signers. Executed terms outrank earlier conversational estimates.
- Post one concise `SOW signed` narrative Update.
- A completed, exactly matched, commercially complete envelope may set `Won`
  after the Phase 4 shadow/canary/retry gate, set the Account's exact live Type
  label to `Active Client`, and reconcile its Client Project. It never marks Lost or
  reopens a Lost Deal.
- After reconciliation, post one idempotent celebration/onboarding prompt in
  `#deal-alerts`. Accept only an explicit human reply of `slack: <HTTPS URL>` or
  `teams: <HTTPS URL>` and write the validated platform/link to the exact
  Account and Client Project; this does not authorize reading channel messages.
- Any ambiguous identity, missing Fees section, insufficient evidence, missing
  Drive Folder, or conflicting Deal state posts once to `#deal-alerts`, remains
  in the private Agent Events audit, and makes no CRM or archive change.

### 6.4 Shared Slack and Microsoft Teams — deferred adapters

No Slack or Teams channel reading is part of the first release. Define the adapter
interface and source-key convention only, so the existing Slack agent (and a later
Teams adapter) can submit normalized, signed events without duplicating business
logic.

### 6.5 Client Projects narrative and lifecycle — new

Client Projects becomes a first-class narrative target, while its structured fields
stay human-managed.

- When a Deal reaches Closed Won, deterministically create one Client Project and
  link it to the Deal, Account, and primary Contact. Prefer a Monday-native
  automation for this create step.
- Post the same call/email/Slack/Teams narrative Updates to the Client Project that
  are posted to the Account and Deal, once the client is active.
- The engine never writes Client Project fields (Status, Owner, Priority, Timeline)
  or Deliverables — those remain human-managed.
- Client Projects currently has no channel field; add a `Slack Channel` (link)
  column (and optionally `Drive Folder`) so a new client channel can be mapped to
  the record.

## 7. Normalized BusinessEvent

```json
{
  "source": "email|meeting|manual_note|docusign|slack",
  "source_id": "provider-stable-id",
  "occurred_at": "ISO-8601",
  "content_sha256": "hex",
  "title": "source title or subject",
  "participants": [{"name": "", "email": "", "role": "sender|recipient"}],
  "source_url": "optional deep link",
  "account_ids": [],
  "contact_ids": [],
  "deal_ids": [],
  "content": "normalized source content",
  "attachments": [],
  "crm_snapshot": {},
  "open_activity_candidates": []
}
```

Reasoning output:

```json
{
  "relevant": true,
  "timeline_event": {"headline": "", "body": "", "labels": []},
  "field_updates": [],
  "new_activities": [],
  "activity_completions": [],
  "risks": [],
  "ambiguities": [],
  "evidence": []
}
```

Every field update and Activity completion includes a source excerpt, source
message/timeline ID, confidence, and event timestamp.

## 8. Activity creation and completion

### 8.1 Candidate retrieval

Before reasoning, code retrieves open Activities related to the matched Account,
Contacts, or Deal. Deterministic lexical and entity filters reduce the candidate
set; the model never searches the entire Activities board.

### 8.2 Completion decisions

The model classifies each candidate as `completed`, `partially_completed`,
`not_completed`, or `unrelated`.

Automatic completion requires all of the following:

- The source proves the specific obligation, not merely intent or progress.
- The deliverable/action matches the Activity’s object and recipient.
- Required attachment or link evidence is present when the task implies delivery.
- Confidence is above the configured high-confidence threshold.
- There is no competing Activity that could plausibly be the one completed.

Examples:

- “Attached is the Quillr onsite prep document” plus attachment/link -> done.
- “I sent it yesterday” with an exact, unique candidate -> may be done.
- “I’ll send it tomorrow” -> not done.
- “The document is almost ready” -> partially completed; leave open.

On completion, code writes `Status = Done`, completion timestamp, source type,
source event key/link, evidence summary, and confidence. The existing Slack alert
loop then removes any open `#deal-alerts` post.

### 8.3 New Activities

Create only explicit Harbor commitments or explicit chase items. Dedupe by
normalized title plus source event key and related Deal/Account. Existing
owner-resolution order remains Deal Owner -> Account Relationship Owner -> source
owner -> configured default owner.

Open-Activity Slack alerts are due-date gated. A task with a valid future Due Date
stays quiet until that date in America/New_York, then posts once on the first
poll; overdue tasks post on the next poll, and undated or invalid legacy values
retain the immediate-alert fallback. A pre-existing premature alert is retracted
and its saved cursor cleared so the due-date poll can create a fresh notification.
The due-date pass also runs in a narrow daily workflow with two redundant UTC
slots, independent of Fireflies and model processing; the saved Slack timestamp
makes the second pass idempotent.

### 8.4 Follow-up and nudge engine — new

Following up is where deals are won, so the system nudges proactively. Both nudges
are deterministic (timers plus reply detection); the only model work is the
Activity-completion reasoning already defined in §8.2.

- **Stalled-deal nudge.** When a Deal has had no activity for 36 hours, post a
  general nudge to the owner in Slack. The `Last interaction` date on Deals is the
  anchor; a native automation stamps it on every new call/email/Slack/Teams event.
- **Unanswered-follow-up nudge.** When a completed commitment (e.g. "send the SOW")
  gets no client response within two days, nudge the owner to reach back out.
  Completion comes from §8.2; "no response" is a deterministic check for a new
  inbound message on that Deal/thread after the completion timestamp.
- Nudges are actionable in Slack like open-Activity alerts, and clearing the
  underlying condition (new activity, or a client reply) removes the nudge.

## 9. CRM field-write policy

The writer is deterministic and field-aware. Per-field definitions and write
policies live in the completed field dictionary (`docs/field-dictionary.md` and
`reasoning/config/fields.yaml`), which states, for every column on every object,
what belongs there and whether the writer may `overwrite`, `merge`, treat it as
`explicit_only`, set a bounded value, resolve a `relation`, or never touch it.

- `NOT_DISCUSSED`, partial, ambiguous, or unsupported values never write.
- Narrative history is append-only through monday Updates.
- A newer explicit fact may replace a mapped current-state field.
- Durable Account/Contact/Deal notes are merged or represented in Updates; they are
  never blanked because a later source omitted them.
- **Stage policy.** A call or email may set any open pipeline Stage — New,
  Discovery, Proposal, or Negotiation — when the signal is clear. The ordinary
  listening writer can never set Won. Only the separately gated Phase 4
  DocuSign writer may set `Won` after an exactly matched completed envelope and
  validated final commercials. Closed Lost may be set
  automatically only on an explicit, unambiguous "not moving forward" signal.
  `Paused` is human-controlled and cannot be automatically entered or exited.
  `Completed` is prohibited on Deals; completed customer relationships use the
  Account Type `Completed - Past Client` while their original Deals remain Won.
- Multiple active Deals are never guessed.
- Phase 4 validated executed-contract terms are authoritative for contracted
  scope, fee, billing model, and dates.
- Client Project fields and Deliverables stay human-managed; the engine only
  auto-creates the Client Project on Closed Won and posts narrative Updates to it.

Source precedence for conflicting claims:

1. Phase 4 validated executed-contract terms for contractual commercials.
2. Newer explicit direct communication or human note.
3. Call transcript evidence.
4. Bounded current operating context from Notion/the Harbor website or the CRM snapshot.
5. Portfolio patterns as a prior only; never as a record fact.

## 10. Context and tool access

### 10.1 Principle

The model receives bounded, read-only tools. It cannot call monday/Notion/Drive
with a general mutation capability. The orchestrator validates structured output
and performs all writes through deterministic clients.

All integrations are backend-native. In priority order, use a small repository
CLI, a direct provider API, or an installed MCP/connector. A one-time provider
consent or app-installation step by an authorized administrator is acceptable;
runtime browser control, UI scraping, and simulated user activity are prohibited.
No dedicated human or bot login/seat is created solely for this system. Provider
app installations and integration identities are acceptable because they are
machine principals, not simulated people.

### 10.2 monday

For the internal first release, use the existing monday GraphQL API client and an
API token issued to an existing authorized Harbor account. Store it only in local
secret storage and the production secret store. This path is static, requires no
dedicated monday user, and exposes the API through the repository CLI. OAuth is
not required for v1. Revisit an installed OAuth app or first-class monday agent
identity only if finer scopes, independent app identity, or broader distribution
becomes necessary.

Register monday's official local MCP separately in read-only mode for Codex schema,
data, and automation discovery. The wrapper reads the existing local token from
secret storage and never exposes mutation tools to the model. Approved production
writes continue through the deterministic GraphQL client.

Required lightweight client operations:

- Discover boards, columns, views, relations, and E&A capabilities live.
- Read E&A timelines and timeline-item content/timestamps.
- Read current items, relations, Activities, and Updates.
- Create/edit CRM values and Activities.
- Post rich-text Updates.
- Upload SOW files to Account and Deal Files columns.
- Expose `schema`, `read`, `dry-run`, and explicit write subcommands through a
  small repository CLI.

### 10.3 Notion

Use the existing `Harbor GTM Engine` Notion internal integration with read-only
access across the existing Harbor workspace. Do not create a dedicated user,
workspace, root page, or agent-only knowledge base. The reader searches existing
pages for current operating knowledge including:

- What Harbor does and who it serves.
- Services and engagement types.
- Delivery model and team responsibilities.
- Monthly, quarterly, and annual goals.
- Current strategic bets and constraints.
- Definitions of a strong opportunity and material risk.
- Owner and `Last reviewed` date.

The client supports bounded title search and page/block reads across content
visible to the integration. It exposes no page/block mutation method, returns at
most the configured result count and excerpt size, and does not crawl or inject
the entire workspace into a prompt.

### 10.4 Google Drive

Do not use the native Google Drive connector because its bundled grant requires
write access. For Codex and the deployed reader, use the `rclone` CLI authenticated
to the existing Harbor account with Google's `drive.readonly` OAuth scope across
that account's visible Drive. Do not create a service account, dedicated Google
Workspace user, agent-only folder, or domain-wide delegation. The runtime exposes
no general Drive mutation command and retrieves only query-matched, size-capped files.

The Drive client supports:

- Search/list metadata across the visible Drive with provider and local result caps.
- Export Google Docs and download PDFs/documents.
- Track changes/watermarks for refreshed context.
- Shared Drive requests with `supportsAllDrives=true` where applicable.

Use rclone's supported OAuth token handling; do not implement token refresh
manually. Phase 4 uses a separate authorized `drive`-scope token because the
existing client folders predate the app and are not visible through `drive.file`.
The archive runtime accepts only the folder ID from the matched Account's `Drive
Folder`, performs only a one-level exact-name list and streaming upload, and has
no delete, move, rename, permission, or general search method. The read-only
business-context credential remains separate and unchanged.

### 10.5 Local and production credentials

- Never commit or paste secrets into chat.
- Local/Codex execution receives secrets through the environment or OS-backed
  secret storage.
- GitHub Actions receives authorized app credentials or existing-account API
  tokens through repository or environment secrets.
- Never provision an automation-only human/bot account or paid user seat.
- Never use browser automation, UI scraping, or interactive user simulation as a
  production integration path.
- Provide a read-only connectivity check and schema snapshot before any write test.

### 10.6 Agent read tools

- `get_crm_context(entity_ids)`
- `get_recent_updates(entity_ids, limit)`
- `get_open_activities(entity_ids)`
- `get_operating_brief()`
- `search_business_docs(query, account_id?, deal_id?)`
- `get_signed_sow(account_id, deal_id)`
- `get_deal_pattern_brief()`

Obvious context is prefetched deterministically. An agent session may make a
small configured number of extra read calls when it identifies a genuine gap.

## 11. Business calibration

### 11.1 Stable operating context

Build a compact operating-context prefetch from relevant existing Notion pages
and the current Harbor website. Goals and internal context remain in their existing
Notion locations; no dedicated operating-brief page is required. The website is
refreshed weekly using a content hash; it is positioning context, not a pricing
or goal source.

### 11.2 Signed-SOW pricing history

Drive is the canonical executed-file store. Phase 4 writes validated SOW facts to
the matched Deal and keeps source evidence in the private Agent Events record.
Future cohort retrieval may use those structured Deal facts; it must not load the
agreement corpus or rediscover commercials from every PDF.

### 11.3 What “good” looks like

Run a separate weekly portfolio-analysis job over Closed Won and Closed Lost
Deals. It produces a dated `Harbor Deal Pattern Brief` covering stage velocity,
stakeholders, pricing bands, services, common objections, and loss reasons.
Patterns guide attention and questions; they never directly mutate a Deal.

### 11.4 Evaluation set

Create a versioned golden set of representative Harbor calls, email threads,
task-completion cases, and SOWs. Each fixture contains the expected entity match,
field updates, Activities, completion decisions, narrative Update, and prohibited
actions. Prompt/model changes must pass this suite before deployment.

## 12. Durable state and recovery

Create a private/internal monday `Agent Events` board, hidden from normal CRM
views. One item represents one processed source event/thread, not each individual
email message.

Minimum fields:

- Event Key (unique item name)
- Source Type
- Source ID
- Occurred At
- Content SHA256
- Processing Status (`Ready`, `Processing`, `Processed`, `Ambiguous`, `Failed`)
- Processing Started At
- Matched Account/Contact/Deal IDs
- Reasoning Cache
- Effects/Audit JSON
- Source URL
- Error/Ambiguity

Processing claims expire after a configured stale interval. Once reasoning is
saved, retries resume deterministic writes without another model call. The event
key and per-target effect keys prevent duplicate Updates, Activities, completion
actions, and file uploads.

Existing Meetings retain their current processing fields and reasoning cache;
Agent Events records the cross-source narrative effect without requiring a
Meetings migration.

These controls are deterministic code and spend no model tokens. The single
reasoning pass per event is the only model call, and the saved reasoning cache lets
a retry resume writes without a second one — so idempotency, shadow mode, dry-run,
the digest, and the kill switch stay fast and effectively free.

## 13. Observability and controls

- Manual workflow dispatch with `dry_run` and `setup_schema` options.
- Nightly digest containing processed/relevant/skipped counts, records changed,
  Activities created/completed, SOWs attached, ambiguities, and failures.
- No shared-channel Slack ingestion in the first release; the existing bot may
  receive the digest if desired.
- Hard caps for source events and reasoning sessions per run.
- Per-event structured audit and model/provider usage metadata.
- Kill switch environment variable for all writes.
- Shadow mode computes and records proposed effects without mutating CRM records.

## 14. Delivery plan

### Pre-build access gate

Before product implementation begins, complete and record an access matrix for:

- GitHub read access to this repository, the Muthur organization/repository, and
  any other GTM-agent repositories included in the overlap audit.
- monday API read access to live CRM schemas, E&A timelines, items, relations,
  Activities, and Updates; a separately approved reversible write test; and a
  native API/MCP path for inventorying existing board automations.
- Notion read access across the existing workspace through the installed
  integration. No Notion write actions are part of this build.
- Google Drive `drive.readonly` access for context plus a separately authorized
  `drive` token used only by the Phase 4 exact-folder list/upload client.
- DocuSign JWT access — required only for the separately gated Phase 4 path.
- Public read access to the Harbor website. Gmail and shared Slack remain out of
  scope because email arrives through monday E&A and Slack ingestion is deferred.

Phase 0 may perform connectivity probes and reversible test operations, but no
production feature work begins until the matrix identifies each connection as
ready, intentionally deferred, or blocked with an owner and next action.

### Phase 0 — Access and live discovery

- Connect GitHub through its native connector and authorize all repositories in
  the Muthur overlap audit. Connect Google Drive through the read-only rclone CLI
  using the existing Harbor account.
- Configure the monday CLI/API token from an existing authorized Harbor account,
  the Notion internal integration, and DocuSign integration access. Do not create
  automation-only user accounts.
- Grant workspace-wide read access for context providers, scoped monday access,
  and store all credentials outside the repository.
- Add lightweight connectivity/schema CLI commands.
- Capture live monday board/column/relation/E&A schema and validate file upload.
- Inventory connected E&A mailboxes without accessing Gmail directly.
- Inventory Muthur and the other GTM agents, then write the source-of-truth
  ownership and overlap findings to `docs/system-inventory-and-ownership.md`.

### Phase 1 — Shared event and narrative foundation

- Add `BusinessEvent`, Agent Events ledger, evidence policy, and monday Update writer.
- Extend meeting/manual-note results with narrative events.
- Complete the field dictionary / `fields.yaml` across all objects (every column
  defined with a write policy) so extraction and writes cover the full schema.
- Auto-create a Client Project on Closed Won and post narrative Updates to it.
- Backfill no history; begin with events processed after activation.
- Verify concise Updates on a small set of real Accounts and Deals.

### Phase 2 — Email listening, Activity reconciliation, and nudges

- Add E&A timeline ingestion, high-water state, filtering, normalization, and dedupe.
- Add relevant-thread reasoning, field-aware writes, Activity creation, and
  evidence-based Activity completion.
- Add the Follow-up & Nudge Engine — the 36-hour stalled-deal nudge and the two-day
  unanswered-follow-up nudge — with last-interaction stamping.
- Pass the deterministic release suite, one exact disposable-record live canary,
  and an identical zero-reasoning/idempotent retry; then enable high-confidence
  automatic writes immediately.

### Phase 3 — Business context

- Connect bounded read-only search across the existing Notion workspace and
  synthesize compact operating context from relevant pages.
- Add whole-visible-Drive read-only search/export and website refresh.
- Add bounded agent read tools and context audit.

### Phase 4 — DocuSign executed-contract finalization and onboarding — authorized

The authorized Phase 4 boundary:

- Run sequentially after the 02:00 ET listening flow; each envelope is a separate
  stateless reasoning call and shares the global monday mutation lock.
- Poll completed envelopes, resolve exact recipient domain -> Account -> one open
  Deal, locally isolate bounded contract sections, reason with exact evidence,
  archive the signed PDF, finalize fields, set `Won`/`Active Client`, and post Updates.
- Post every ambiguity to `#deal-alerts`; do not maintain a separate human review
  queue that operators would need to remember to visit.
- Reconcile the Client Project, post the idempotent collaboration-onboarding
  prompt, and save only an explicit validated Slack/Teams channel link.
- Build the structured signed-SOW pricing index from accepted CRM fields.

### Phase 5 — Portfolio pattern learning

- Produce the weekly Closed Won/Lost pattern brief.
- Add cohort-aware retrieval and evaluation without granting pattern output direct
  write authority.

### Deferred — Slack/Teams ingestion, monday contract-file upload, and Muthur

- Accept normalized events from the existing Slack agent (and a later Microsoft
  Teams adapter) through an authenticated, idempotent interface.
- DocuSign PDF download/archive/upload and model-based contract extraction.
- Muthur as the Slack front door and as the universal feedback-capture mechanism;
  until then, feedback stays the `score:`/`feedback:` Slack replies plus the golden
  evaluation set, and we do not build dedicated feedback widgets.

### 14.1 Codex task and delivery protocol

This PRD is checked into the repository and is the durable contract for every
implementation task. It is not downloaded and re-uploaded between tasks.

- Keep one local Codex project attached to this repository.
- The user starts one new task for each distinct phase outcome, points it at this
  same project/directory, and names the phase. Codex—not the user—creates or
  selects a safe branch/worktree from the latest accepted baseline and handles
  dirty-tree and Git synchronization concerns.
- Every task first reads this PRD plus the artifacts explicitly named for its phase.
- The checked-in PRD is never downloaded or re-uploaded between tasks.
- Only the Phase 0 task performs the system inventory and overlap audit. Later tasks
  read `docs/system-inventory-and-ownership.md`; they do not repeat the audit.
- A task implements only its assigned phase, runs relevant tests, updates durable
  documentation, and opens a reviewable PR before the next dependent phase begins.
- Merge or deliberately reconcile each phase before starting a dependent phase so
  later worktrees inherit the accepted contract and code.

Standard task prompt:

> Implement Phase N of `docs/harbor-business-listening-agent-prd-v2.md`. First read the
> full PRD and the prior-phase artifacts it names. Stay within Phase N; do not build
> later phases. Preserve unrelated working-tree changes. Run proportionate tests,
> update durable docs, and finish with a concise change/risk/verification summary.

## 15. Acceptance criteria

- The same source cannot create duplicate CRM Updates or Activities across reruns.
- A processed reasoning result resumes writes without a second model call.
- Email ingestion uses monday E&A and requires no Gmail credentials.
- Every automatic Activity completion has exact evidence and a stable source key.
- A previously unknown coworker added to a tracked meeting or email thread is
  created once as a Contact and linked to the exact Account; one unambiguous
  active Deal is linked when available, while unknown domains, duplicate emails,
  and Deal ambiguity fail closed.
- Intent, partial progress, and ambiguous task matches never close an Activity.
- Every material event produces a readable 30–70 word Update on the correct records.
- A Deal-status agent can answer current state, movement, next steps, and risks from
  live fields plus recent Updates.
- Multiple plausible Deals block Deal mutations.
- Calls/emails may set any open Stage (through Negotiation); only Phase 4 may
  auto-set Won after its completed-envelope gates; Closed Lost only on an explicit signal.
- A Client Project is auto-created on Closed Won and receives narrative Updates,
  without writing its human-managed fields or Deliverables.
- Follow-up nudges fire on a 36-hour stalled Deal and on a completed commitment with
  no client response within two days.
- A matched completed SOW is archived once in the exact Account Drive Folder and
  linked from the Deal; monday file-column upload remains deferred.
- The model has no direct mutation credentials for monday, Notion, or Drive.
- Integration secrets use authorized app grants or existing-owner API tokens, are
  absent from source control and model prompts, and do not require dedicated users.
- No production workflow uses browser automation or simulated user activity.
- The existing deterministic test suite remains green and new source/event tests
  cover success, retry, ambiguity, and no-op cases.

## 16. Approval decisions before implementation

Recommended defaults are shown first.

1. **monday authentication:** existing-account API token through the repository
   CLI for v1; no dedicated user; reconsider OAuth only when its additional
   controls justify the operational cost.
2. **Drive boundary:** keep the context reader on `drive.readonly`; Phase 4 has a
   separate approved `drive` credential whose runtime exposes only exact-folder
   list/upload rooted to the Account `Drive Folder` ID.
3. **Notion boundary:** the existing internal integration receives workspace-wide
   read access; no dedicated user/workspace/page and no runtime write method.
4. **Rollout:** deterministic suite plus one bounded live canary and an identical
   zero-reasoning/idempotent retry, then high-confidence automatic writes.
5. **Signed SOW / DocuSign:** Phase 4 reads bounded executed-document sections
   with the same model, archives the PDF, and may set Won after exact gates.
6. **Contract-review destination:** `#deal-alerts`; Agent Events remains the
   private audit/ledger, not the operator review surface.
7. **Deal Stage ceiling:** agent may set any open stage (New through Negotiation);
   Won is executed-contract-gated; Closed Lost requires an explicit signal;
   Paused is human-controlled; Completed is an Account lifecycle, not a Deal stage.
8. **Client Projects:** auto-create on Closed Won; narrative Updates in scope;
   fields and Deliverables human-managed. Add a `Slack Channel` column.
9. **Follow-up nudges:** 36-hour stalled-deal nudge and two-day unanswered-follow-up
   nudge, built as native automations where possible.
10. **Muthur / feedback:** deferred; Muthur will host feedback capture later.

## 17. Implementation references

- Existing meeting orchestration: `engine/runtime/monday_runner.py`
- Existing monday client: `engine/runtime/monday.py`
- Codex monday read-only MCP wrapper: `scripts/monday_mcp_readonly.sh`
- Existing cached reasoning bridge: `engine/runtime/monday_pipeline.py`
- Existing Activity creation: `engine/runtime/activities.py`
- Existing final writer: `reasoning/writer.py`
- Existing semantic field map: `reasoning/config/fields.yaml`
- Existing schedule: `.github/workflows/fireflies-monday-poller.yml`
- monday E&A timeline API: https://developer.monday.com/api-reference/reference/timeline-ea
- monday OAuth: https://developer.monday.com/apps/docs/oauth
- monday Updates API: https://developer.monday.com/api-reference/reference/updates
- Notion internal integrations: https://developers.notion.com/guides/get-started/internal-connections
- Google service accounts: https://developers.google.com/identity/protocols/oauth2/service-account
- Google shared-drive support: https://developers.google.com/workspace/drive/api/guides/enable-shareddrives
