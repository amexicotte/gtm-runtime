# Harbor GTM Engine — Data Model (working)

Status legend: **CONFIRMED** (decided by Alex) · **PROPOSED** (my recommendation, pending confirm) · **DEFERRED** (not touching yet).

Live IDs verified against the workspace on 2026-07-28 (see `context/notion-ids.md` in harbor-brain). Automation writes to the **sales half only** (Companies, Contacts, Opportunities, Tasks). It never writes to the billing half (Deliverables, SOWs, Billing Clients, Milestones) — that stays behind the Closed-Won human gate.

---

## Object model changes

### CONFIRMED — split Tasks from Deliverables
- **Rename** `Tasks & Deliverables` (`collection://0e04743a-d5f3-8261-915f-07c315a2b446`) → **`Deliverables`**. Post-sales execution work only. Human-managed; automation does not write here.
- **Create** a new **`Tasks`** database — sales-side Next Steps / To-Dos. The reasoning agent's `do-it` / `chase` items land here as drafts.

### DEFERRED — the 3 name collisions (do not touch without Alex's call)
1. `Projects → Engagements` (collides with Meetings' existing `Engagement` relation).
2. Companies `Engagement Type → Service Type` (collides with Opportunities' existing `Service Type` text field).
3. Meetings' `Engagement` relation → `Opportunity`.

---

## New database: `Tasks` — PROPOSED schema

Parent: **Harbor Advisors HQ → Source Databases**. Add to `notion-ids.md` + the `harbor-notion-crm` guard LIVE table once created.

| Property | Type | Notes |
|---|---|---|
| `Task` | title | Action-verb title |
| `Task Type` | select | `do-it` · `chase` |
| `Status` | select | Not started · In progress · Waiting · Blocked · Done (mirrors Deliverables) |
| `Assignee` | person | named → meeting owner → relationship owner → unassigned+flag |
| `Due date` | date | stated → relative-to-meeting → meeting+7d |
| `Priority` | select | High · Medium · Low |
| `Company` | relation → Companies | |
| `Contact` | relation → Contacts & Referrals | |
| `Opportunity` | relation → Opportunities | |
| `Source meeting` | relation → Meetings | **dedupe key** — re-runs must not duplicate |
| `Review Status` | select | Needs Review · Ambiguous · Verified |
| `Slack link` | url | notify nudge |
| `Notes` | text | |

---

## Additive fields — PROPOSED (safe, reversible)

### Companies (`f6c4743a-d5f3-83a1-9fc3-07e6670d5709`)
- `Domain` — text, bare lowercase (`acme.com`). Matcher key.
- `Domain Role` — select: `Client` · `Partner` · `Investor`. Learn-once memory.
- `Represents` — relation → Companies (self). "On behalf of" routing.
- `Review Status` — select: `Needs Review` · `Ambiguous` · `Verified`.
- `Proposed Changes` — text. `was X → proposes Y (source: [date] call)`.

### Opportunities (`14d4743a-d5f3-8254-992b-07cb10000013`)
- 7 MEDDICC text fields: `Metrics`, `Economic Buyer`, `Decision Criteria`, `Decision Process`, `Identified Pain`, `Champion`, `Competition`.
- `Qualification Gaps` — text, **visible**. Auto-filled from NOT DISCUSSED.
- `Review Status` — select (as above).
- `Proposed Changes` — text (as above).
- (`Expected revenue` already exists as number/$ ✅. `Stage` already has `Verbal Yes` ✅. `Service Type` already exists as text ✅.)

### Meetings / "Meeting Notes" (`4c64743a-d5f3-821a-a437-079879c23879`)
- `Processing Status` — select: `Awaiting Notes` · `Ready to Process` · `Needs Review` · `Processed`.
- `## Notes` — page-body section convention for manual notes (not a property).
- **Reuse** existing `Internal/External` select as the PRD's "Meeting Type" (don't add a duplicate).
- **Reuse** existing `Invited` (emails) as the domain source — no separate `Attendee Emails` field needed unless a normalized copy proves useful.

### Deliverables (formerly Tasks & Deliverables)
- No automation fields added. `Source meeting` / `Task Type` stay as-is for human use.

---

## Ground-truth findings (from live rows, 2026-07-28)

- **Attendee format RESOLVED:** `Invited` = emails (matcher key); `Attendees` = display names (unusable for matching). Fireflies already connected and writing rows.
- **Matcher drop-lists must include:**
  - Harbor domains: `harboradvisors.example` **and the typo** `harboradvisers.example` (both seen in real rows).
  - Notetaker domains: `fireflies.ai`, `otter.ai`.
  - Free-mail: `gmail.com`, etc. — non-identifying; don't count toward external-domain total.
- **Fireflies emits duplicate Meeting rows** (same meeting 2–3×, one per participant's FF account). NEW dedup requirement not in the PRD: collapse Meeting rows by transcript-id (or title+date) before the reasoning agent fires, or it runs N× and multiplies credits + drafts.
- Fireflies rows arrive with `Internal/External` = null → matcher sets it.
