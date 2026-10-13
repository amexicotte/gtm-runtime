# Harbor business-listening system inventory and ownership

**Phase:** 0 — Access and live discovery
**Captured:** 2026-08-16 America/New_York (schema refreshed 2026-08-24)
**Contract:** `docs/harbor-business-listening-agent-prd-v2.md`
**Status:** Phase 0 discovery complete; scoped-access blockers and deferred systems are recorded below. No Phase 1 product work is included.

## 1. Outcome

The live CRM, its Emails & Activities (E&A) source, current meeting runtime, and
the private `Harbor-Advisors/muthr-slack` repository were inventoried read-only.
monday remains the only CRM system of record. This repository owns deterministic
meeting/manual-note listening and its Activity/Slack accountability loop. Muthur
owns the conversational Slack pipeline surface, explicit user-commanded CRM
operations, website-lead intake, pipeline reporting, and its existing Slack SOW
workflow. monday-native automations own simple CRM lifecycle reactions where the
live automation already exists.

On 2026-08-19 the owner replaced the provisional dedicated-root design with
workspace-wide read-only access through existing accounts. The `Harbor GTM Engine`
Notion integration and `harbor-drive` rclone remote are the production readers;
the latter is exactly `drive.readonly`. No dedicated user, workspace, page,
folder, or paid seat is created. Retrieval remains query-driven and capped, and
neither runtime exposes provider mutation methods. The rclone remote still uses
the shared Google OAuth client that rclone reports is being retired during 2026;
moving the same existing-account read-only grant to an Harbor OAuth client is an
operational hardening task, not a dedicated-user requirement. DocuSign, shared
Slack/Teams ingestion, Gmail API access, and Muthur implementation remain deferred.

## 2. Source-of-truth and execution ownership

| Capability/data | Source of truth | Current execution owner | Ownership rule for later phases |
| --- | --- | --- | --- |
| Accounts, Contacts, Deals, Activities, Client Projects | monday CRM | monday plus deterministic writers | No second CRM store. All mutations go through a deterministic monday adapter with source evidence. |
| Email messages | monday E&A timeline | monday's three connected Gmail subscriptions | The listening engine reads E&A; it never requests Gmail credentials or calls Gmail directly. |
| Meeting transcript | Fireflies, referenced by monday Meeting | This repository | Keep rolling ingestion, deterministic gating, cached reasoning, and retry-safe writes here. |
| Manual call notes and listening feedback | Slack `#call-notes`, mirrored to Meeting state | This repository | `score:`/`feedback:` remains the current feedback mechanism until Muthur feedback is deliberately activated. |
| Open Activity action UI | monday Activity plus Slack `#deal-alerts` post | This repository | monday status is authoritative; Slack is a reversible interaction surface. |
| Narrative Updates | monday item Updates | Human-authored today; future listening writer | Phase 1 may add the single idempotent narrative writer. Muthur must not create a parallel narrative store. |
| Client Project creation on Won | monday native automation | monday automation `100000015` | This is the primary creator. The repository's relation-based reconciler is repair/fallback only; Phase 1 must not add a third creator. |
| Client Project fields and Deliverables | monday, human-managed | Humans | Listening code may post narrative later but must not modify fields, subitems, or delivery state. |
| Pipeline Q&A, weekly report, deal-change Slack messages | monday live data | Muthur | Muthur may read and summarize; its reports are not CRM memory and do not replace monday Updates. |
| Explicit user-commanded Deal edits/imports | monday | Muthur controlled-action layer | Keep Slack user allowlist, approval rules, idempotency, and audit. Do not treat conversational action tools as event-listening writes. |
| Website lead capture | monday Contact + Update | Muthur | Muthur remains the sole intake owner. The listening engine consumes resulting CRM state and must not duplicate lead creation. |
| SOW ingestion from Slack | monday Deal fields | Existing Muthur feature | Outside the v2 listening build. Do not reuse this as the deferred DocuSign path without a separate decision and policy reconciliation. |
| Notion client-project digest | Notion pages | Existing Muthur feature | Separate workflow. Workspace reads are authorized, but each runtime keeps its own bounded queries and audit. |
| Durable meeting claims/cache | monday Meetings | This repository | Preserve; Phase 1's Agent Events ledger covers cross-source event effects without migrating Meeting claims. |
| Muthur action/dedupe/audit state | Muthur Postgres | Muthur | Remains local operational state, not CRM truth. Do not merge it with Agent Events; handoffs use stable source IDs. |
| Drive business documents | Google Drive | Human-managed plus bounded read client | Read only across the existing account's visible Drive. No archive writer in this build. |

