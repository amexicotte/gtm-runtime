# Runtime design (external, subscription-based)

Supersedes the "native Notion agents" plan (Custom Agents + Workers are
workspace-admin-gated, unavailable). No 15-min cron, no Anthropic API.

## Settled decisions (2026-07-28)
- **Trigger:** scheduled run, **no receiver / no standing serverless infra**
  (Option B). Runs every ~30 min during business hours (configurable). Polling
  makes only cheap Notion/Slack REST calls — the model runs once per *new*
  meeting, so cadence affects latency, not model cost.
- **Reasoning:** **Claude via your subscription (OAuth)** — driven headlessly
  (Claude Code / Agent SDK), **not** the metered Anthropic API. One pass per
  meeting. Provider is behind an interface so a cheaper/local model can be
  swapped in later.
- **Notion access:** internal-integration token (REST). **Slack:** our own bot
  token (read + write). **Capture:** Fireflies → Notion, native, unchanged.
- **Write-discipline moves into code** (not trusted to the model): blank→write,
  filled→append to `Proposed Changes`, never touch `Verified`.

## Pipeline (one scheduled invocation)
1. **Pull** Meetings that are new/unprocessed (and Slack `#call-notes` messages
   since last run, and `#deal-alerts` replies).
2. **Dedupe** Fireflies duplicate rows (`matcher.dedupe_meeting_rows`).
3. **Notes:** match `#call-notes` messages to Meetings → write `## Notes`, mark
   `Ready to Process`.
4. **Matcher** (`matcher.classify_meeting`): set `Internal/External`, link
   Company / flag-create / or post the gate prompt to `#deal-alerts` and hold.
5. **Gate replies:** apply `#deal-alerts` answers → set company, persist
   `Domain Role` / `Represents` (learn-once), release.
6. **Reason:** for each ready+gated-clear meeting, one subscription-Claude call →
   structured extraction (MEDDICC, revenue, tasks, etc.).
7. **Write** drafts to Notion with code-enforced write-discipline; create Tasks
   (dedupe on `Source meeting`); set `Qualification Gaps`, `Review Status`.
8. **Notify:** new Task w/ owner → Slack DM to that owner.
9. **Fallback:** meetings with no notes after 12h → process on transcript alone,
   flagged into the review queue. (This is the one job that must run on a timer
   regardless — infrequent.)

State is tracked in Notion itself (`Processing Status`), so the scheduler is
stateless and safe to re-run — nothing double-processes.

## Reasoning input — FULL transcript, native (2026-07-29)
Fireflies' Notion integration has a toggle to sync the **full transcript into the
Notion page body** — it's now on. So the reasoning step reads the page body
directly (`notion.block_children_text`): transcript + any appended `## Notes`.
**No Fireflies API, no key, no custom script** — the earlier `fireflies.py` was
removed. Summary properties are used only as a fallback if a page has no body
(e.g. a non-Fireflies meeting).

Cost note: full transcripts are larger than summaries, but at DeepSeek V4-Pro
prices even 2–3M input tokens/month is ~$1–1.50 — still well under budget.

## OPEN DECISION — where does this run?
"No infra" + "subscription OAuth" have a wrinkle: subscription auth wants a place
that stays logged in, and GitHub Actions runners are ephemeral.

| Host | Fit with "no infra" | Subscription-OAuth robustness | Notes |
|---|---|---|---|
| **GitHub Actions** (creds stored as a secret) | Best — fully managed | Fair — must seed the Claude OAuth credential as a secret; may need periodic re-auth when it rotates. Confirm your plan permits headless/automated use. | Truest "no infra" |
| **A host you keep logged in** (your Mac via launchd, or a cheap always-on box) | Some infra (the box must be on) | Best — `claude login` persists and auto-refreshes | Mac = fine for the pilot; always-on box = production |

Recommendation: **GitHub Actions with the subscription credential injected**, to
honor "no infra"; fall back to a logged-in host if the credential upkeep gets
annoying or the plan doesn't allow CI use.

## Secrets (set in the chosen host; never shared with Claude)
`NOTION_API_TOKEN` · `SLACK_BOT_TOKEN` · Claude subscription OAuth credential.
