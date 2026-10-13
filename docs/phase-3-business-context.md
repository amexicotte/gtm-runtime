# Phase 3 — bounded business context

PRD v2 governs this phase. The implementation adds four read surfaces without
giving a model general provider clients or mutation credentials:

- exact monday item context, recent Updates, and related open Activities;
- query-matched existing Notion pages across the authorized workspace;
- query-matched Drive metadata and bounded Google Doc/text/PDF/DOCX exports
  across the existing account's visible Drive;
- weekly, content-hashed public positioning from `harboradvisors.example`.

Every call has a hard input/result/call cap and emits a content-free audit record
containing the tool name, timestamps, counts, and request/result hashes. Notion
and Drive expose no mutation method. Every rclone run verifies `drive.readonly`,
rejects invalid paths, and applies provider/local metadata, result, file-size,
excerpt, and tool-call caps. Website redirects may remain only on Harbor's exact
HTTPS hosts.

## Activation boundary

The owner authorized read-only access across the existing Notion workspace and
existing account's visible Drive; no dedicated user, workspace, page, folder, or
paid seat is required. `BUSINESS_CONTEXT_ENABLED` remains false until the
production `NOTION_API_TOKEN`, the `harbor-drive` remote using exactly
`drive.readonly`, compact operating-context assembly, and bounded cross-provider
search all pass `python -m runtime.context_main audit`. Existing Notion pages
remain human-managed; the runtime never seeds or edits them. Weekly website
refreshes supplement internal context but never overwrite it. DocuSign ingestion,
Drive writes, Slack or Teams ingestion, and Muthur remain out of Phase 3; Phase 5
portfolio retrieval is separately advisory.