## 3. Access gate matrix

Statuses mean: **Ready** = connectivity and required read surface verified;
**Blocked** = access exists or is partially configured but violates the required
boundary; **Deferred** = explicitly not required by PRD v2; **Not exercised** =
capability exists but a safe reversible production target was not designated.

| System/surface | Status | Evidence from Phase 0 | Owner and next action |
| --- | --- | --- | --- |
| Current GitHub repository | Ready | Native GitHub app can read private `alex-mexicotte/harbor-gtm-runtime`; local checkout is at `6f9ea38` with pre-existing uncommitted work preserved. | Repository admins keep the GitHub app grant. |
| `Harbor-Advisors/muthr-slack` | Ready | Native GitHub app has read access; audited `main` at `bd22b379818b911e11575de05ea4fd1a3d4f8d9a` (2026-08-14). | Harbor GitHub organization admin keeps repository authorization. |
| Other GTM-agent repositories | Ready / none visible | Affiliation inventory returned only the current runtime and Muthur; no collaborator repositories were visible. | Harbor GitHub admin authorizes additional repositories only if they enter a future audit scope. |
| Local `gh` CLI | Blocked, non-critical | Stored `alex-mexicotte` login reports an invalid token. Native connector covered Phase 0 read access. | Repository admin re-authenticates `gh` only when a later CLI-only workflow requires it. |
| monday identity/account | Ready | Existing-account token and read-only connector both reach Harbor Advisors account `10000024`; API version `2026-07`. | Harbor monday admin retains token in local/production secret storage. |
| monday boards/columns/relations/views | Ready | Six boards and 203 columns captured live; machine snapshot is `docs/live-monday-schema.json`. | Zero-token production preflights block on unreviewed schema drift. |
| monday items/relations/Activities | Ready | Board item counts, relation targets, and Activity schema were read live. | No action. |
| monday Updates | Ready | A recent Deal Update was read through the native read-only connector. | No action. |
| monday E&A timelines | Ready | Direct E&A timeline query succeeded. A bounded scan covered all 308 Contacts and their latest page: 3,205 email items on 255 Contacts. | Phase 2 must implement a durable high-water mark and reconciliation overlap; Phase 0 performed no ingestion. |
| E&A mailbox inventory | Ready | Active inbound/outbound subscriptions and timeline authors identify Sam, Alex, and Jordan mailboxes. | CRM admin removes stale subscriptions if personnel/access changes. |
| monday automations | Ready | Twenty active native automations inventoried across the six boards; see §5. | CRM admin uses this inventory before adding native rules. |
| monday production writes | Not exercised | Existing code and token have write capability, but Phase 0 used read-only queries/connectors. | Harbor CRM owner must name a disposable/reversible target before any write test. |
| monday file upload | Not exercised / deferred | Accounts and Deals have no Files column; no disposable item was designated. DocuSign/file attachment is deferred by v2. | Defer until future SOW scope defines columns, target item, rollback, and approval. |
| Notion integration | Ready by owner decision | `Harbor GTM Engine` is authenticated and workspace-wide read-only retrieval is explicitly authorized. | Keep provider credentials outside prompts and expose only capped search/read methods. |
| Google Drive rclone | Ready; OAuth-client hardening advised | Remote `harbor-drive` uses `scope = drive.readonly`; whole-visible-Drive reads are explicitly authorized. | Retain the existing account and read-only scope; move from rclone's retiring shared OAuth client to an Harbor OAuth client when available. |
| Google Drive contract writer | Authorized; credentials not configured | Phase 4 may use a separate existing-account `drive` token, rooted per operation to the exact Account `Drive Folder`, for list/upload only. | Keep it separate from `harbor-drive`; no delete/move/rename/sharing method and no activation before canary. |
| Harbor public website | Ready | Public homepage and contact positioning are readable without credentials. | Phase 3 may hash/refresh public content weekly. |
| Fireflies | Existing runtime access | Local env has a configured key and the scheduled runtime already uses it; no new scope was needed for Phase 0. | Existing runtime owner retains current secret. |
| Slack call notes/deal alerts | Existing runtime access | Local env has a configured bot token; shared-channel business ingestion remains out of scope. | Existing runtime owner retains the current narrow workflow; do not add shared-channel reading in this build. |
| Gmail API | Deferred / prohibited path | Email is available through monday E&A. No Gmail credential was requested or used. | None. |
| DocuSign | Phase 4 authorized; credentials not configured | Completed-envelope metadata, recipient emails, and executed PDF reads are approved through existing-user JWT Grant. | Configure integration key/RSA consent, then pass connectivity, shadow, canary, and retry gates. |
| Shared Slack and Microsoft Teams ingestion | Deferred | Adapter contract only; no channel read performed. | Future adapter owner proposes narrow app grants and signed event submission. |
| Muthur feedback/front door | Deferred | Repository inventoried only; no Muthur implementation changed. | Product owner activates only through a later approved phase. |

