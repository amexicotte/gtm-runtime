# Harbor CRM — Notion → monday Schema Map

*For review/approval before build. Source: live Notion data sources + the harbor-gtm-engine. Target: your monday CRM workspace. **v3 — Tasks→Activities, Deliverables→subitems, engine mapping added.***

---

## Resolved decisions

- Opportunities → **Deals** (pipeline); Projects → **Client Projects** (delivery). Two separate records. ✅
- **Tasks → the native Activities board** (not a custom board) — add the engine's columns. ✅
- **Deliverables → subitems under Client Projects** (not a standalone board) — human-managed PM. ✅
- **Meetings stays a custom board** — it's the transcript surface the agent reads/writes. Native agents can run on it. ✅
- Champion (Deals) → relational to Contacts. Source → simple type + relational Source Details. Migration fields dropped. ✅
- **Qualification Gaps → kept** on Deals (agent-generated next-call agenda; aids call prep). ✅
- **Native-first build.** Run as much as possible in monday's native automations/agents to dogfood it. Where native blocks, drop to a custom script and **save that script as a reusable customer-implementation asset**. ✅

**Footprint:** 5 CRM boards (Accounts, Contacts, Deals, Client Projects, Meetings) + native **Activities** for tasks + **Deliverables** as Client Projects subitems.

---

## Board map at a glance

| # | Notion source | → monday | Status |
|---|---|---|---|
| 1 | Companies | **Accounts** | Exists (reshape) |
| 2 | Contacts & Referrals | **Contacts** | Exists (reshape) |
| 3 | Opportunities | **Deals** | Exists (reshape) |
| 4 | Projects | **Client Projects** | Exists (reshape) |
| 5 | Meeting Notes | **Meetings** | New (custom — transcript surface) |
| — | Tasks (sales next-steps) | **Activities** (native) | Exists (add engine columns) |
| — | Deliverables | **Client Projects → subitems** | Native subitems |

---

## Field-type translation legend

| Notion type | monday column |
|---|---|
| Title | Item Name |
| Text / long text | Text / Long Text |
| Select | Status |
| Multi-select | Dropdown |
| Number ($) | Numbers (currency) |
| Date / date range | Date / Timeline |
| Email / Phone / URL | Email / Phone / Link |
| Person | People |
| Checkbox | Checkbox |
| **Relation** | **Connect Boards** (two-way) |
| Rollup / dynamic display | **Mirror** |
| Created / edited time | Creation Log / Last Updated |

---

## 1. Accounts *(from Companies)* — item name: **Company name**

| Notion property | → monday column | Type |
|---|---|---|
| Website | Website | Link |
| Domain | Domain | Text *(bare lowercase — matcher key)* |
| Status | Relationship Status | Status |
| Source | Source | Status *(cleaned)* |
| — new — | Source — Contact | Connect → Contacts |
| — new — | Source Contact's Company | Mirror *(dynamically populated from Source — Contact)* |
| Engagement Type (multi) | Engagement Type | Dropdown *(Outbound Acceleration, Lead Sourcing Tool, GTM Implementation, GTM System Buildout, GTM Retainer, Rev Ops Support, List Enrichment)* |
| Follow-up cadence | Follow-up Cadence | Status |
| Next follow-up | Next Follow-up | Date |
| Relationship owner | Relationship Owner | People |
| Notes | Notes | Long Text |
| Related Drive folder | Drive Folder | Link |

**Connects:** Contacts, Deals, Meetings, Client Projects, Source relations.
**Referral scorecard:** two-way Source connects give each partner an automatic "Companies Referred" rollup (your Morgan Lee / monday tracker).
*Dropped from the working UI: Review Status, Proposed Changes, Migration Batch, Represents. The engine may retain the first two legacy columns for compatibility, but it writes final values and does not use a review queue.*

---

## 2. Contacts *(from Contacts & Referrals)* — item name: **Name**

| Notion property | → monday column | Type |
|---|---|---|
| Email | Email | Email *(auto-log + matcher key)* |
| Phone | Phone | Phone |
| LinkedIn | LinkedIn | Link ✅ |
| Role | Role / Title | Text |
| Relationship type | Relationship Type | Status |
| Source | Source | Status |
| Follow-up cadence | Follow-up Cadence | Status |
| Next follow-up | Next Follow-up | Date |
| Last interaction | Last Interaction | Date |
| Notes | Notes | Long Text |

