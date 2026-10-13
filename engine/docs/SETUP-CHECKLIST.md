# GTM Engine — manual setup checklist (Unity admin + wiring)

Everything code/schema is built (see repo). This is the list of steps that
require **admin/OAuth actions Claude Code cannot perform**, plus the wiring to
turn the pieces on. Ordered so nothing waits on something later.

## 0. Already done (no action)
- ✅ **Schema** — all fields, the Deliverables rename, the new **Tasks** DB, and
  the four review views are live under *Harbor Advisors HQ*.
- ✅ **Matcher** — built + tested (`../matcher/`, 13/13).
- ✅ **Reasoning-agent prompt** — `../agent/reasoning-agent.md`.
- ✅ **Fireflies → Notion** — already connected and writing Meeting rows.

## 1. Admin grants (Unity workspace owner/admin)
- [ ] Give the user **teamspace admin** on Harbor (create agents + automations).
- [ ] Enable **Custom Agents** (Business plan; may be admin-restricted).
- [ ] Decide the **matcher runtime** and enable it:
      - **Notion Workers** (native code runtime), or
      - **GitHub Actions 15-min cron** hitting the Notion API (no Worker enable
        needed — Harbor already runs this cron). *Recommended if Workers are
        gated.*
- [ ] Connect the **Slack AI connector** (owner/admin) with access to
      **`#call-notes`** and **`#deal-alerts`**.
- [ ] Slack admin: allow members to **create user groups** (needed to @mention
      agents).
- [ ] Confirm **Fireflies Pro** is active (full transcripts, not just summaries).
- [ ] **Credits:** clarify the pool (shared across all of Unity vs allocated),
      confirm the real included number (admin console — observed ~300), and who
      can buy more.

## 2. Verify the blocking unknowns
- [x] **Attendee format** — RESOLVED: emails arrive in the Meeting `Invited`
      field; `Attendees` holds display names. Matcher already keys on `Invited`.
- [ ] **Agent-triggers-agent via DB change** (20-min test, once agents are on):
      agent A flips a checkbox, agent B triggers on the change. **If it fails,**
      collapse to a single agent doing match-flag + enrich in one pass (the
      matcher decision can be passed inline instead of via a DB hand-off).

## 3. Wire the pipeline
- [ ] **Notes-submit agent** — native Custom Agent watching `#call-notes`:
      match a dropped note to the right Meeting row, write it into a `## Notes`
      section, set `Processing Status` = `Ready to Process`.
- [ ] **Matcher** — deploy on the chosen runtime. On each new/updated Meeting:
      run `parse_domains` + `classify_meeting`, set `Internal/External`, link the
      Company (or flag create / gate). On a gate, post the `slack_prompt` to
      `#deal-alerts` and hold. Run `dedupe_meeting_rows` first so Fireflies
      duplicates don't each trigger a pass — **log every collapse**.
- [ ] **Reasoning agent** — create one Custom Agent from
      `../agent/reasoning-agent.md`. Trigger: Meeting `Processing Status` =
      `Ready to Process` **and** matcher gate cleared.
- [ ] **12-hour fallback** — recurring scheduled agent: sweep Meetings still
      `Awaiting Notes` / unresolved after 12h and process on transcript alone
      (ambiguous ones → no company link, everything flagged into the queue).
- [ ] **Notify** — native Notion→Slack automation: new **Tasks** row with an
      `Assignee` → Slack message to that owner (title, `Task Type`, Company,
      Opportunity, `Due date`, links). Use an agent only if a plain automation
      can't carry the fields.

## 4. Cost controls
- [ ] Set the **native per-agent credit cap** on the reasoning agent (catches a
      runaway trigger loop — the real risk).
- [ ] Turn on **native admin credit alerts** (80% / 100%).
- [ ] Add a cheap **kill switch**: an "Agents Paused" checkbox each agent checks
      first. (Continuity: Fireflies keeps writing Meeting rows if credits run
      out — nothing is lost; meetings queue and process when credits return.)

## 5. Pilot
- [ ] Run **5–10 real meetings** through end-to-end.
- [ ] Check the credit dashboard after week one against the ~45 meetings/month
      estimate (should be low tens of $/month).
- [ ] Tune the **one** reasoning prompt on real misses; tune matcher drop-lists
      in `../matcher/domains.py` if a domain is mis-bucketed.

## Acceptance (from PRD §9)
Clean external call → correct Company/Contact/Opportunity drafts + tasks, zero
hand-entry, user only approves. Ambiguous call → one Slack prompt, one-line reply
routes it, same party never prompts again. Internal multi-deal call → tasks +
flagged deals, none guessed. Re-run → no duplicate tasks, no overwritten fields
(diff proposed instead). MEDDICC + Expected revenue populate → a forecast exists.