## 4. Live monday CRM inventory

The authoritative machine-readable snapshot is
[`live-monday-schema.json`](live-monday-schema.json). It contains board metadata,
groups, views, every live column ID/type/settings object, status/dropdown labels,
and relation targets. It intentionally contains no item values or secrets.

| Board | ID | Items | Columns | Primary role |
| --- | ---: | ---: | ---: | --- |
| Accounts | `10000000001` | 180 | 22 | Company/account current state and relationships |
| Contacts | `10000000002` | 308 | 23 | Person identity, email, account/deal relations, E&A surface |
| Deals | `10000000003` | 46 | 35 | Pipeline state, qualification, commercial fields, stage |
| Meetings | `10000000004` | 56 | 45 | Fireflies/manual input, claims, identity, reasoning cache/audit/feedback |
| Activities | `10000000005` | 94 | 17 | Native E&A activities plus engine-created commitments and Slack linkage |
| Client Projects | `10000000006` | 6 | 9 | Human-managed post-sale project record and Deliverable subitems |

### 4.1 Critical live schema facts

- Deal stages are exactly `New`, `Discovery`, `Proposal`, `Negotiation`, `Paused`,
  `Won`, and `Lost`. `Paused` is human-controlled and excluded from pipeline and
  stalled-Deal nudges. `Completed` is an Account lifecycle, not a Deal stage.
- Account Type uses `Active Client` while any Client Project is active and
  `Completed - Past Client` after every related Client Project is inactive.
- Deals already have direct `Account` and relation-derived `Account (from
  Contacts)` columns. The new direct relation is the deterministic writer target;
  old Muthur code still resolves some Accounts through Contacts.
- Deals have `Last interaction` (`date__1`), enabling the future deterministic
  nudge anchor without adding a duplicate column.
- Contacts also have `Last Interaction` (`date_mm5rjnav`).
- Client Projects relate to Deals, Contacts, and Accounts but currently have no
  `Slack Channel` or `Drive Folder` column.
- Accounts and Deals have no Files column. File upload cannot be attached to the
  PRD's intended targets without later schema approval.
- `Review Status`, `Proposed Changes`, and `Needs Review` views still exist on
  Accounts, Contacts, and Deals even though the current uncommitted runtime
  direction writes final values. Meetings also retain a `Needs Review` label/view.
- Activities' `Review Status` description still says “Human gate for
  agent-drafted tasks,” while current runtime code sets agent-created Activities
  to `Verified`. This is documentation/schema drift, not Phase 0 work to repair.
- The Activity native relation can target Contacts, Deals, Leads, Accounts, and
  Activities. Engine-specific `Source Meeting` supplies the stable meeting dedupe
  relation.
- E&A appears as an `Activities timeline` app column whose generic schema type is
  `unsupported`; actual email events are readable through the root `timeline`
  API and have stable UUID IDs.

### 4.2 Relation map

- Account → Contacts, Source Contact, Client Projects, Meetings, direct Deals.
- Contact → Accounts, Deals, Meetings (plus older duplicate/reflection relations).
- Deal → Contacts, Champion, Client Project, Meetings, direct Account.
- Meeting → external Contacts, Account, Deal, Activities.
- Activity → related CRM item(s), Source Meeting.
- Client Project → Deal, Contact, Account, Deliverable subitems.

Duplicate/reflection relations are live and must not be mistaken for independent
semantic relationships. Later code should resolve column titles/IDs from the
snapshot or live schema and use one declared relation per semantic edge.

### 4.3 E&A mailbox and event inventory