**Connects:** Account, Meetings, Activities.

---

## 3. Deals *(from Opportunities)* — item name: **Opportunity name**

| Notion property | → monday column | Type |
|---|---|---|
| Stage (Potential/Proposal/Verbal Yes/Active/Completed/Closed Lost) | Stage | Status |
| Source | Source | Status |
| Owner | Owner | People |
| Expected / Collected revenue, contract commercials | Deal Value / Collected Revenue / Fixed Fee / Monthly Retainer / Hourly Rate | Numbers ($) |
| Billing model | Billing Model | Status |
| Start / Target end date | Engagement Dates | Timeline |
| Service Type | Service Type | Dropdown |
| Economic Buyer / Decision Criteria / Decision Process / Identified Pain / Metrics | (same) | Long Text |
| **Champion** | **Champion** | **Connect → Contacts** |
| Competition | Competition | Text |
| Qualification Gaps | Qualification Gaps | Long Text *(agent-generated next-call agenda — kept for prep)* |
| Billing instructions / Invoice line item | (same) | Text |
| Notes | Notes | Long Text |

**Connects:** Account, Meetings, Client Project, Activities, Champion (Contacts).
**Formula:** weighted pipeline = Expected Revenue × stage probability → dashboard.
**Agent rule (from the engine):** Stage never advances past **Verbal Yes** from a call alone.
*Dropped from the working UI: Migration Batch, Review Status, and Proposed Changes. Legacy status/proposal columns may remain but no longer require human action.*

---

## 4. Client Projects *(from Projects)* — item name: **Project name**

| Notion property | → monday column | Type |
|---|---|---|
| Status (Not started/Active/Paused/Completed/Archived) | Status | Status |
| Health (On track/At risk/Blocked) | Health | Status |
| Start / Target end | Timeline | Timeline |
| Drive folder / Slack channel | (same) | Link |
| Project lead | Project Lead | People |
| Notes | Notes | Long Text |

**Connects:** Account, Deal (Opportunity), Activities.
**Deliverables live here as subitems** (below).
The runtime may create the parent Client Project when a Client/Project call is
matched to an `Active Client` and its single Won Deal. Deliverables remain
human-managed.

### Deliverables — **subitems of Client Projects**

| Field | monday column | Type |
|---|---|---|
| Task / deliverable | Subitem Name | |
| Status (Not started/In progress/Waiting/Blocked/Done) | Status | Status |
| Priority | Priority | Status |
| Task Type | Task Type | Dropdown |
| Due date | Due Date | Date |
| Assignee | Assignee | People |
| Blocked by | Blocked By | Text |
| Slack link | Slack Link | Link |
| Notes | Notes | Long Text |

*Human-managed. The automation never writes here (post-sales, behind the Closed-Won gate).*

---

## 5. Meetings *(from Meeting Notes — custom board)* — item name: **Meeting title**

| Notion property | → monday column | Type |
|---|---|---|
| **Transcript** | **Transcript** | Link **(primary — agent input)** |
| Date | Date | Date |
| Call type | Call Type | Status |
| Internal/External | Internal/External | Status *(matcher sets when null)* |
| AI/New Summary + Overview | Summary | Long Text *(agent-populated)* |
| Gist / Bullet Notes / Action Items | (same) | Long Text *(agent-populated)* |
| Processing Status (Awaiting/Ready/Processing/Processed/Gated) | Processing Status | Status |
| Proposal needed? | Proposal Needed? | Checkbox |
| Follow-up date | Follow-up Date | Date |
| Owner | Owner | People |
| **Invited (emails)** | **Attendee Emails** | Text/Email list **(matcher key — keep raw)** |
| Attendees (display names) | External Attendees | Connect → Contacts *(resolved from emails)* |
| — | Internal Attendees | People |
| — runtime recovery — | Reasoning Result | Long Text *(durable model output; resumes failed writes without another model call)* |
| — runtime recovery — | Processing Started At | Text *(UTC timestamp)* |

**Connects:** Account, Contacts, Deal, Activities.
**Engine ground truth:** `Invited` (emails) is the matcher key; `Attendees` (display names) are unusable for matching — so we keep the raw emails. Fireflies emits duplicate meeting rows (one per participant) → dedupe before the agent fires.

