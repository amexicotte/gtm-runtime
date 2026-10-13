# Project instructions

## Integration policy

- Prefer a repository CLI, then a direct provider API, then an MCP/installed
  connector, in that order.
- Never use browser automation, UI scraping, Computer Use, or simulated user
  activity to access an integration.
- Never ask Harbor to create a dedicated human/bot login or paid user seat for an
  automation. Prefer an existing-account API token, an administrator-installed
  app grant, or a native connector. A one-time consent/install action performed
  by an authorized administrator is acceptable.
- Keep credentials in approved local or production secret storage. Never commit
  or paste secrets into prompts, logs, documentation, or source control.
- Start with read-only access and the smallest resource scope that can complete
  discovery. Add write access only for an approved implementation requirement.

## Business-listening implementation

- Read `docs/harbor-business-listening-agent-prd-v2.md` and
  `docs/business-listening-implementation-runbook.md` before planning or changing
  the business-listening/CRM system. PRD v2 is the governing specification.
- The user only needs to start a task in this same local project/directory and
  name the PRD phase. Codex owns branch/worktree selection, dirty-tree checks,
  synchronization with the accepted baseline, and other Git mechanics. Do not
  ask the user to manage or understand those implementation details.
- Never ask the user to download or upload the PRD between tasks. Read the
  checked-in repository copy.
- A phase task owns only its named phase. Phase 0 produces
  `docs/system-inventory-and-ownership.md`; later phases consume that artifact
  instead of repeating the inventory.
- Preserve unrelated and uncommitted user work.