The Contact timeline scan used `skipConnectedItems: true` so events attached
through other related records did not inflate each Contact's sample. It inspected
the latest page (up to 25 items) for every Contact:

| Mailbox identity | monday user ID | Email | Sampled email timeline items |
| --- | --- | --- | ---: |
| Sam Reyes | `100000008` | `sam@harboradvisors.example` | 3,095 |
| Jordan Patel | `100000014` | `jordan@harboradvisors.example` | 59 |
| Alex Mexicotte | `100000007` | `alex@harboradvisors.example` | 51 |

Totals: 308 Contacts scanned, 255 with at least one item on the latest page,
3,205 items sampled, all with `type = email`, and zero query failures. This is a
mailbox/connectivity inventory, not a historical backfill or production count.
The live automation inventory confirms one incoming and one outgoing Gmail
subscription for each of the three identities. The engine must still deduplicate
the same timeline UUID when monday associates it with multiple CRM records.

## 5. Live monday automation inventory

All 20 discovered automations are active. No additional workflow-draft objects
were present. Automation IDs are included so later tasks can compare live state
without rediscovering ownership.

### Accounts — 1

- `100000025`: when any E&A Activity is created on an Account, create/connect an
  item on Activities and map name, status, type, owner, start, and end time.

### Contacts — 9

- `100000026`: notify item subscribers when an associated email is opened.
- `100000027`: notify item subscribers when a new email arrives from the Contact
  email column.
- `100000028`: when any E&A Activity is created, create/connect an Activities item.
- `100000029`, `100000030`: Sam incoming/outgoing Gmail subscriptions.
- `100000031`, `100000032`: Alex incoming/outgoing Gmail subscriptions.
- `100000033`, `100000034`: Jordan incoming/outgoing Gmail subscriptions.

### Deals — 8

- `100000035`, `100000036`, `100000037`: move Proposal, Negotiation, and Discovery
  Deals to the active group.
- `100000038`: move Won Deals to the Engagements group.
- `100000039`: notify subscribers when an associated email is opened.
- `100000040`: when any E&A Activity is created, create/connect an Activities item.
- `100000041`: notify board subscribers when a Deal becomes Won.
- `100000015`: when a Deal becomes Won, create a Client Project in `New Projects`,
  connect it through `project_opportunity`, name it `<Deal> - New project`, and
  copy the Deal owner.

### Meetings — 0

Meeting ingestion and reasoning are external and scheduled through GitHub Actions.

### Activities — 1

- `100000042`: at 09:00 when `End time` arrives, set Status to Done. The automation
  is configured in `Asia/Jerusalem`, not `America/New_York`; the CRM owner should
  confirm whether this is intentional before the future completion/nudge engine
  relies on Activity timing.

### Client Projects — 1

- `100000043`: on item creation, assign the creator to Project Owner.

## 6. Current meeting/listening runtime inventory

Repository: private `alex-mexicotte/harbor-gtm-runtime`. Phase 0 was executed
in the existing dirty working tree at committed baseline `6f9ea38`; all prior
modified/untracked files were preserved.

### 6.1 Inputs and schedule

- GitHub Actions workflow `harbor-monday-meeting-engine` polls at minutes 7, 22,
  37, and 52 from 08:00–19:59 plus 20:00, Monday–Saturday,
  `America/New_York` (approximately every 15 minutes during the window).
- Fireflies is read with a rolling two-day lookback and a production not-before
  watermark; canonical Meeting rows are deduplicated before creation.
- Slack `#call-notes` supplies human notes, ambiguity resolution, manual call
  intake, and post-write `score:`/`feedback:` evaluation.

### 6.2 Deterministic/runtime responsibilities

- `engine/runtime/monday_runner.py` orchestrates Fireflies ingestion, Slack note
  collection, identity gate, one reasoning call, cached result recovery, final
  monday writes, Activity creation, Slack alerts, Won reconciliation, and feedback.
- `engine/runtime/monday.py` is the single Python monday GraphQL client. Board IDs
  are configured; column IDs are resolved live by title.
- `engine/runtime/monday_pipeline.py` caches model output before downstream writes
  and resumes without another reasoning call.
- `reasoning/writer.py` applies semantic fields and marks changed CRM objects
  `Verified` in the current uncommitted direction.
- `engine/runtime/activities.py` creates explicit commitments, deduped through
  Source Meeting and title.
- `engine/runtime/activity_alerts.py` mirrors open work to `#deal-alerts`; a check
  reaction or `done` reply marks monday Done and removes the bot post.
