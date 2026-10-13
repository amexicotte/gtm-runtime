# Pre-sale monday CRM automation reference

This is the optional detailed companion to the team working agreement. The
current listening system is a **pre-sale CRM workflow**. It keeps prospect,
Contact, Account, Deal, and Activity information current through the sales
process. It is not yet a post-sale engagement-management or delivery system.

monday is the system of record. Fireflies, email, Slack, and eventually Muthur
are source and interaction surfaces around it.

## Current operating state — Sunday, August 23, 2026

- At 7:00 p.m. ET, monday reached data parity with the Google Sheets CRM. The
  sheet is retired as an operating CRM; monday is the only current system of
  record for pipeline and commercials.
- The migration verified 164 historical Deal rows and created the missing Account,
  Deal, and limited Client Project relationships without importing unreliable
  legacy fee text.
- The native Commander dashboard now reports pipeline and estimated closes from
  open Deal Value. Thirty-three open Deals still need a human rough-total estimate.
- Dedicated Fixed Fee, Monthly Retainer, Hourly Rate, and Estimated Hours fields
  are live. `Calculated Deal Value` uses a three-month retainer planning horizon;
  the legacy generic Fee field has been removed.
- Executed-contract reasoning, commercial finalization, Won/Customer transition,
  Drive archiving, and collaboration-onboarding logic are merged and structurally
  ready. Production DocuSign extraction remains inactive until the required
  DocuSign account access and rollout gates are completed.
- Shared-channel listening and the Muthur connection are still being completed.

## Standard meeting workflow

1. Fireflies captures an external meeting automatically.
2. The meeting runtime deduplicates team copies and matches attendees to monday
   Contacts and Accounts using exact email addresses and company domains.
3. The Fireflies summary appears in `#call-notes`.
4. Team members have 12 hours after the meeting to paste any personal notes into
   that meeting's Slack thread. Notes are combined with the transcript; no
   transcript summary or capture-verification step is expected from the team.
5. After notes arrive—or after the 12-hour fallback—the agent performs one saved,
   retry-safe reasoning pass and updates supported pre-sale fields.
6. Explicit Harbor commitments become Activities. The listening summary and next
   steps return to the same Slack thread.
7. Optional `score:` and `feedback:` replies are saved for evaluation.

The flow will not guess between multiple companies or active Deals. It asks for a
human decision in the same thread when identity is genuinely ambiguous.

## Standard email workflow

1. The initial meeting creates or matches the first external Contact, Account,
   and active Deal.
2. monday's existing Gmail connections place subsequent correspondence in Emails
   & Activities.
3. At 2:00 a.m. ET, the listener scans new email timeline items from monday. It
   does not access Gmail directly.
4. It removes signatures, quoted history, calendar noise, receipts, and other
   non-material content; deduplicates messages; and groups a thread into one
   business event.
5. Exact Contact identity and existing Account/Deal relationships determine the
   CRM targets. One reasoning pass may update supported fields, append narrative
   Updates, create Activities, or complete a uniquely matched Activity when an
   outgoing email provides exact evidence.
6. New interaction timestamps suppress stale-Deal and unanswered-follow-up
   nudges. Stable event keys prevent duplicate effects on retries.

No routine team action should be required for normal follow-up email after the
first Contact and Deal have been established.

### Required email participant-expansion enhancement

The current implementation builds its nightly scan from Contacts that already
exist in monday. It reads sender, recipient, CC, and BCC addresses on discovered
messages, but it does **not** create or link a Contact for a newly introduced
email participant. Model-proposed writes for an unmatched Contact are explicitly
discarded.

This creates two coverage gaps:

- A colleague newly copied on a known Contact's thread is visible as a participant
  but is not added to the monday Contacts board or linked to the Deal.
- If later correspondence includes only that new colleague, the listener may no
  longer discover it because it scans existing Contact timelines.

Before calling email capture complete, the system should deterministically:

1. Identify new external participants on an email already matched to exactly one
   Account and active Deal.
2. Resolve by exact lowercase email and create a Contact only when no Contact
   exists.
3. Link the Contact to the resolved Account and Deal without guessing across
   multiple Accounts or Deals.
4. Include the new Contact's timeline in future nightly scans, even when the
   original meeting Contact is absent from later correspondence.
