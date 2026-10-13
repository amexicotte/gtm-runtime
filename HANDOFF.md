# Harbor — monday CRM + Meeting Engine · Coding Agent Handoff

## What this is
Harbor Advisors (a GTM advisory firm) has moved its CRM to monday.com and is rebuilding its
Fireflies → CRM "meeting engine" in a **split architecture**: the heavy LLM reasoning runs
**outside monday** (cheap, model-of-choice via OpenRouter), and **monday holds the data and
runs only free, non-AI automations**. Architecture diagram: `docs/harbor_engine_split_architecture.mermaid`.

The external runtime is now the chosen implementation. It owns matching, gating,
reasoning, CRM writes, Activities, and Slack accountability; monday remains the
system of record.

Principle to preserve (from the original engine): **scripts over LLMs — exactly one LLM pass;
everything else is deterministic.** Model output is written as final CRM state; humans use Slack
to score the listening summary and provide quality feedback, not to approve monday changes.

## What's already DONE — do not rebuild
- **monday CRM schema is built** (Accounts, Contacts, Deals, Meetings [new], Activities), additively
  over an existing import. Full board / field / column-id map: `docs/harbor_monday_crm_schema_map.md`.
- **Slim reasoning prompt + config**: `reasoning/reasoning_prompt.md` + `reasoning/config/{fields,entities,routing}.yaml`,
  assembled by `reasoning/build_prompt.py` into a ~900-token static system prompt (caches ahead of the transcript).
- **Deterministic monday writer**: `reasoning/writer.py`. Builds the column map **live** from monday
  (no hardcoded IDs), maps semantic keys → columns, overwrites mapped fields with final values,
  and enforces the two relationship rules: the meeting links to **BOTH** companies;
  any Deal ties to the **PROSPECT only**.
- **Notion → monday backfill** (optional/reference): `migrations/backfill_accounts.py`.
- The prior Notion engine is in `engine/` — reuse its structure. Key files: `engine/matcher/` (matcher +
  domain drop-lists), `engine/agent/reasoning-agent.md` (the operating spec), `engine/runtime/reasoning.py`
  (OpenRouter call), `engine/runtime/slack.py`, `engine/runtime/pipeline.py`.

## Implemented runtime requirements

### 1. Matcher / gate — three-way, resolve-OR-create  (upgrade `engine/matcher/`)
Parse external attendee domains; drop internal (`harboradvisors.example`), notetaker (`fireflies.ai`, `otter.ai`),
and free-mail domains (all listed in `reasoning/config/entities.yaml`). Then, by count of external companies:
- **0** → set Meeting `Processing Status = "Gated"`, stop.
- **1** → resolve the Account by domain; **if it doesn't exist, CREATE it from the domain** (monday's
  Crunchbase enrichment fills name/details) and create Contacts from the attendees; link Account +
  Contacts to the Meeting; set `Processing Status = "Ready"`.
- **2+** → post a **Slack prompt** listing the companies; a human picks the **PROSPECT** (the others are
  partners on the call). On pick: resolve-or-create each, link the Meeting to **BOTH**, set `Processing Status = "Ready"`.
  (Reuse `engine/runtime/slack.py`; the Notion flow already had this disambiguation.)

Emit a `ctx` json for the writer:
`{"meeting_id":…, "prospect_account_id":…, "partner_account_id":… , "deal_id":…, "contact_ids":[…]}`

### 2. Reasoning pass (one LLM call)
`python reasoning/build_prompt.py` → the system prompt. Call OpenRouter (reuse `engine/runtime/reasoning.py`)
with `[system prompt] + [transcript]`. The model returns **semantic JSON** (keys like `identified_pain`,
`champion_name`), grouped by `deal` / `meeting` / `account` / `contacts`.
The result is persisted on the Meeting before any downstream write. A failed
monday write resumes from that cached JSON on the next poll without a second
model call. The cache uses a `Reasoning Cache` text column because monday caps
long-text columns at 2,000 characters.

### 3. Write to monday (already built)
`from writer import apply` → `apply(result_json, ctx)`. No new logic — it maps semantic keys to live
columns, fills empty-only, links both companies, and keeps Deals on the prospect.

### 4. Feedback and accountability
- Account, Contact, Deal, and Activity writes are final; affected records are
  marked `Verified`, and Meetings finish as `Processed`.
- The originating `#call-notes` thread receives the agent's listening summary.
  Replies using `score: 1-5` and/or `feedback:` are persisted with the model run.