- `engine/runtime/engagements.py` scans Won Deals, ensures a related Client
  Project, and then marks the Account `Active Client` (or `Completed - Past
  Client` when every related project is inactive). Because native automation
  `100000015` is active, this path should be treated as relation-based repair,
  not the primary creator.
- Meeting rows store processing claims, reasoning result/cache, reasoning audit,
  Slack cursors, manual-note integrity hashes/payloads, and feedback.

### 6.3 Existing write surface

The runtime can create/update Accounts, Contacts, Deals, Meetings, Activities,
and Client Projects; write final mapped fields; link relationships; change Deal
stage; set Account type; and complete Activities. It does not yet create the v2
BusinessEvent/Agent Events/narrative Update foundation, ingest E&A nightly, or
run the future nudge engine. Those are later phases and were not started here.

## 7. Detailed overlap audit: `Harbor-Advisors/muthr-slack`

Audit basis: private repository `main` at
`bd22b379818b911e11575de05ea4fd1a3d4f8d9a`. The GitHub app reports repository
read access. The repository is TypeScript/Node 22 using Express/Vercel,
Trigger.dev, Postgres, the OpenAI Agents SDK, Slack Web API, and monday GraphQL.
Production deployment is described as `muthr-slack.vercel.app`; Phase 0 audited
code and configuration but did not mutate or independently certify deployment.

### 7.1 Muthur capability inventory

- Receives authenticated monday Stage/Deal Value webhooks and posts Slack deal
  change notifications through Trigger.dev.
- Runs a Monday 08:30 ET weekly pipeline report using live Deals, activity logs,
  a saved weekly snapshot, and deterministic ARR calculations.
- Answers Slack Agent View/mention/DM pipeline questions with bounded read tools
  for summary, open Deals, Deal details, weekly movement, and thread attachments.
- Exposes controlled external actions for Deal create/update/archive/delete,
  CRM batch imports, Slack actions/messages, and future Lemlist/EmailBison actions.
- Restricts writes with `SLACK_WRITE_USER_IDS`; destructive/batch/public-message
  actions require an expiring Slack approval. Postgres records idempotency keys,
  proposals, action audit, snapshots, short thread history, and model usage.
- Accepts authenticated website leads, deduplicates Contacts by exact email under
  a lock, creates a Contact if absent, appends a monday Update with attribution,
  and posts a Slack notification.
- Previews CSV/TSV CRM imports, resolves Accounts/Contacts/Deals with deterministic
  identifiers, uses LeadMagic only for missing company identifiers, blocks rows
  without a real Contact, and requires approval before batch creation.
- Reads Slack-attached PDF/Word SOWs, resolves a Deal link (or an Account to its
  newest related Deal), extracts structured commercial fields with OpenAI, and
  automatically writes high-confidence fields.
- Reads up to 25 recently edited Notion pages visible to its token, summarizes
  client-project work with OpenAI, and sends a weekday 09:00 private Slack digest.
- Scaffolds Lemlist and EmailBison providers but deliberately returns “not
  configured” until credentials/live APIs exist.

### 7.2 Field and object overlap matrix

