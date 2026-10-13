# Business Listening implementation and release runbook

**Governing contract:** `docs/harbor-business-listening-agent-prd-v2.md`
**Prior-phase evidence:** `docs/system-inventory-and-ownership.md`,
`docs/phase-1-shared-event-and-narrative-foundation.md`, and
`docs/phase-2-email-listening-activity-reconciliation-and-nudges.md`

This runbook is the durable execution protocol for work after the Phase 0–2
release integration. PRD v2 wins if any older document conflicts with it. The
unversioned PRD is obsolete and must not be used as an implementation contract.

## Current boundary

Phases 0–2 provide the live-system inventory, shared BusinessEvent and Agent
Events foundation, narrative Updates, monday Emails & Activities ingestion,
evidence-gated Activity reconciliation, interaction timestamps, and deterministic
nudges. Merging the code does not activate production email reasoning or writes.

Do not configure production secrets, enable automatic monday or Slack effects,
or count an ingestion-only/manual run as rollout evidence during implementation
or release integration. Client Project fields and Deliverables remain
human-managed. Ordinary listening keeps Won human-only; the separately gated
Phase 4 executed-contract writer is the sole automatic exception.

## Remaining sequence

1. Complete the Phase 2 deterministic release gates and bounded live canary
   described below; do not impose a calendar wait.
2. Implement Phase 3 business context in a separate task and PR, after Phase 2's
   accepted baseline is merged. Scope it to workspace-wide read-only Notion,
   whole-visible-Drive `drive.readonly`, website refresh, bounded read tools, and
   context audit.
3. Phase 4 executed-contract finalization and client onboarding were authorized
   on 2026-08-23. It may
   download the completed PDF, reason only over locally isolated approved
   sections, archive through the separate exact-folder upload-only Drive writer,
   notify `#deal-alerts`, and finalize `Won`/`Active Client` after access, identity,
   evidence, shadow, canary, and identical-retry gates pass. monday file-column
   upload and shared-channel ingestion remain deferred; the existing onboarding
   prompt may store only an explicit validated Slack/Teams channel link.
4. Implement Phase 5 portfolio pattern learning only in its own later task and
   PR. Pattern output remains advisory and never receives Deal write authority.
5. Keep shared Slack ingestion, Teams ingestion, and Muthur integration deferred
   until each has an approved adapter and ownership design. Do not fold them into
   Phases 2 or 3.

## Branch and pull-request protocol

1. Start from the accepted GitHub `main`. Inspect the local working tree first;
   never discard, overwrite, reset, or silently include unrelated user work.
2. Synchronize a clean local `main`, then create or select one `codex/phase-N-*`
   branch for the named phase. Codex owns these Git mechanics.
3. Read PRD v2, this runbook, the system inventory, and the artifacts named by the
   phase before changing code. A phase owns only its named outcome.
4. Use clearly named logical commits. Keep contract/docs, implementation, tests,
   and release fixes reviewable; do not squash away already accepted phase
   commits during integration.
5. Push the phase branch and update its existing PR when one exists. Do not open
   duplicate PRs. Draft PRs become ready only after local validation and an
   independent full-diff review have no unresolved blockers.
6. Wait for every required GitHub check and fix failures on the same branch. Merge
   with a merge commit only when checks are green, the PR is mergeable, review
   blockers are resolved, and the task explicitly authorizes merging.
7. After merge, update a clean original local `main` to the remote merge commit
   with a fast-forward. If local `main` is dirty or has diverged, stop without
   overwriting it and record the genuine blocker.

Do not begin a dependent phase until the preceding phase PR is merged or an
explicit reconciliation decision is committed.

## Deterministic validation

Run from the repository root with the project virtual environment when present:

```sh
PYTHON_BIN=engine/.venv/bin/python ./scripts/validate_business_listening_release.sh
```

The script is also the pull-request CI entry point and must remain secret-free.
It runs every `engine/tests/test_*.py` file, compiles `engine`, `reasoning`, and
`migrations`, validates the complete field policy against the checked-in live
schema snapshot, and assembles the meeting, manual-note, and email prompts while
rejecting unresolved template markers. Tests and CI must not call production
services or mutate monday, Slack, Fireflies, OpenRouter, Notion, or Drive.

Every production monday-dependent workflow also runs
`PYTHONPATH=engine python -m runtime.schema_guard_main` before reading source
content or applying effects. This direct API comparison uses no model tokens. It
normalizes away item counts and capture timestamps but fails on board, group,
column, relation-target, status-label, or dropdown-label drift. Review and commit
the new schema contract before restoring affected production work.

Before deployment, add deterministic tests for every defect fix and for new
success, retry, ambiguity, and no-op behavior. The accepted Phase 2 baseline is
157 passing tests; later changes may increase that count but must never reduce
coverage by removing or skipping an existing test without an explicit reason.