- Meeting commitments become assigned Activities. New Activities are posted to
  `#deal-alerts`; replying `done` or reacting ✅ closes the Activity
  on the next poll.

### 5. Deal lifecycle
- A first Intro/Sales meeting creates a Discovery Deal only when the Account has
  no active Deal.
- Exactly one existing active Deal is reused. Multiple active Deals are never
  guessed; processing gates and alerts a human.
- A Client/Project call for an Account already marked `Active Client` reuses its
  single Won Deal, or creates the missing Won Deal plus active Client Project.
  Prospect Accounts never get an inferred Won Deal, and multiple Won Deals are
  never guessed. Deal Source is only an explicitly evidenced
  acquisition/origination source.

## Credentials (the only manual step — set once)
- `MONDAY_API_TOKEN` — monday personal API token (profile → Developers → API token → Show).
- `OPENROUTER_API_KEY` — for the reasoning pass.
- `NOTION_API_TOKEN` — only if running the backfill.
Put them in a local `.env` (already gitignored in `engine/`). Never commit tokens.

## Key facts
- Board IDs: Accounts `10000000001` · Contacts `10000000002` · Deals `10000000003` · Meetings `10000000004` · Activities `10000000005` · Client Projects `10000000006`.
- monday users: Alex `100000007` · Sam `100000008`.
- **Never hardcode column IDs.** `writer.py` resolves them live by column title. To add a field: create the
  column in monday, then add one line to `reasoning/config/fields.yaml`. To add a partner/sponsor: one line in `entities.yaml`.
- Write discipline: extracted values are final (`Processing Status = "Processed"`), mapped fields
  are overwritten, Deal `Stage` never advances past a verbal-yes from a call alone, and the runtime
  remains **sales side only** (don't touch billing/delivery boards).
- Requirements: Python 3, `pip install pyyaml` (+ the engine's `requirements.txt`).

## Operating order
Run `runtime.schema_main` once → deploy GitHub secrets → run a dry-run workflow →
run one real meeting → inspect the listening feedback thread and `#deal-alerts` in Slack.

For Phase 2 email activation and the still-outstanding three-night shadow gate,
use `docs/phase-2-email-listening-activity-reconciliation-and-nudges.md`. Do not
set `EMAIL_RUN_MODE=live` until its explicit reasoning, shadow-review, observation,
and automatic-write gates are all satisfied.

## Live status — 2026-08-02
- The external polling path is wired end to end: Fireflies dedupe/ingest → deterministic
  company/contact gate → one exact Slack thread per Meeting → cursor-based manual-note
  collection → one combined reasoning pass → final monday writes → Slack feedback.
- GitHub requests a poll every five minutes during business hours. Scheduled
  runs skip the regression suite; manual runs still execute it. GitHub cron is
  best-effort, so a separate heartbeat/external trigger remains the production
  fallback if scheduled runs continue to be dropped.
- New `#call-notes` prompts include the native Fireflies summary. Fireflies summaries are also
  saved on Meetings. Slack reply scans start at each Meeting's stored timestamp; there is no
  static three-day reread.
- Intro/Sales calls resolve or create one active Deal after the reasoning pass.
  Client/Project calls for an Account already marked `Active Client` reuse the
  single Won Deal or create the missing Won Deal and active Client Project.
  Deal Source remains blank unless acquisition/origination is explicitly evidenced.
- Extracted commitments now create native Activities, deduped by Source Meeting + title. They
  carry `Do it`/`Chase`, Owner, due date, `Verified`, Slack link, and relations to the Account,
  Contact, Deal, and Meeting.
- Lumen live validation/backfill completed: Meeting `10000000009`, Won Deal `10000000010`,
  and five linked Activities. Account and Deal mirrors both resolve to Lumen.
- 71 deterministic tests pass locally and as a mandatory preflight in GitHub Actions.
  Schema setup is live and idempotent, and consecutive production polls completed
  cleanly without duplicate work; the Slack completion loop also closed one Activity.
  The remaining Item Card layout change is manual in monday:
  use one Text widget for Summary, one for Action Items, and a Table widget sourced from
  `link to Activities`; the user is managing that presentation layer directly.
- Fireflies attendee emails and display names are stored on each Meeting. Accounts
  resolve by domain; Contacts resolve by exact lowercase email; the reasoning model
  is prohibited from guessing identity from transcript text.
- Every engine Activity resolves owner in this order: Deal Owner → Account
  Relationship Owner → Meeting Owner → Alex. The public Slack loop only touches
  Activities linked to a Source Meeting, leaving legacy/manual Activities alone.
