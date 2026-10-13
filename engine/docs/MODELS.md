# Reasoning model — cost evaluation

The reasoning step is **one call per meeting**: ~transcript + notes in, structured
extraction out. Volume is **~45 meetings/month, bounded** (one call each, guarded
by a per-run cap). That bound is the story: per-token pricing stays a few dollars
a month and is predictable.

## Token math (transparent; adjust as needed)
- Per meeting: ~15K input avg (transcript + notes + prompt; long calls 25–45K),
  ~1.5K output.
- 45/month → **~1M input + ~70K output**; heavy month ~2M input + ~140K output.
- Input is mostly **cache-miss** (each transcript is unique); the shared system
  prompt is tiny, so use cache-miss input prices below.

## Current pricing (fetched from provider docs 2026-07-29 — reverify before committing)
Per 1M tokens, USD. Sources: platform.kimi.ai, docs.z.ai, api-docs.deepseek.com.

| Model | Input (miss) | Output | ~Monthly (1M/70K) | Heavy (2M/140K) |
|---|---|---|---|---|
| **DeepSeek V4-Flash** | $0.14 | $0.28 | **~$0.16** | ~$0.32 |
| **DeepSeek V4-Pro** | $0.435 | $0.87 | **~$0.50** | ~$1.00 |
| **GLM-4.7-Flash** | free | free | **$0** (rate-limited) | $0 |
| **GLM-4.7-FlashX** | $0.07 | $0.40 | **~$0.10** | ~$0.20 |
| **GLM-4.7 / GLM-4.6** | $0.60 | $2.20 | **~$0.75** | ~$1.50 |
| **GLM-5.2** (flagship) | $1.40 | $4.40 | **~$1.70** | ~$3.40 |
| **Kimi K3** | $3.00 | $15.00 | **~$4.00** | ~$8.10 |

All 1M-token context. Prices exclude tax. (My training data predated K3 / GLM-5.x
/ DeepSeek-V4 — these are the live figures.)

## Read on the numbers
- **Cheapest capable:** DeepSeek V4-Pro (~$0.50/mo) and GLM-4.7 (~$0.75/mo) — the
  price/quality sweet spot for structured extraction.
- **Nearly free:** DeepSeek V4-Flash, GLM-4.7-FlashX, GLM-4.7-Flash — smaller/faster
  tiers; fine to try but likely weaker on nuanced MEDDICC extraction.
- **Priciest:** **Kimi K3 (~$4/mo, heavy ~$8)** — its $15/M output makes it 5–8×
  the others. Still cheap in absolute terms, but only worth it if it clearly wins
  the pilot on quality.

## Fixed vs variable
At this bounded volume, pay-as-you-go is ~$0.50–$4/mo depending on model. A
**per-run meeting cap** in code gives a hard ceiling → variable-but-capped ≈ fixed.
Flat plans (e.g. z.ai's GLM coding subscription, sold separately from the per-token
page) exist but would cost more than PAYG here — only worth it for a single tidy
line item. Self-hosting isn't viable (multi-GPU, thousands/mo).

## Recommendation
1. Reasoning step targets an **OpenAI-compatible API**; model + `base_url` + key
   set by **env var** — swap providers with zero code change.
2. **Pilot on real Harbor transcripts:** DeepSeek V4-Pro (default), GLM-4.7, and
   Kimi K3. Pick the cheapest that clears the quality bar. Access each via its
   direct OpenAI-compatible endpoint, or OpenRouter if it already carries these
   new versions.
3. Expect **under ~$2/month** unless Kimi K3 wins on quality (then ~$4–8/mo).
4. Add a per-run meeting cap as the hard cost ceiling.
5. **Plain API key** (not OAuth) → GitHub Actions runs clean, no persistent host.

Secrets (GitHub Actions; never shared with Claude): the model API key(s) +
`NOTION_API_TOKEN` + `SLACK_BOT_TOKEN`.
