# Google Sheets CRM migration

**Completed:** 2026-08-23
**Source:** `Harbor CRM + Contractors - CRM (4).csv`
**Destination/system of record:** monday CRM

**Parity milestone:** At 7:00 p.m. ET on Sunday, August 23, 2026, monday reached
data parity with the Google Sheets CRM. monday is now the operating CRM for Deals,
pipeline, and commercials; the Google Sheet is retained only as historical source
material and should not receive new CRM updates.

## Outcome

The migration reconciled every row with a deal lifecycle status into monday:

- 164 Deal rows were verified by stable per-row migration markers.
- 11 `Ongoing` rows resolve to one Won Deal, one Active Client Account, and one
  linked Client Project with `Status = Working on it`.
- 33 `New` rows are represented by an open Deal. Existing Discovery, Proposal,
  and Negotiation stages were preserved instead of being reset to New.
- 41 `Completed` rows are Won. Historical projects were not invented; the two
  projects created by the native Won automation during migration were closed.
- 78 `Closed lost` rows are Lost.
- The one `Paused` row is preserved without forcing a terminal outcome.
- The two `Referral Partner` rows were retained as Accounts without inventing
  Deals. The one unclassified row was reported and intentionally not converted
  into a Deal.

The identical post-migration dry run produced zero creates, zero updates, and
zero ambiguities.

## Commercial and revenue policy

The legacy `Fees` column was not copied into `Fee`, `Fixed Fee`, `Monthly
Retainer`, `Hourly Rate`, or other final-commercial fields. For open Deals only,
the importer accepted a value when the entire legacy cell was one bare numeric
amount with no cadence, range, formula, or qualifier. Nine Deals received such a
provisional `Deal Value`; their Notes identify the estimate as provisional.

The legacy `2025` and `2026` columns were not copied into `Collected Revenue`.
Mercury remains the future source of truth for cash collected. Until Mercury is
connected, the Commander dashboard reports estimated pipeline from open Deal
Value and shows no collected-revenue rows.

The approved DocuSign commercial/onboarding schema is installed and verified in
monday. Signed-envelope ingestion is not active because the local/production
DocuSign JWT credentials and rollout gate are not configured. No legacy fee was
used as a substitute for a signed contract.

## Repeatable importer

The importer is dry-run by default and treats CSV cells as data only:

```sh
engine/.venv/bin/python migrations/import_google_sheets_crm.py \
  '/path/to/export.csv' --report /tmp/harbor-crm-migration.json
```

Live mode requires an exact non-empty company-row count:

```sh
engine/.venv/bin/python migrations/import_google_sheets_crm.py \
  '/path/to/export.csv' --apply --confirm-company-row-count 167 \
  --report /tmp/harbor-crm-migration-live.json
```

The importer matches the known name variants and phase records, refuses
ambiguous Account/Deal relations, preserves advanced open stages, writes one
stable migration key into Deal Notes, and handles monday complexity-budget
rejections without retrying unknown mutation outcomes.

## Operating handoff

- New/open Deals: keep `Deal Value` current as the best total expected value.
- Retainers: enter the expected total in Deal Value using the current monthly
  retainer and the current three-month retention-horizon assumption recorded in
  Deal Notes. `Calculated Deal Value` applies this three-month horizon
  automatically.
- Signed work: after DocuSign JWT access and the canary/retry gate are enabled,
  structured signed fields replace estimates as the commercial authority.
- Cash collected: leave legacy annual totals out of Deals until Mercury supplies
  dated actuals through the separately approved integration.
- Client delivery: manage Client Project owner, priority, timeline, and delivery
  status directly in monday.
