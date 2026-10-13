# Phase 1 — shared event and narrative foundation

**Contract:** `docs/harbor-business-listening-agent-prd-v2.md`
**Phase 0 handoff:** `docs/system-inventory-and-ownership.md`
**Status:** implementation and offline verification complete; production
activation remains deliberately off pending the reversible monday write target
already required by the Phase 0 access gate.

## Implemented

- `runtime/business_events.py` defines the normalized `BusinessEvent`, source
  participants, content hash, target IDs, event key, and concise timeline event.
  Meeting/manual-note results cache a compact envelope; full source content is
  not duplicated into monday.
- `runtime/evidence.py` normalizes evidence. The model supplies excerpt and
  confidence; code supplies source ID and occurred-at timestamp.
- `runtime/agent_events.py` claims the private Agent Events ledger, renders
  source-dated rich-text Updates, routes them to the Account, exact external
  Contacts, Deal, and related active Client Project, and records one effect key
  per target.
- The writer checks complete evidence, bounded values, relation resolution, and
  merge semantics from the complete field dictionary.
- Meeting and manual-note prompts now return `relevant`, one 30–70 word
  `timeline_event` (100-word hard maximum), and structured evidence wrappers.
- The existing cached-result invariant is preserved: reasoning plus its event
  envelope is saved before deterministic fields and Updates. A retry resumes
  effects without a second model call.
- The Agent Events setup creates/fetches one private board and installs Source
  Type/ID, occurred-at/hash, status/claim, matched IDs, reasoning cache,
  effects/audit, source URL, and error fields.
- monday automation `100000015` remains the primary Client Project creator. The
  repository fallback no longer writes Owner, Priority, Status, Timeline, or
  Deliverables; it can only repair identity relations. Narrative routing reads
  the single active project and posts an Update.

## Idempotency and recovery

The event key is `source + monday Meeting ID + reasoning-result hash`. Narrative
effects are keyed by event, target type, and target item ID. Before posting, the
writer checks both Agent Events effects and an unobtrusive marker embedded in the
monday Update. This closes the retry gap where an Update succeeded but the
ledger write failed. Fresh processing claims block concurrent completion; stale
claims may be resumed after `PROCESSING_STALE_MINUTES`.

`BUSINESS_EVENTS_ACTIVATED_AT` is required and must be timezone-aware. Blank is
disabled. Events whose cached envelope was captured before the cutover are not
claimed or posted, so schema deployment cannot backfill history accidentally.

## Activation sequence

1. CRM owner names reversible Account/Deal/Contact targets, satisfying the
   outstanding Phase 0 production-write gate.
2. Manually run the existing workflow with `setup_schema=true` and
   `dry_run=true`. Record its returned `agent_events_board_id` as repository
   variable `MONDAY_AGENT_EVENTS_BOARD_ID`.
3. Set `BUSINESS_EVENTS_ACTIVATED_AT` to the approved timezone-aware cutover.
4. Process a small new meeting/manual note and verify one concise Update on the
   exact Account, Contact, Deal, and (when applicable) active Client Project.
5. Rerun the same cached event and confirm the ledger reports deduplication with
   no new Updates.

## Verification record

- Baseline before Phase 1: every pre-existing executable test passed.
- Field dictionary validator: 151 Phase 0 columns + 15 managed additions = 166
  entries; zero coverage/policy errors.
- New tests cover event normalization/cache recovery, evidence acceptance and
  rejection, four-target narrative routing, repeated-run dedupe, crash recovery
  from an embedded Update marker, partial-effect resume, explicit activation/no
  backfill, Client Project field protection, native Updates API calls, Stage/Won
  blocking, merge behavior, and cache-before-effect ordering.
- Full executable suite: 141 tests passed. Python compilation, prompt assembly,
  and `runtime.field_policy` also completed successfully with zero errors.
- No live monday mutation was performed in Phase 1 because Phase 0 records
  production writes as “Not exercised” until a disposable/reversible target is
  designated. This is the only unexecuted acceptance check; implementation does
  not weaken or bypass that access boundary.

## Phase boundary

No monday E&A ingestion, email normalization/high-water state, Activity
completion, follow-up/nudge engine, Notion/Drive business context, DocuSign,
shared Slack/Teams ingestion, Muthur integration, or portfolio analysis is
included. Those remain Phase 2 or later/deferred work.