5. Dedupe retries and safely quarantine duplicate-email or cross-Account cases.

This must be implemented and tested as automation. It should not become a manual
contact-maintenance requirement for the team.

## Activities and nudges

- New Activities are assigned using Deal Owner, then Account Relationship Owner,
  then source owner, with the configured default as fallback.
- Future-dated Activities appear in `#deal-alerts` on their due date. Overdue or
  undated Activities appear on the next eligible poll.
- A ✅ reaction or `done` reply closes the linked monday Activity and removes its
  alert.
- A Deal with no activity for 36 hours can create an owner nudge.
- A completed follow-up with no client response for two days can create a chase
  nudge. A later interaction or response clears the condition.

## Commercial and stage ownership

Commercials are generally decided outside the meetings and email threads being
captured, so the automation does not have reliable evidence for the early
estimate. The Deal owner enters `Deal Value` as the rough total expected value.
It powers open-pipeline reporting, ranked Deal views, and the 33% estimated-close
planning view.

The Deal owner may also enter `Fixed Fee`, `Monthly Retainer`, `Hourly Rate`, and
`Estimated Hours` as they become known. Once the gated DocuSign path is active,
validated executed-contract terms replace those components as the commercial
authority. Until then, signed terms remain a manual monday update.

The meeting/email agent may move a Deal through supported open stages when the
evidence is clear. Closed Won remains a human decision unless the separately
gated DocuSign writer finalizes an exact completed contract. The pre-sale
workflow largely ends at the handoff after a terminal outcome.

## Human and automated ownership

| Area | Automation owns | Team owns |
| --- | --- | --- |
| Meeting capture | Fireflies ingestion, identity, extraction, monday Updates, dedupe, retry | Adding personal notes within 12 hours; answering rare ambiguity prompts |
| Email memory | Nightly discovery for known Contacts, thread reasoning, supported updates, Activities, timestamps | No routine action; participant expansion remains an implementation gap |
| Pipeline stages | Clear open-stage movement and explicit Lost signals | Closed Won and correcting an obvious error |
| Commercials | Calculated Deal Value; gated DocuSign finalization once activated | Rough total Deal Value and any known fee components before signature |
| Activities | Creation, evidence-gated email completion, due-date alerts, nudges | Doing the work and closing an item when automation cannot prove completion |
| Post-sale | No broad engagement-management automation | Existing onboarding, delivery, and engagement-management process |

## Shared Slack listening

Shared prospect and client Slack channels are not yet being read. The current
Slack integration is limited to `#call-notes` and `#deal-alerts`.

The intended channel-listening design uses one administrator-installed app—not
individual employee credentials. Each in-scope Deal needs a canonical channel
mapping and the app must be present in that channel. Signed, deduplicated channel
events should reuse the same identity, evidence, field-policy, Activity, audit,
and retry path used for meetings and email.

Remaining work includes adding a Deal-level channel field, defining safe channel
mapping, extending the Slack app or Muthur adapter with narrow read access, and
validating in shadow mode plus a bounded live canary.

## Muthur: the catch-all conversational layer

Muthur is the final catch-all for anything passive meeting, email, DocuSign, and
channel listening do not handle automatically. Alex is working with Drew to
connect Muthur to this system and make it the main intentional interaction layer
inside Slack. Once integrated, a team member can tag Muthur to:

- Explicitly update a monday field through a manual request.
- Give workflow feedback or submit a feature request.
- Ask for summaries or answers derived from current monday data.

Muthur should use the existing controlled-action permissions, approvals,
idempotency, and audit trail. It does not replace automatic listening or monday's
role as the system of record. It covers the long tail without creating another
place the team must maintain. Its universal feedback route is still future work;
current meeting feedback remains in `score:` and `feedback:` replies.

## Other boundaries

Broad post-sale engagement management and shared Slack/Microsoft Teams ingestion
are not live. The DocuSign implementation is built, but production polling,
contract extraction, commercial finalization, and automatic Closed Won remain
inactive until DocuSign access and the shadow/canary/retry gates are complete.

The governing technical specification is
[`harbor-business-listening-agent-prd-v2.md`](harbor-business-listening-agent-prd-v2.md).
