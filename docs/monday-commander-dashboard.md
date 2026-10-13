# Harbor monday commander dashboard

**Dashboard:** `Harbor Business Command Center`  
**Workspace:** `Harbor Advisors HQ` (`10000012`)  
**Connected sources:** Deals, Client Projects, and `Revenue Goals 2026`  
**Installer:** `python -m runtime.commander_dashboard_main`

## Purpose

The dashboard is the team's native monday view of current pipeline, expected
closes, revenue progress, monthly pipeline coverage, the largest active Deals,
and active Client Project status. It is reporting-only: the dashboard installer
does not change Deal values, stages, Client Project fields, or collected revenue.

## Metric definitions

| Metric | Definition | Source |
| --- | --- | --- |
| Current Pipeline | Sum of Deal Value where Stage is New, Discovery, Proposal, or Negotiation. Blank, Paused, Won, and Lost stages contribute zero. | Deals formula `Commander | Open Pipeline` |
| Estimated Closes | Current Pipeline divided by 3. This is the agreed simple 33% planning assumption, not the CRM's per-Deal Close Probability. | Deals formula `Commander | Estimated Close (33%)` |
| Revenue Collected YTD | Sum of the twelve monthly actuals for the dashboard year. Future months remain blank/zero. | `Revenue Goals 2026.Revenue Collected` |
| Revenue Collected This Month | The current calendar month's actual revenue. | Current-month formula on `Revenue Goals 2026` |
| Year Revenue Goal | Sum of the twelve monthly targets; seeded to $1,000,000 for 2026. | `Revenue Goals 2026.Monthly Revenue Goal` |
| Pipeline Coverage Needed | `max((current month goal - current month collected) * 3, 0)`. | Current-month formula on `Revenue Goals 2026` |

The initial equal monthly target is $125,000. The monthly targets are ordinary
monday number cells and may be changed to a non-linear operating plan while
keeping the annual sum at $1M. The installer never overwrites an existing
month or a human-entered actual.

## Native dashboard contents

- KPI numbers for current pipeline, estimated closes, YTD/month revenue, annual
  goal, and current-month pipeline coverage needed.
- Revenue collected versus monthly goal.
- Current open pipeline versus required current-month coverage.
- Open pipeline by stage and active Deals ranked by value.
- Active Client Projects by status and a delivery-progress battery.
- Deals view `Commander | Active Deals by Value`, excluding Paused, Won, and Lost and
  sorting by Deal Value descending.
- Client Projects view `Commander | Active Clients`, excluding Done and
  sorting by Priority.

## Operating ownership

- Deal Value and Stage remain owned by the sales team and the approved listening
  writer policies.
- Project Status, Priority, Owner, and Timeline remain human-managed.
- Finance/operations enters one aggregated `Revenue Collected` actual per month
  on `Revenue Goals 2026`. This avoids pretending that the currently blank Deal
  `Collected Revenue` field contains dated payment history.
- The dashboard's formula columns and widgets are derived reporting surfaces and
  never feed CRM writes.

## Run and verify

From `engine/`, preview read-only:

```sh
.venv/bin/python -m runtime.commander_dashboard_main
```

Apply the additive installer:

```sh
.venv/bin/python -m runtime.commander_dashboard_main --apply
```

The installer saves only monday object IDs and goal metadata in
`docs/commander-dashboard-state.json`. It saves no credentials or client data.
Each created widget is recorded immediately, so an interrupted retry does not
duplicate completed widgets. An existing same-named dashboard without matching
state is refused rather than modified. monday's current read APIs do not reliably
list a newly created dashboard, so retries treat the persisted dashboard ID as
authoritative and let widget creation fail closed if that parent is unavailable.

Validation:

```sh
.venv/bin/python tests/test_commander_dashboard.py
PYTHON_BIN=engine/.venv/bin/python ./scripts/validate_business_listening_release.sh
```

## Current data-readiness notes

After the 2026-08-23 Google Sheets migration, the live preview found 43 open
Deals and a measurable pipeline of $267,500, for a simple 33% estimate of
$89,166.67. Thirty-three open Deals still have no Deal Value and require a human
estimate in monday. No Deal contains Collected Revenue because the legacy annual
sheet totals were intentionally excluded and Mercury is deferred. Sixteen active
Client Projects are visible; the 11 migrated `Ongoing` clients were separately
verified as Won / Active Client / one linked Working-on-it project. Blank human
project fields remain CRM-input gaps, not dashboard calculation errors.