| Area | This listening runtime | Muthur | Collision/ownership decision |
| --- | --- | --- | --- |
| Deals reads | Resolves active/Won Deal deterministically for meetings and writes | Reads all Deals for Q&A/reporting; reads activity logs | Shared read is safe. monday is canonical. |
| Deal field writes | Event-derived final fields through semantic writer and stage policy | Explicit Slack action can write stage, value, close date, probability, owner, billing model, fee, dates, service types | Muthur remains an explicit human-command channel. Event-derived writes belong to the listening writer. Both must share field policy before Muthur is used as a listening front door. |
| Deal resolution | Refuses multiple plausible active Deals | Account-linked SOW resolution selects the newest related Deal | Muthur newest-Deal fallback violates the listening system's no-guess rule and must not be reused by future adapters. |
| Stage/Won | Calls/emails allow open stages through Negotiation; only the separately gated Phase 4 completed DocuSign writer may set Won | SOW extraction includes `Won` and auto-writes any high-confidence Stage from a draft or signed SOW | Existing Muthur draft/newest-Deal behavior remains prohibited; Phase 4 owns automatic Won only after completed-envelope, exact-domain/Deal, evidence, archive, and retry gates. |
| Account/Contact creation | Meeting/manual-note identity and relationship flow | Website leads and approved CRM imports | Inputs are distinct. Muthur owns website/import intake; listening engine must not recreate those records. Both rely on exact email/domain and should converge on one shared resolver later. |
| monday Updates | Future narrative owner; not implemented in Phase 0 | Website lead attribution creates Contact Updates | Muthur keeps website attribution Updates. Narrative events use stable source markers so the same lead/email is not reposted. |
| E&A email listening | Phase 2 will read monday timeline items | No generic E&A ingestion; Muthur reads pipeline state | No current overlap. Do not add Gmail API to Muthur/listening engine. |
| Activities | Creates commitments and drives `#deal-alerts` completion | No commitment reconciliation; pipeline reports read Deal activity logs | Listening runtime owns commitment Activities. Muthur may surface them later only through explicit adapter/design. |
| Client Projects | Current runtime reconciliation can create/repair; v2 later adds narrative only | Notion digest summarizes client-project pages, not monday Client Projects | monday native automation is primary creator. Runtime repair remains secondary; Muthur digest is informational. |
| SOW/contract fields | Phase 4 reads bounded sections from completed DocuSign PDFs and makes accepted terms authoritative | Slack attachment SOW extraction exists now | Keep isolated. Muthur's draft/newest-Deal path cannot bypass Phase 4 completion, identity, evidence, archive, or write policy. |
| Slack reading | Narrow `#call-notes` and `#deal-alerts` loops | Mentions, DMs, group DMs, attachment threads, configured channels | Shared-channel business listening is still deferred. Future Muthur adapter emits signed normalized events; it does not bypass the central policy/writer. |
| Slack notifications | Listening feedback and Activity alerts | Pipeline changes, weekly report, website leads, approvals, Notion digest | Separate destinations/purposes. Nightly listening digest must not duplicate Muthur's weekly pipeline report. |
| Model role | One extraction/reasoning pass; deterministic write validation | Conversational Agents SDK and separate SOW/digest model calls | Do not merge prompts or let a conversational session hold generalized listening write authority. |
| Durable state | monday Meeting cache today; future Agent Events | Postgres processed events, proposals, audits, snapshots, thread history | Keep separate operational ledgers. Cross-system handoff uses signed source key and idempotent effect key. |
| Notion | Bounded workspace-wide operating context | Broad visible-page daily digest | Both may read the workspace, but the listening runtime uses capped query-driven retrieval and never exposes mutations. |

### 7.3 Binding boundaries for future work

1. **One monday mutation policy.** Muthur may remain a human-command front end,
   but any future event-listening use must submit a normalized event to the
   central policy/writer rather than calling `monday.update_deal` directly.
2. **No guessed Deal.** Muthur's newest-related-Deal fallback is not valid for
   listening/contract evidence. Exact Deal link or one unambiguous candidate is
   required.
3. **Won stays gated.** The v2 listening build must never auto-set Won. The
   existing Muthur SOW path is a separate live-policy exception to reconcile,
   not precedent for Phase 1 or 2.
4. **Website leads stay in Muthur.** Their stable submission UUID and Contact
   Update are the dedupe/audit source. The listening engine only consumes monday.
5. **monday owns Client Project creation.** Automation `100000015` is primary;
   this repository's reconciler repairs missing relations/records. Later code
   posts narrative only after resolving the single project.
6. **Separate ledgers, shared keys.** Muthur keeps Postgres action audit; the
   listening engine gets Agent Events. An adapter passes `source`, `source_id`,
   timestamp, content hash, and signature so both sides remain retry-safe.
7. **Separate Slack surfaces.** Muthur's pipeline agent/reporting and the current
   `#call-notes`/`#deal-alerts` workflows coexist until a dedicated migration is
   approved. No implicit channel scraping or bot-seat creation.
8. **Notion remains bounded at retrieval time.** Workspace-wide read access is
   authorized for both systems, but the listening engine uses its own credentials,
   result caps, content hashes, and query-driven retrieval.

### 7.4 Muthur issues requiring an owner before integration