## Phase 3 workspace-read activation

The owner authorized read-only context across the existing Notion workspace and
the existing account's visible Google Drive on 2026-08-19. Do not create a
dedicated user, workspace, page, folder, or paid seat. The weekly workflow always
refreshes public Harbor website context. Before activation, dispatch
`business-context-weekly.yml` with `audit_private_context=true`; require the
Notion identity/page probe, compact operating-context assembly, rclone
connectivity, exact `drive.readonly` scope, and bounded cross-provider search to
pass. Then set `BUSINESS_CONTEXT_ENABLED=true`. Provider credentials stay in the
orchestrator, Notion and Drive mutation methods are absent, searches are
query-driven, and result/file/excerpt/tool-call caps prevent whole-corpus prompt
loading despite the workspace-wide authorization.

## Phase 2 deployment gates

All gates fail closed. Complete them in order:

1. An authorized Harbor owner approves the configured reasoning provider/model and
   its handling of live client email, then sets `EMAIL_REASONING_APPROVED=true`.
2. The CRM owner designates disposable/reversible monday records for schema and
   live-write validation, satisfying the Phase 0 production-write gate.
3. Manually dispatch the nightly workflow with `setup_schema=true` and
   `live=false`; store the returned private board ID in
   `MONDAY_AGENT_EVENTS_BOARD_ID`.
4. Set `EMAIL_LISTENING_ACTIVATED_AT` to an approved timezone-aware cutover. Never
   backdate it to manufacture volume or history.
5. Run the complete deterministic release validation and required GitHub CI.
6. Dispatch `phase2-rollout-supervisor.yml` with
   `immediate_validation=true`. This is the owner's explicit authorization to
   substitute the bounded canary for the former three-night calendar wait; it is
   not a general safety bypass.
7. The supervisor creates the synthetic timeline fixture, resets only the four
   named reversible records, captures preflight, and runs one live pass restricted
   to Contact `10000000011`. The runtime restricts scanning, derived
   Accounts/Deals, and nudges to that exact allowlist and fails if the ID is
   missing.
   Verify event/effect retry dedupe, one narrative Update, complete Activity audit
   fields, monotonic timestamps, Slack post cleanup, and no prohibited writes.
8. The supervisor runs the identical canary again and requires zero reasoning
   calls and no state changes, then always cleans the synthetic fixture.
9. Only after every verifier passes does it commit `live_enabled: true` with
   `validation_mode: bounded_live_canary`. The nightly workflow consumes that
   committed state and begins automatic writes at 02:00 ET. Keep rollback
   available by restoring shadow mode or setting
   `BUSINESS_LISTENING_WRITE_KILL_SWITCH=true`.

No repository change may silently set these production variables or secrets.

## Legacy three-night shadow option

A three-night shadow remains available as an optional slower rollout path, but it
is no longer required after the owner's 2026-08-19 immediate-rollout decision.
A qualifying observation must be produced by the actual scheduled 02:00
America/New_York workflow, run with full reasoning, and report
`observation_eligible=true`. It must have no saturation gap, cap hit, unresolved
failure, or missing audit/report data. The three observations must have distinct
ET dates and must be reviewed by an authorized owner.

Ingestion-only runs, ad-hoc/manual dispatches, backdated runs, failed runs, and
runs that skip model reasoning do not count. The two 2026-08-17 ingestion-only
shadows recorded in the Phase 2 artifact remain useful read-path evidence but are
not part of the three-night total.

## Definition of done

A phase implementation is done only when its scoped code and durable docs are
committed; new and existing deterministic tests pass locally and in PR CI; field
policy, compilation, and all prompt assemblies pass; an independent complete-diff
review has no unresolved blocker; the PR is mergeable and required checks are
green; the authorized PR is merged; clean local and remote `main` resolve to the
merge; and unrelated work remains untouched.

Phase 2 production rollout has a separate definition of done: all deterministic
deployment gates above, the exact reversible live verification, the zero-call
retry, cleanup, and committed activation must pass before scheduled automatic
writes are enabled. A merged PR alone is not permission to activate workflows.

## Phase 2 rollout state

