# Phase 4 - DocuSign executed-contract finalization

PRD v2 governs this phase. On 2026-08-23 Harbor authorized completed DocuSign
MSA + SOW documents to finalize the matched Deal's commercials, set the Deal to
`Won`, set the Account Type to the exact live label `Active Client`, archive the
executed PDF in the Account client folder, and reconcile the Client Project.

## Runtime shape

The contract pass is a separate stateless task using the same configured
DeepSeek/OpenRouter model and shared policy/writer infrastructure. In the nightly
workflow it starts after the 02:00 ET email pass. It cannot interrupt or share
conversation state with another reasoning request, and the workflow-level
`harbor-business-listening-writes` concurrency group serializes monday mutations.

For each newly completed envelope:

1. DocuSign JWT Grant reads completion metadata, all signer/recipient emails, and
   the combined executed PDF/certificate. It exposes no DocuSign mutation method.
2. Deterministic code excludes `harboradvisors.example`, maps remaining recipient
   domains against monday `Domain (match key)`, and requires one existing Account
   with exactly one active Deal. It never creates or guesses an entity.
3. Local `pdfplumber` extraction scans PDF text without model tokens. It requires
   an unambiguous `Fees` heading and returns only Fees, Payment Terms,
   Scope/Services, Term/Renewal/Termination, and bounded first-page
   party/effective-date context.
4. One contract-specific model call returns structured values with exact quotes,
   source sections/pages, and confidence. Values below `0.95`, unsupported CRM
   labels, missing quotes, image-only PDFs, and incomplete commercials fail closed.
5. A separate upload-only rclone client lists and streams the combined PDF into
   the exact folder ID from the Account `Drive Folder`. It has no delete, move,
   rename, permission, or arbitrary search operation.
6. Retry-safe effects archive the PDF, finalize Deal fields and `Won`, reconcile
   `Active Client` plus the Client Project, and post one `SOW signed` Update to the
   Account, linked Contacts, Deal, and active Client Project. The existing
   idempotent `#deal-alerts` collaboration-onboarding prompt then runs for that
   exact project.

Ambiguity posts once to `#deal-alerts` and remains recorded in the private Agent
Events ledger. No CRM value, Deal stage, Account type, or Drive file changes on
an ambiguous envelope, and the completion watermark does not advance past it.

## Deal fields

| Contract fact | monday field |
| --- | --- |
| One-time fee | `Fixed Fee` |
| Monthly retainer | `Monthly Retainer` |
| Hourly rate | `Hourly Rate` |
| Estimated/committed hours | `Estimated Hours` |
| Explicit total contract value | `Deal Value` |
| Billing structure | `Billing Model` |
| Invoice cadence | `Billing Cadence` |
| Payment terms | `Payment Terms` |
| Contracted services | `Service Type` |
| Scope | `Contracted Scope` |
| Engagement dates | `Engagement Dates` |
| Effective date | `Contract Effective Date` |
| Renewal/termination | `Renewal / Termination Terms` |
| Completed signers | `Contract Signers` |
| Completion/signature date | `Contract Signed At` |
| Envelope identity | `DocuSign Envelope ID` |
| Account client folder | `Signed Contract Drive Link` |

`Calculated Deal Value` is a monday-derived planning value: Fixed Fee plus three
months of Monthly Retainer plus Hourly Rate multiplied by Estimated Hours. The
three months are a forecasting horizon, not an inferred contract commitment.

The model never derives total contract value from a monthly/hourly amount. A
later call, email, or meeting cannot overwrite overlapping values whose
provenance is marked authoritative by the executed-contract writer.

## File archive

The Account's existing `Drive Folder` link is the only destination authority.
The writer extracts its folder ID and roots each rclone command to that ID. It
does not search for folders by Account name. The deterministic filename is:

```text
YYYY-MM-DD - Account Name - Signed MSA + SOW - envelope-prefix.pdf
```

An exact same-name/same-size file is a retry no-op. A same-name/different-size
file or duplicate name blocks processing for review. The existing business
context remote remains `drive.readonly`; the archive remote uses its own approved
existing-account token with `drive` scope because the client folders predate the
application.

## Connection and rollout

1. An authorized DocuSign administrator creates or selects an integration key,
   adds an RSA public key, and configures a redirect URI. The private key stays
   in approved secret storage.
2. Generate one-time existing-user consent with:

   ```sh
   PYTHONPATH=engine python -m runtime.docusign_main consent-url \
     --integration-key '<integration-key>' \
     --redirect-uri '<configured-redirect-uri>'
   ```

3. Store the DocuSign identifiers/private key and separately authorized Drive
   writer token in GitHub environment secrets. Do not replace or widen the
   read-only business-context token.
4. Run `connectivity` and `archive-connectivity`; neither prints credentials.
5. Apply the managed Deal schema once with `schema --apply`, then verify it.
6. Set a non-backdated `DOCUSIGN_ACTIVATED_AT`, enable contract reasoning, and
   run bounded shadow passes. Review extracted field names, Deal matches, section
   names, token-character counts, and `#deal-alerts` failures; reports contain no
   raw contract text or recipient email address.
7. Run one disposable exact-Account/Deal/PDF canary and an identical retry. The
   retry must make zero model calls, zero uploads, and zero duplicate Updates.
8. Only then enable the archive and automatic-write approval flags and set
   `DOCUSIGN_RUN_MODE=live` plus `DOCUSIGN_NIGHTLY_ENABLED=true`.

No production credential, live flag, or schedule activation is committed by the
implementation PR.