| Finding | Risk | Owner / next action |
| --- | --- | --- |
| SOW extraction accepts draft or signed documents and may auto-write `Won`. | Conflicts with v2 human-only Won and deferred DocuSign gate. | Product + CRM owner decides whether to disable stage extraction, require explicit human confirmation, or keep it as a documented exception. |
| Account SOW links resolve to newest related Deal. | Can attach/write contract facts to the wrong Deal when more than one is plausible. | Muthur owner requires an exact Deal link or blocks ambiguity. |
| Muthur writes supported fields without the listening field dictionary. | Conflicting overwrite/precedence rules across user-commanded and event-derived writes. | Later Muthur integration reuses the central field definitions/policy adapter. |
| Notion digest scans the most recently edited visible pages when no query is configured. | It can be noisy or expensive despite workspace-read authorization. | Muthur owner keeps its query/caps independent; the listening runtime always uses bounded queries. |
| Muthur has its own monday API version (`2026-04`) while this runtime uses `2026-07`. | Schema/value behavior can drift. | Repository owners align versions after compatibility tests; no change in Phase 0. |
| Muthur and monday both notify on Deal changes. | Duplicate or noisy Slack output if destinations overlap. | Sales operations confirms channel routing and suppresses redundant messages. |

## 8. Other system findings

### Notion

The installed connector and `Harbor GTM Engine` integration can read the existing
workspace. Workspace-wide read access is now the approved production boundary.
The Phase 0 CLI reads identity and a one-page search sample; the Phase 3 client
performs capped title search and bounded block reads with no mutation method.

### Google Drive

`rclone v1.75.0` is installed and remote `harbor-drive` uses `drive.readonly`.
Whole-visible-Drive read access through the existing account is approved. The
Phase 0 CLI validates scope and connectivity without listing content; the Phase 3
client performs provider-filtered, capped searches and bounded exports only.

### Harbor website

Public access is ready. The homepage describes the GTM positioning around buyer
discovery, outbound, conversion, and repeatable testing. It can seed Phase 3
positioning context, but it is not a goals, pricing, or contract source.

### Deferred providers

- DocuSign: not configured or required by v2.
- Gmail: intentionally accessed only through monday E&A subscriptions.
- Shared Slack/Teams: no ingestion; adapter contract only.
- Drive write/archive: no authorization in this build.

## 9. Phase 0 read-only CLI

`engine/runtime/discovery.py` adds commands that do not expose mutation methods:

```sh
cd engine
python3 -m runtime.discovery monday-connectivity
python3 -m runtime.discovery monday-schema
python3 -m runtime.discovery monday-timeline <monday-item-id>
python3 -m runtime.discovery notion-connectivity
python3 -m runtime.discovery drive-connectivity
```

Behavior and safeguards:

- optionally loads `engine/.env` without overwriting process variables;
- never prints credential values;
- rejects any monday GraphQL text containing a mutation;
- returns E&A metadata but not email bodies;
- verifies Notion identity and workspace page visibility without returning content;
- verifies Drive `drive.readonly` and account connectivity without listing files;
- uses argument arrays rather than shell interpolation for rclone.

The checked-in `.env.example` documents workspace-wide read-only boundaries and
caps. Real values remain in approved secret storage and are not present here.

## 10. Verification record

- Read `AGENTS.md` and the governing PRD v2 in full.
- Recorded the pre-existing dirty worktree before edits; no reset, checkout,
  clean, stash, commit, push, or branch switch was performed.
- Native GitHub app read both private repositories and enumerated all visible
  affiliated repositories.
- Read Muthur README, manifest, config, schema migration, monday client,
  controlled-action layer, agent tools, Slack SOW path, CRM import, Notion digest,
  website lead route, webhooks, Trigger.dev schedules, and tests/tree inventory.
- Read live monday board schemas, relation targets, views, Updates, automation
  configurations, user identities, and E&A timeline metadata.
- Scanned the latest E&A page for all 308 Contact items with zero query failures.
- Verified the new CLI against live monday connectivity, schema, and timeline.
- Ran `python3 -m unittest tests.test_discovery`: 5 tests passed.
- Confirmed rclone remote scope with redacted config and performed one read-only
  top-level folder listing; no Drive content was downloaded.
- No monday/Notion/Drive/GitHub/Slack/Fireflies write was performed.

## 11. Phase boundary

Phase 0 ends with this inventory, the live schema snapshot, and read-only
connectivity commands. No BusinessEvent model, Agent Events board, narrative
Update writer, field-dictionary completion, email ingestion, Activity completion,
nudge engine, Slack/Teams adapter, Muthur integration, DocuSign work, or portfolio
analysis was implemented. Those remain Phase 1 or later according to PRD v2.
