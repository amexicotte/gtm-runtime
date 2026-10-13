# CRM field dictionary and write policy

**Governing contract:** `docs/harbor-business-listening-agent-prd-v2.md`
**Schema baseline:** `docs/live-monday-schema.json`, captured 2026-08-31
**Machine-readable dictionary:** `reasoning/config/fields.yaml`

## Coverage

The dictionary explicitly defines all 203 current columns across
Accounts, Contacts, Deals, Meetings, Activities, and Client Projects. It also
defines 15 Phase 1 managed additions: `Slack Channel` on Client Projects and the
14 fields (including Name) on the private Agent Events ledger. Phase 2 adds 17
managed audit/state fields: six on Deals for exact interaction/inbound timestamps
and one active Slack nudge, plus eleven on Activities for email source dedupe,
evidence-based completion, reply waiting, and one active follow-up nudge. Release
integration adds four long-text field-provenance ledgers on Meetings, Accounts,
Contacts, and Deals. The Deal-fee schema adds four contract inputs and one
formula. `Calculated Deal Value` uses a three-month planning horizon for Monthly
Retainer, plus Fixed Fee and estimated hourly work. The redundant legacy Deal
`Fee` column was retired after its only value
was preserved in `Fixed Fee`. Phase 4 adds nine executed-contract/audit/archive
fields plus eight client-onboarding state/channel fields. The commander dashboard
adds two reporting-only Deal formula columns, and the Deals board adds a
monday-derived `Created Date` creation-log column, for 61 managed additions total.
Each entry records
the live title, Phase 0 column ID/type where applicable, one owner, one write
policy, whether it is exposed to the reasoning prompt, and its evidence rule.

Run the deterministic drift/coverage check from `engine/`:

```bash
.venv/bin/python -m runtime.field_policy
```

The check fails for missing/duplicate columns, unmanaged ID/type drift, unsupported
policies, missing owners, model-writable fields without evidence, any writable
Client Project field, lifecycle-label drift, or a Deal Stage policy that allows Won.

## Policies

| Policy | Meaning |
| --- | --- |
| `overwrite` | A newer, fully evidenced current-state fact may replace the mapped value. |
| `merge` | Append a new durable fact without blanking or repeating existing narrative context. |
| `explicit_only` | Write only an explicit source statement with complete evidence; never infer it from surrounding context. |
| `bounded` | Apply `explicit_only`, then require one configured allowed value. |
| `relation` | Resolve the value to an exact monday item ID before writing the relation. |
| `never` | The listening model/writer cannot mutate the column; its declared owner remains authoritative. |

Every model-writable field requires an evidence wrapper containing a source
excerpt and numeric confidence. Deterministic code adds the stable source ID and
timezone-aware event timestamp. `PARTIAL`, `NOT_DISCUSSED`, ambiguous,
unsupported, out-of-range, and incomplete-evidence values do not write.

## Binding object boundaries

- Account and Contact Notes, and Deal Notes, use `merge`; later events never
  blank durable context.
- Deal Stage is bounded to New, Discovery, Proposal, Negotiation, and explicit
  Lost for the listening writer. Won is available only to the separate
  deterministic Phase 4 executed-contract writer after its exact gates.
- Paused is a human-controlled Deal hold. It is excluded from open-pipeline
  formulas and stalled-Deal nudges, and the listening writer cannot enter or
  exit it. Completed is not a Deal stage.
- Account Type is `Active Client` while any related Client Project is active and
  `Completed - Past Client` once all related Client Projects are inactive. Won
  Deals remain Won so sales history is never rewritten as delivery state.
- Every model-writable status/dropdown uses declared live labels and mutations
  prohibit automatic label creation.
- Field provenance is updated atomically with an accepted write; a delayed or
  retried older event cannot overwrite a newer accepted fact.
- Identity, owner, processing, source-adapter, duplicate/reflection relation,
  formula, app, review-queue, and automation columns use `never`.
- Account `Type` remains outside model write authority. A human reply in the
  exact `#call-notes` thread may deterministically set the prospect Account to
  `AI Investment` with `Type: AI Investment`; partner Accounts are never changed.
- Activities remain owned by deterministic code. Phase 2 completion fields are
  never model-writable: the reconciler accepts only a unique retrieved Activity,
  a verbatim outbound excerpt, configured high confidence, and required delivery
  link/attachment evidence.
- `Last interaction` remains outside the model writer. The event writer stamps it
  monotonically from exact event time and records the source event separately.
- Every structured Client Project field is `never`. monday automation `100000015`
  is the primary creator on Won; repository fallback writes only Account/Deal/
  Contact relations, and Phase 1 adds narrative Updates plus `Slack Channel`.
- Collaboration platform/channel values are written only from an explicit
  validated `slack:` or `teams:` reply to the signed-client onboarding prompt.
- Agent Events fields are ledger-owned and never exposed to the model.
- `Commander | Open Pipeline` and `Commander | Estimated Close (33%)` are
  monday-derived reporting formulas. They are never model-writable and include
  only New, Discovery, Proposal, and Negotiation Deals.
- `Created Date` is monday's immutable creation log in date-only display mode.
  monday automatically supplies it for existing and future Deals, including
  Deals created by the meeting flow; no model or writer may set or clear it.
- `Mercury Customer ID` is owned by the external Mercury integration. The
  listening model and deterministic CRM writers may neither populate nor clear it.