---

## Activities (native) — general Tasks / sales next-steps *(engine writes here)*

The native CRM Activities board, extended with the engine's Task columns.

| Field | monday column | Type |
|---|---|---|
| Task (action-verb) | Item Name | |
| Task Type | Task Type | Status (do-it / chase) |
| Status | Status | Status (Not started/In progress/Waiting/Blocked/Done) |
| Assignee | Assignee | People *(named → meeting owner → relationship owner → blank+flag)* |
| Due Date | Due Date | Date *(stated → relative to meeting → meeting +7d)* |
| Priority | Priority | Status |
| Review Status | Review Status | Status — runtime-written records are final / Verified |
| Slack link | Slack Link | Link |
| Notes | Notes | Long Text |

**Connects:** Account, Contact, Deal, **Source Meeting** (→ Meetings — **dedupe key**), Client Project.
Slack is the working UI for open Activities; monday stores their final state.

---

## Relationship map

```
Accounts ─┬─< Contacts ──(referred by / referrals)── Accounts
          ├─< Deals ──┬─ Champion >── Contacts
          │           ├─< Meetings
          │           └── Client Projects ──< (Deliverables = subitems)
          ├─< Meetings                       │
          └─< Client Projects                │
Activities >── Accounts / Contacts / Deals / Client Projects / Meetings (source meeting)
```

---

## Phase 2 — Fireflies → CRM engine, rebuilt on monday

### What it does (Notion version, from harbor-gtm-engine)
Scheduled every 30 min (business hours): pull Fireflies meeting rows → **dedupe** duplicate FF rows → **deterministic matcher/gate** (domain drop-lists, 4-branch classifier — no LLM) links or skips and sets Internal/External → **one LLM reasoning pass** reads transcript + `## Notes`, extracts the MEDDICC/revenue/stage/context "listening brief" (ANSWERED / PARTIAL / NOT DISCUSSED), routes each fact ("still true if the deal died?" → Company/Contact, else Opportunity) → writes **final CRM values** → creates **Tasks** (do-it/chase, deduped by Source Meeting) → posts the listening summary and task UI in **Slack**.

### Principles to preserve
Scripts over LLMs (one reasoning pass). Extracted values are final and overwrite their mapped CRM fields. Never guess a link; ambiguous identity is resolved in Slack before writing. Idempotent re-runs (dedupe by Source Meeting). Writes to the **sales side only**, never the billing/delivery side.

### Runtime choice — the thing that changes in monday
The engine ran **external (GitHub Actions + OpenRouter/DeepSeek)** *only because Notion's Custom Agents/Workers were admin-gated and unavailable*. In monday that blocker is gone — native agents/automations are available. Two options:
- **(A) Native monday agent + automations** — matcher/gate/dedup as automations (+ a small code step if needed); the reasoning pass as a monday AI agent on the Meetings board. Most native; best partnership dogfood.
- **(B) Keep the external engine** — reuse the tested matcher + reasoning spec verbatim; swap the Notion writer for monday's API.
**Decision (Alex): native-first (A).** Build in monday's native agents/automations wherever possible to dogfood the platform; validate the native agent against `agent/reasoning-agent.md`. Where native hits a blocker, drop to a custom script (option B pattern) **and save it as a reusable customer-implementation asset** — the native limits we hit are the gaps our clients will hit. The reasoning spec is execution-agnostic, so it ports either way.

### Piece-by-piece mapping
| Engine piece | monday home |
|---|---|
| Fireflies transcript | Meetings → Transcript field (FF→monday sync) |
| Dedupe duplicate FF rows | Automation keyed on transcript-id / title+date |
| Matcher drop-lists (harboradvisors.example **+ typo harboradvisers.example**, fireflies.ai, otter.ai, free-mail) | Matching step over Attendee Emails |
| Reasoning pass (MEDDICC extract + routing) | External engine writing final CRM values |
| Tasks (do-it/chase, dedupe by Source Meeting) | Activities board |
| Human interaction | Slack identity disambiguation, listening feedback, and Activity completion |
| Qualification Gaps (NOT DISCUSSED → next-call agenda) | Deals → Qualification Gaps (kept) |

Sequencing: **schema first**, engine second — the agent needs these boards/fields to exist.
