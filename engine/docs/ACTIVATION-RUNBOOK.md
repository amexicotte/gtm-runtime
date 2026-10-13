# Activation runbook (UI steps)

Everything code/schema is built. This is the click-through to turn it on. Steps
marked **[Notion UI]** / **[Slack]** are yours — the connector I use can't create
agents, automations, or act in Slack. Steps marked **[Claude]** I can do via the
API on your say-so.

Notion's Custom-Agent builder is new and labels shift; where I say "look for X"
and you see something different, tell me the wording and I'll re-map the step.

---

## Step 0 — Feature-access check (2 min; settles "teamspace owner vs workspace admin")

Open Notion **Settings** (top-left, your account/workspace menu):

1. **Custom Agents** — look under *Notion AI* / *AI* (or *Members → permissions*
   for "who can create agents"). You're checking two things: (a) is the feature
   **on** for the workspace, (b) are **you** allowed to create agents.
   - If you can open an "New agent / Create agent" flow → ✅ you can build.
   - If it's greyed out, hidden, or says "ask your admin" → ❌ workspace-admin gate;
     Unity's workspace owner must enable it.
2. **Slack connector (Notion side)** — Settings → *Connections* (or *Connectors*)
   → find **Slack**. Can you connect/authorize it? If it says an admin must
   approve → ❌ workspace-admin gate.
3. **Workers** — only relevant if the matcher runs as a Notion Worker. Look under
   *Notion AI* / developer settings. If absent, we use the GitHub Actions cron
   instead (no Notion Workers needed).

**Report back which of the three are green vs walled.** Green → do the steps
below. Walled → hand that specific toggle to the Unity workspace owner; the rest
of the runbook still stands.

---

## Step 1 — Slack prep  **[Slack]** (you have Slack admin)
- [ ] Create public channels **`#call-notes`** and **`#deal-alerts`**.
- [ ] Allow members to **create user groups**, and make the group used to
      @mention the agent (Slack admin setting).
- [ ] Install/approve the **Notion (AI) Slack app** in the workspace.
- [ ] In Notion, connect the **Slack connector** (Step 0.2) and grant it access
      to those two channels.

## Step 2 — Notes-submit agent  **[Notion UI]**
Create a Custom Agent that watches **`#call-notes`**:
- **Trigger:** new message in `#call-notes`.
- **Job:** match the note to the right **Meeting** row (by title/date/attendees
  in the message), write the note text into a **`## Notes`** section on that
  Meeting page, then set **`Processing Status` = `Ready to Process`**.
- **Access:** the Meetings (Meeting Notes) database only.
- Keep replies to plain Slack messages (no buttons).

## Step 3 — Reasoning agent  **[Notion UI]** — the core one
- [ ] Create one Custom Agent; paste **`../agent/reasoning-agent.md`** as its
      instructions verbatim.
- [ ] **Trigger:** Meeting `Processing Status` = `Ready to Process` **and** the
      matcher has set the company/gate (see Step 6). Simplest: trigger on
      `Processing Status = Ready to Process`; the instructions already no-op if
      no company is linked and the call wasn't gated.
- [ ] **Access (grant only these):** Meetings, Companies, Contacts &
      Referrals, Opportunities, **Tasks** (new sales-side DB). **Not**
      Deliverables, not Billing.
- [ ] Model: the most capable available (this is the one reasoning step).
- [ ] (Optional kill switch) Add a first line to the instructions: "If the
      *Agents Paused* flag is checked, stop." and make a one-row settings page
      with that checkbox — I can add that field if you want it.

## Step 4 — 12-hour fallback  **[Notion UI]**
- [ ] Recurring scheduled agent: sweep Meetings still `Awaiting Notes` /
      unresolved after 12h; process on transcript alone. Ambiguous ones → no
      company link, everything flagged into the review queue.

## Step 5 — Notify  **[Notion UI]**
- [ ] Native Notion→Slack automation on the **Tasks** DB: new row **with** an
      `Assignee` → Slack DM/message to that owner with `Task`, `Task Type`,
      `Company`, `Opportunity`, `Due date`, and links. Use a plain automation if
      it can carry the fields; an agent only if it can't.

## Step 6 — Matcher runtime  **[Claude drafts + you deploy]**
Deterministic gate; must run before the reasoning agent. Pending your call:
- **GitHub Actions cron** (recommended, no Workers needed): I'll write the
  wrapper that pulls new/updated Meetings via the API, runs `classify_meeting` +
  `dedupe_meeting_rows`, writes `Internal/External` + Company link (or posts the
  gate prompt to `#deal-alerts`), and sets readiness. Runs on Harbor's existing
  15-min cron.
- **Notion Worker:** only if Step 0.3 shows Workers available.

## Step 7 — Cost controls  **[Notion UI]**
- [ ] Native **per-agent credit cap** on the reasoning agent (runaway-loop guard).
- [ ] Native **admin credit alerts** at 80% / 100%.
- [ ] Optional **kill switch** flag (Step 3).

## Step 8 — Blocking-unknown (b): agent-triggers-agent  **[Notion UI, 20 min]**
- [ ] Test: agent A flips a checkbox on a row → agent B triggers on that change.
      **If it works,** the notes-submit → reasoning hand-off via `Processing
      Status` is sound. **If it fails,** tell me — I'll fold match-flag + enrich
      into a single agent so there's no DB hand-off.

## Step 9 — Pilot
- [ ] 5–10 real meetings end-to-end. Watch the credit dashboard vs ~45/mo.
- [ ] Tune the one prompt on real misses; tune `../matcher/domains.py` drop-lists.
