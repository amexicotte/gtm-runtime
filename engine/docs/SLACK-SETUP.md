# Slack setup (for the GitHub Actions runtime)

The runtime reads and writes Slack from GitHub Actions, so we need our own Slack
app with a **bot token** — the Notion↔Slack connector (post-only, Notion-owned)
can't read messages or thread replies. You have Slack admin; all of this is
yours to do. **Do not paste the token to Claude** — it goes straight into GitHub
Actions secrets.

We **poll** Slack on the 15-min cron, so you do **not** need Event Subscriptions,
Socket Mode, or a public request URL. That keeps the app simple.

## 1. Create the app
1. Go to **https://api.slack.com/apps** → **Create New App** → **From scratch**.
2. Name it **`Harbor GTM`** (or similar) and pick the Harbor/Unity workspace.

## 2. Add bot token scopes
Left sidebar → **OAuth & Permissions** → **Scopes → Bot Token Scopes** → add:
- `channels:history` — read messages in the public channels
- `channels:read` — resolve channel names → IDs
- `chat:write` — post messages (gate prompts, task nudges)
- `users:read` and `users:read.email` — map a task owner's email → Slack user, to DM them
- *(only if you make the channels private:* `groups:history`, `groups:read`)

That's the full set. Nothing else needed.

## 3. Install + grab the token
1. Top of **OAuth & Permissions** → **Install to Workspace** → Allow.
2. Copy the **Bot User OAuth Token** (starts `xoxb-…`).
3. Put it in **GitHub → repo → Settings → Secrets and variables → Actions** as
   **`SLACK_BOT_TOKEN`**. (Not to Claude, not in the repo.)

## 4. Create the channels
- Create public channels **`#call-notes`** and **`#deal-alerts`**.
- `#call-notes` — where you drop your post-call notes.
- `#deal-alerts` — where the matcher asks "which company is this?" and you reply
  in-thread.

## 5. Invite the bot
In **each** channel, type: `/invite @Harbor GTM`
(The bot must be a member to read history and post.)

## Manual notes for an unrecorded call

Create a new root post in `#call-notes` beginning with `manual:`. The rest can
be unstructured notes. For example:

```text
manual: Acme check-in
Jane confirmed budget. Send the scope Friday; she will send the pipeline export.
```

The explicit prefix prevents ordinary channel conversation from becoming CRM
data. No new Slack scopes, Events API, slash command, or Socket Mode are needed;
the existing poller reads it with `channels:history`. Replies remain reserved
for the bot's acknowledgement or a requested identity clarification.

## 6. Tell Claude the non-secret bits
Send me (these are safe to share — they are not credentials):
- the two channel names exactly as created (I resolve IDs in code), and
- the app/bot display name (for the `/invite` and for nice log messages).

## What you do NOT need (obsolete under this runtime)
- **User groups to @mention an agent** — the bot DMs task owners directly by
  email, so no agent user-group is required.
- **Event Subscriptions / Socket Mode** — we poll on the cron.

## The other two secrets (same GitHub Actions place, same rule)
Set these alongside `SLACK_BOT_TOKEN` when we wire the runtime:
- **`NOTION_API_TOKEN`** — a Notion **internal integration** token. Create at
  notion.so/my-integrations, then share the Harbor databases with it (as teamspace
  owner you can). Check you can create one — if it's also admin-gated, tell me.
- **`ANTHROPIC_API_KEY`** — for the one reasoning call per meeting.
