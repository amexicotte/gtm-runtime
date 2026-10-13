# monday meeting runtime

One idempotent polling cycle:

1. Poll Fireflies and deduplicate copies created for multiple Harbor attendees.
2. Resolve Accounts from external domains and Contacts from exact attendee emails.
   Meetings create missing exact attendees immediately. Email threads create a
   missing participant only for an exact existing Account domain, and link one
   unambiguous active Deal when available.
3. Ask for company-role clarification in `#deal-alerts` when multiple external
   domains are present.
4. Post the Fireflies summary to one `#call-notes` thread and collect only new
   human replies after its stored Slack timestamp. A reply line
   `Type: AI Investment` deterministically classifies only the resolved prospect
   Account and is removed from the notes sent to reasoning.
5. After notes arrive (or the 12-hour fallback), make exactly one OpenRouter call.
6. Reuse one active Deal or create a Discovery Deal for a first Intro/Sales call.
   monday automatically stamps the board's `Created Date` creation-log column.
7. Write extracted CRM values as final and mark affected records `Verified`.
   The listening summary and quality-feedback prompt stay in the Slack thread.
8. Persist one normalized Agent Event and post one idempotent narrative Update
   to the matched Account, Contacts, Deal, and active Client Project.
9. Create assigned Activities and announce each as one concise line in
   `#deal-alerts`. A ✅ reaction marks the already-final Activity Done in monday,
   then deletes its Slack post.

The separate Phase 2 nightly path reads monday Emails & Activities, normalizes
and threads only new messages, reconciles exact Activities, stamps interactions,
and evaluates stalled/unanswered nudges. It never accesses Gmail directly.

Phase 3 exposes bounded read tools for exact monday records and Updates,
query-matched pages across the existing Notion workspace, and query-matched
metadata/text across the existing account's visible Drive. Notion and Drive are
runtime read-only; no dedicated user, workspace, page, or folder is required.
The public Harbor website refresh is domain-restricted and content-hashed.
Provider credentials remain inside deterministic clients, content/result sizes
are capped, and every tool call emits content-free audit hashes.

## Commands

From the repository root, with `engine/.env` loaded:

```bash
PYTHONPATH=engine engine/.venv/bin/python -m runtime.schema_main
PYTHONPATH=engine engine/.venv/bin/python -m runtime.monday_main --dry-run
PYTHONPATH=engine engine/.venv/bin/python -m runtime.monday_main
PYTHONPATH=engine engine/.venv/bin/python -m runtime.email_main \
  --shadow --ingest-only --since 2026-08-17T00:00:00-04:00
PYTHONPATH=engine engine/.venv/bin/python -m runtime.context_main website-refresh
PYTHONPATH=engine engine/.venv/bin/python -m runtime.context_main audit
PYTHONPATH=engine engine/.venv/bin/python -m runtime.portfolio_main --shadow
```

`schema_main` is safe to rerun: it creates missing managed columns without
duplicating existing ones. Legacy review views may remain in monday but are not
used by the runtime.

## Required environment variables

- `MONDAY_API_TOKEN`
- `FIREFLIES_API_KEY`
- `SLACK_BOT_TOKEN`
- `OPENROUTER_API_KEY`

Optional: `OPENROUTER_MODEL`, `MONDAY_DEFAULT_OWNER_ID`,
`FIREFLIES_LOOKBACK_DAYS`, `FIREFLIES_NOT_BEFORE`, `NOTES_FALLBACK_HOURS`, and
`MAX_MEETINGS_PER_RUN`. Slow released-meeting reasoning is independently capped
by `MAX_REASONING_PER_RUN`, so transcript ingestion and Slack prompting finish
within the polling cadence.

Phase 1 also uses optional `MONDAY_AGENT_EVENTS_BOARD_ID` (schema setup can
discover it by name) and required-at-activation `BUSINESS_EVENTS_ACTIVATED_AT`.
The latter is blank/disabled by default to prevent historical backfill.

Phase 2 uses `EMAIL_LISTENING_ACTIVATED_AT`. `EMAIL_REASONING_APPROVED=true`
is required before live email bodies may be sent to the configured OpenRouter
model. Ingestion-only shadow mode needs only monday and performs no mutation or
model call. Live effects additionally require `EMAIL_AUTOMATIC_WRITES_APPROVED`
and either the legacy reviewed-shadow dates or the owner-approved bounded-canary
activation recorded in `docs/phase-2-rollout-activation.json`.

## Tests

The tests are zero-dependency executable files:

```bash
for f in engine/tests/test_*.py; do engine/.venv/bin/python "$f" || exit 1; done
```
