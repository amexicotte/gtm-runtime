# Phase 4 — DocuSign completion and client collaboration onboarding

This onboarding behavior follows the executed-contract finalization described in
`docs/phase-4-docusign-executed-contracts.md`. The contract path authenticates
with DocuSign JWT Grant, resolves an existing Account from the external recipient
domain, and requires exactly one active Deal. Missing, multiple, invalid, or Lost
matches are recorded as ambiguity and cause no CRM, Drive, or onboarding effect.

After an exact match and commercial validation, one Agent Event gates retry
behavior. The engine finalizes supported contract fields, records the envelope
ID, completion timestamp, and signer names, sets the Deal to Won, reuses the
exact Client Project/Active Client reconciler, posts one SOW narrative plus one
celebration and onboarding prompt in `#deal-alerts`, and leaves the celebration in
place.
The prompt accepts only an explicit human `slack: <HTTPS URL>` or
`teams: <HTTPS URL>` reply. The validated platform and link are then written to
the exact Account and Client Project. Slack links also populate the legacy
`Slack Channel` project column.

The implementation is disabled by default. Live mode requires the global write
kill switch to be off, a timezone-aware `DOCUSIGN_ACTIVATED_AT`, the private Agent
Events board, and the contract reasoning, archive, and automatic-write approval
flags. Start with a bounded shadow pass, then an exact disposable Deal canary and
identical retry before enabling the nightly workflow.

Still deferred: monday file uploads and daily reading of shared Slack/Teams
conversations. The stored channel link is the handoff required for that later
source adapter; it is not ingestion authorization.
