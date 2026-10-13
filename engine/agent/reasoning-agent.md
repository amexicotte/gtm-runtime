# Meeting reasoning agent

This is the operating specification for the one OpenRouter reasoning call used
by the monday meeting/manual-note runtime. The executable prompts are assembled
from `reasoning/reasoning_prompt.md`, `reasoning/manual_notes_prompt.md`, and the
configuration under `reasoning/config/`.

## Boundary

The model listens to one meeting or manual note and returns structured JSON. It
has no monday credentials and performs no matching, record creation, dedupe,
idempotency, Activity creation, or writes. Deterministic code resolves exact
Accounts/Contacts/Deals, validates evidence and policy, caches the result, and
applies effects.

Inputs are the full source content, source date/title, exact external attendee
email-to-Contact mapping, resolved prospect Account, at most one unambiguous
Deal, and a bounded live CRM snapshot. Human manual notes may correct Fireflies
speaker labels. Attendee emails remain authoritative for Contact identity.

## Listening output

Return one JSON object grouped under `deal`, `meeting`, `account`, and
`contacts`, using semantic keys from `reasoning/config/fields.yaml`. Also return:

- `relevant`: whether the source contains material CRM movement or context.
- `timeline_event`: one consolidated narrative with a short headline, 30–70
  word body (100 words maximum), and only useful labels from `Decision`, `Risk`,
  `Next step`, or `Task completed`.

Every writable field uses:

```json
{
  "status": "ANSWERED|PARTIAL|NOT_DISCUSSED",
  "value": "field-appropriate value",
  "evidence": {
    "excerpt": "short exact source excerpt",
    "confidence": 0.0
  }
}
```

The orchestrator adds the stable source ID and timezone-aware event timestamp.
The model never invents them. Partial, not-discussed, ambiguous, unsupported,
and incomplete-evidence values never write.

## Reasoning rules

- Use the durability test: if a fact remains true when this Deal dies, route it
  to the Account or Contact; otherwise route it to the Deal.
- Opportunities always belong to the prospect, never a partner on the call.
- Never guess among multiple plausible Deals.
- A clear signal may set New, Discovery, Proposal, or Negotiation. Never return
  Paused or Won. Return Lost only for an explicit, unambiguous decision not to
  proceed. A current Paused Deal is human-controlled and cannot be reopened.
- `Source` means acquisition/origination source, not call type or deal motion.
- Return numeric fields as numbers. For an explicit range, use its midpoint and
  preserve the range in the source excerpt.
- Put every MEDDIC element not discussed in `qualification_gaps` as the
  next-call agenda.
- Include every explicit Harbor commitment or chase item in
  `meeting.action_items`; the deterministic Activity writer owns creation and
  dedupe.
- Exclude pleasantries, signatures, repeated history, and unchanged context
  from the timeline event.

## Recovery invariant

The completed reasoning result, evidence metadata, and compact BusinessEvent
envelope are saved before CRM fields or narrative Updates. If a downstream
effect fails, the next poll resumes the cached result without another model
call. Slack `score:` and `feedback:` replies evaluate listening quality after
the final writes; they do not gate them.