On 2026-08-19, the Harbor owner approved OpenRouter with
`deepseek/deepseek-v4-pro` for live client-email reasoning and approved the
system as the primary automatic CRM writer after initial rollout validation.
The private monday Agent Events board is `10000000044`, and the email cutover is
`2026-08-19T00:16:02-04:00`. Scheduled full-reasoning collection is enabled in
shadow mode through explicit GitHub Actions variables; the workflow fails closed
when the nightly-enable or reasoning approval variable is absent. Automatic
writes use the owner-approved immediate path: complete deterministic validation
plus the bounded reversible live canary and identical zero-call retry. Recurring
per-write human approval is not required after that activation gate.
Each scheduled shadow now emits a second sanitized `email-shadow-review.json`;
the workflow fails unless deterministic acceptance checks confirm the scheduled
trigger, ET date, complete reasoning coverage, no runtime gaps/caps/failures, no
shadow effects, Activity confidence floors, ambiguity isolation, narrative hard
limit, Closed Won prohibition, nudge safety, and absence of email addresses.
The workflow uses separate 06:00 and 07:00 UTC cron expressions and records the
intended slot in `schedule-gate.json`. The daylight-saving gate compares that
intended cron expression—not the runner's actual wall-clock start hour—so a
GitHub queue delay cannot silently skip a valid 02:00 ET run. The nonmatching
DST slot emits gate evidence but does not run reconciliation or count as an
observation.
`scripts/reconcile_phase2_shadow_observations.py` uses the authenticated GitHub
CLI to download only completed scheduled 02:00 ET artifacts, cross-check the run
metadata, report, and review, and update the compact durable evidence in
`docs/phase-2-shadow-observations.json`. Its optional
`--apply-post-shadow-gates` mode refuses to configure the observation dates and
write approvals until three distinct accepted dates exist and all three are past
ET calendar dates; it never switches the workflow to live mode. This means the
third night's artifact becomes eligible for promotion on the following ET day,
not during the same morning in which it was produced.

The promotion sequence is cloud-hosted in
`.github/workflows/phase2-rollout-supervisor.yml`; it does not depend on Codex or
an open laptop. An immediate owner-authorized dispatch skips the calendar wait,
resets only the four named disposable records, verifies preflight, runs the exact
allowlisted canary twice,
requires the retry to use zero reasoning calls and make zero state changes,
cleans the synthetic timeline fixture even when a canary fails, and only after
all gates pass commits `live_enabled: true` to
`docs/phase-2-rollout-activation.json`. The nightly workflow consumes that state
as its live-mode control and supplies the existing runtime approval gates. The
legacy scheduled collector remains available but is not the active release gate.
The supervisor uses GitHub's short-lived built-in workflow token with
repository-contents scope; it stores no personal token or long-lived credential.
Any failed gate stops before activation and preserves sanitized evidence for
diagnosis.

Five pre-existing duplicate exact-email Contact identity groups are quarantined
without changing or archiving the CRM records. Their timelines are inspected
read-only on every run. A dormant duplicate group does not invalidate an otherwise
complete night, but any non-noise email inside the reconciliation window fails the
observation closed and prevents the durable high-water mark from advancing until
the identity is resolved.

The Phase 0 reversible-write gate is satisfied with four isolated records created
on 2026-08-19 by `scripts/prepare_phase2_reversible_records.py`. Every item name
starts with `[TEST - SAFE TO ARCHIVE]` and its Notes identify it as temporary:

- Account `10000000045`
- Contact `10000000011` (the exact live-verification allowlist)
- Deal `10000000046`
- Activity `10000000047`

They are linked only to each other. Rollback is to archive these four exact item
IDs after verification; no production record is part of the test boundary.

The bounded E&A fixture was created by
`scripts/prepare_phase2_verification_timeline.py`: custom activity
`669dc3a3-7b93-4ae3-aa42-670b41db3efb` and timeline item
`a1985487-24e4-46c2-942f-92d21ee1dd36` on the test Contact. Normal scheduled
runs ignore it. The runtime recognizes it only when that exact custom-activity ID
and Contact allowlist are supplied for verification. A non-counting DeepSeek
shadow confirmed one exact test Deal Stage proposal (`Discovery`), one exact test
Activity completion at `1.0` confidence, one narrative, no ambiguity, and no
validation warnings. The fixture script's `--cleanup` deletes the timeline item
before its custom activity after live verification.

Before the first live canary, reset only those exact disposable records and
capture a sanitized preflight snapshot:

```sh
python scripts/prepare_phase2_reversible_records.py --reset
MONDAY_AGENT_EVENTS_BOARD_ID=10000000044 python \
  scripts/verify_phase2_live_state.py preflight \
  --output /tmp/harbor-phase2-before.json
```

After the first allowlisted workflow dispatch, run the verifier in `first` mode
with `--before /tmp/harbor-phase2-before.json`. It requires the exact Deal and
Activity changes, source provenance, seven expected ledger effects, and exactly
one narrative marker on each test Account, Contact, and Deal. It stores hashes,
IDs, and bounded audit metadata rather than source or reasoning content. Save its
output, dispatch the identical live canary once more, and run `retry` against the
first output. Retry acceptance requires the complete record, Update, and Agent
Event snapshots to remain identical. Also require the retry workflow report to
show zero reasoning calls. Only then clean up the synthetic E&A fixture and set
`EMAIL_RUN_MODE=live`.
