# Starter

A minimal chat app for one ZooWork Platform Agent. See README.md.

- `agent/agent.md` defines the Agent (frontmatter keys are listed in README.md). Apply changes
  with `npm run setup`.
- `app.config.ts` holds page text and brand. It is sent to the browser: no keys or private data.
- `lib/zoowork/` is server-only and holds the key; `lib/chat/` runs in the browser and only calls
  this app's `/api` routes.
- Keys live only in `.env`. Never put them in code, chat, logs or Git.
- Keep `.local/` after any failed or uncertain setup or cleanup.
- `npm run check` must pass offline. Live requests use credits and need the owner's approval.
