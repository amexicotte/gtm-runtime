# GitHub Actions usage monitor

The daily `github-actions-usage-monitor.yml` workflow posts one Slack alert when
monthly Actions usage first crosses 75%, 90%, and 100% of the configured minute
allowance. Each message includes used minutes, percentage, days remaining in the
calendar month, current daily pace, and projected month-end usage.

## Production configuration

- `ACTIONS_MONTHLY_MINUTES_LIMIT` is a non-secret repository variable. It
  defaults conservatively to `2000` (GitHub Free). Set it to `3000` for GitHub
  Pro or the exact included allowance shown in GitHub billing.
- `SLACK_BOT_TOKEN` is the existing repository secret.
- `SLACK_USAGE_ALERT_CHANNEL` is an optional repository variable and defaults to
  `deal-alerts`.
- `SLACK_USAGE_ALERT_USER_ID` is an optional non-secret Slack user ID. When set,
  the bot sends the alert directly to that user instead of the channel.
- `GITHUB_BILLING_READ_TOKEN` is optional. For exact account-wide usage, store a
  fine-grained personal access token with only **Plan: read** as this repository
  secret. Without it, the workflow uses the built-in repository token and a
  conservative run-duration estimate. That fallback is currently valid because
  this account owns one repository and every workflow has one job.

The workflow stores only a compact, secret-free artifact after sending an alert.
The artifact prevents repeat alerts for the same threshold and month. A manual
dispatch with `send_test_alert=true` verifies Slack delivery without writing a
threshold marker.

## Native GitHub backup alerts

GitHub's included-usage alerts cover 90% and 100%, but not 75%, and do not add a
days-remaining projection. Enable them as a backup under:

`Settings → Billing and licensing → Budgets and alerts → Included usage alerts`

GitHub budget alerts can notify at 75%, 90%, and 100%, but those percentages are
of a dollar-denominated metered-usage budget rather than the included Actions
minute allowance, so they do not replace this monitor.
