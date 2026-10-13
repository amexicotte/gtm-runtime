# Phase 5 — advisory portfolio pattern learning

The weekly Phase 5 job reads current Closed Won and Closed Lost Deals plus their
recent monday Updates. Deterministic code removes record names and IDs, redacts
email addresses, caps the cohort and prompt size, and calculates exact outcome,
pricing, service, billing, champion, and Update-coverage metrics. DeepSeek receives
that bounded snapshot once and returns only aggregate observations, limitations,
and useful questions.

The validated dated `Harbor Deal Pattern Brief` is stored in one private
`state:deal-pattern-brief` Agent Events row and exposed read-only through
`get_deal_pattern_brief()`. The writer has no code path to change a Deal, Account,
Contact, Activity, or Client Project; its audit records `deal_writes: 0`. An
unchanged input hash reuses the prior brief without another reasoning call.

Stage velocity is described only as a proxy from record creation to terminal
record update unless explicit stage history exists. Patterns are portfolio priors,
never facts about an individual Deal and never mutation evidence.

Activation is fail-closed behind `PORTFOLIO_PATTERN_ENABLED`,
`PORTFOLIO_REASONING_APPROVED`, and `PORTFOLIO_PATTERN_WRITES_APPROVED`. The global
business-listening write kill switch also blocks persistence. DocuSign/SOW work,
shared Slack/Teams ingestion, and Muthur remain deferred.
