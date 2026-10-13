# harbor-gtm-engine

> monday migration: the original Notion runtime remains below as reference.
> The active monday slices are `runtime/monday.py` (live-column API client),
> `runtime/gate.py` (0/1/2+ resolve-or-create gate), and
> `runtime/monday_pipeline.py` (one reasoning call → `reasoning/writer.py`).
> This active path writes final values to monday and collects listening-quality
> feedback in Slack; it has no monday approval queue.
> Run the offline migration tests with:
> `python3 tests/test_gate.py && python3 tests/test_monday_pipeline.py`.

Capture-to-CRM pipeline for Harbor (PRD v1). A partner dumps call notes once; the
right Notion records update themselves as **drafts**, tasks get created, a human
approves a queue. Goal: a current, forecastable pipeline. Ladders to **$2M / $1M**.

**One rule:** scripts over LLMs. The only language-reasoning step is the
reasoning agent. Everything else — domain matching, dedup, routing, gating — is
deterministic code or native Notion config.

## Layout
```
matcher/            Deterministic matcher (NO LLM). Domain drop-lists, the 4-branch
  domains.py        classifier, THE GATE, and Fireflies duplicate-row collapse.
  matcher.py        Pure logic — Notion I/O injected, fully testable.
runtime/            External runtime (GitHub Actions). config, notion, slack,
                    reasoning (OpenRouter), writes (discipline), pipeline, main.
agent/
  reasoning-agent.md  The one LLM pass, as an operating spec (API form in runtime/reasoning.py).
schema/
  review-views.md   Retirement note for the old monday approval views.
docs/               DATA-MODEL · RUNTIME · MODELS · SLACK-SETUP · SETUP-CHECKLIST · ACTIVATION-RUNBOOK
tests/              test_matcher.py (13) · test_writes.py (9)
.github/workflows/  gtm-engine.yml — scheduled runner
```

## Runtime decision: external, not native
Notion **Custom Agents and Workers are workspace-admin-gated and unavailable** to
Harbor's teamspace owner. So reasoning + orchestration run **externally on GitHub
Actions**, with the reasoning step on a **cheap model via OpenRouter (DeepSeek
V4-Pro default, swappable by env var)** — ~$1–2/month, no Anthropic API, no OAuth
complexity. No shared Unity credit pool, no agent-chain risk, write-discipline in
code. Fireflies→Notion capture stays native. See `docs/RUNTIME.md` + `docs/MODELS.md`.

## State (2026-07-29)
- **Live in Notion:** all schema, the Deliverables rename, the new Tasks DB, `Gated` status, 4 review views.
- **Built + tested offline:** matcher (13/13) and write-discipline (9/9); full runtime package compiles clean.
- **Runtime built:** scheduled GitHub Actions job — dedupe → match/gate → OpenRouter reasoning → write drafts → tasks → notify. Secrets: `OPENROUTER_API_KEY`, `NOTION_API_TOKEN`, `SLACK_BOT_TOKEN`.
- **Pending you:** create the Slack app (`docs/SLACK-SETUP.md`), the OpenRouter key, and the Notion integration token; set them as GitHub secrets. Then a live-validation pass (`runtime/README.md`).
- **Native transcript:** Fireflies syncs the full transcript into the Notion page body (integration toggle) — reasoning reads the body directly; no Fireflies API/key.
- **Flagged follow-ups:** the two Slack parsers + owner→Slack mapping — implemented to conventions, verify on first live note/reply.
- **Deferred (your call):** 3 rename collisions in `docs/DATA-MODEL.md`.
