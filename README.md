# ZooWork Agent Starter

A minimal, general-purpose chat app for an Agent you define. You describe the Agent in one
file, `npm run setup` creates it on ZooWork Platform, and `npm run dev` gives you a chat page
to test it: multiple conversations, live tool activity, stop, tool approvals, and history
that survives reloads and dropped connections. Then use the checklist below to bring it into
your own product.

It is the companion workspace for the [Publish your Agent guide](https://zoowork.ai/guides/publish-agent).
Use it with your coding agent (Codex, Cursor or another): point it at this
directory and ask it to change `agent_starter/agent.md` and `app.config.ts` for your use case.

The other directories in this repository are capability demos built around a business
scenario (Custom Tools, Remote MCP, RAG, research). This starter has no business logic; it is
the starting point for your own Agent.

It is a minimal integration. It has **no end-user sign-in, no per-user limits and no public
deployment setup**, and it only answers on `localhost`. See [Before you go public](#before-you-go-public).

## Requirements

- Node.js **22.20 or later** (`engines` in `package.json`) and npm. The repository pins
  24.22.1 in `.node-version`; `fnm use` or `nvm use` (or fnm's automatic switching) picks it
  up. Check with `node --version` before `npm ci`.
- A ZooWork Platform **Project key** (`zwp_live_…`) and Organization credits. Step 1 shows
  how to get them.

## 1. Get a Project key and credits

1. Sign in to [Platform Console](https://platform.zoowork.ai) and select a Project. The
   **Default Project** is fine.
2. Open **API Keys**, create a key and copy it. The Console shows it once. It starts with
   `zwp_live_`. Work organization keys (`zct_…`) are not accepted.
3. Open **Billing** and make sure the Organization has credits. Creating the Agent needs
   billing to be set up, and every reply uses credits. All Projects in an Organization share
   the same balance.

## 2. Configure the server

```sh
git clone https://github.com/SerendipityOneInc/zoowork-platform-quickstarts.git
cd zoowork-platform-quickstarts/starter
npm ci
cp .env.example .env
```

Edit `.env` and set your key:

```dotenv
ZOOWORK_API_KEY=zwp_live_your_project_key
```

The key stays in the server process. Never paste it into a chat, browser code, a screenshot,
a log or Git; `.env` is ignored by Git.

## 3. Define your Agent

The Agent is one file: [`agent_starter/agent.md`](agent_starter/agent.md). The YAML frontmatter is the
configuration; everything below it is the Agent's instructions.

```markdown
---
name: Starter Assistant
tools:
  - web_search
  - web_fetch
# permissions:
#   web_fetch: always_ask
global_skills: false
---

You are a helpful assistant inside a web chat. …
```

| Key | Required | Meaning |
| --- | --- | --- |
| `name` | yes | The Agent's name in Platform. |
| `tools` | yes | Built-in tools to enable, by exact name, such as `web_search` and `exec`. |
| `permissions` | no | Tools that ask before each call: a map of tool name to `always_ask` or `always_allow`. Each name must be in `tools`. See [Tool approvals](#tool-approvals). |
| `model` | no | Model id. Omit to use the Platform default at creation. `npm run models` lists the models your key can use. |
| `thinking` | no | `off`, `low`, `medium`, `high` or `max`. Needs `model`. |
| `max_tokens` | no | Output token cap per model request. Needs `model`. |
| `global_skills` | no | Automatic global Skills. Default `false`, so the Agent only has what you list. |
| `skills` | no | Skills to attach, by `name` or `skill_id`, with an optional `version`. |
| `timezone` | no | Named IANA timezone, such as `Asia/Shanghai`. |

Unknown keys are rejected, so a typo fails setup instead of being ignored.

### Tool approvals

To make a tool ask before it runs, list it under `permissions` with `always_ask`. For example,
to approve every page the Agent reads:

```yaml
tools:
  - web_search
  - web_fetch
permissions:
  web_fetch: always_ask
```

Run `npm run setup`. From the next message on, each `web_fetch` call pauses: its card in the
chat opens with **Allow** and **Deny**, and the header says "Waiting for your approval". Allow
runs the call; Deny skips it, the card reads "Denied: web_fetch", and the Agent continues
without it. A call nobody decides on is denied after about ten minutes. Tools without an entry
run without asking (`always_allow` says so explicitly).

Then create and start the Agent:

```sh
npm run setup
```

Setup prints `Agent created: <id>`. You never copy an Agent ID anywhere: setup records it in
`.local/agent.json`, and the app reads it from there.

**To change the Agent later**, edit `agent_starter/agent.md` and run `npm run setup` again. It updates
the same Agent and confirms the change by reading it back; a change the Platform did not apply
fails as `agent_update_not_confirmed` instead of being reported as done. Existing conversations
use the new configuration from their next message.

Setup writes the Agent's whole tool policy from `tools` and `permissions`. A tool approval
added elsewhere (in Console or with the SDK) is therefore removed by the next setup; setup prints
a `WARNING` naming each such field before it writes, and `npm run status` lists them. Keep
approvals in `agent_starter/agent.md`.

Removing a line works for `thinking` (setup clears it) and `permissions` (setup removes them). The Platform has no documented way to
clear `model`, `max_tokens` or `timezone`, so setup refuses to remove them: keep a value, or run
`npm run cleanup` and then `npm run setup` to recreate the Agent. To remove all Skills, write
`skills: []` instead of deleting the line.

## 4. Chat with it

```sh
npm run dev
# or, if port 3000 is in use:
npm run dev -- --port 3001
```

Open the URL `npm run dev` prints (**http://localhost:3000** by default) and send a message.
You will see the Agent think, use its tools, and reply. Each conversation in the sidebar is
one ZooWork Session.

- **Reload or lose the connection**: the page reconnects and continues from where it stopped.
  The Agent keeps working while the page is closed; its reply is there when you come back.
- **Stop** (the square button) interrupts the run on the Platform. Closing the page does not.
- **Allow / Deny** appears on the card of a tool call that needs approval. Set which tools ask
  in `agent_starter/agent.md` ([Tool approvals](#tool-approvals)).
- **Switch conversations** while one is working: the sidebar keeps a spinner on it until its
  reply is done (for up to three background conversations at a time).
- **Archive** moves a conversation to the sidebar's **Archived** group. It stays readable but
  accepts no new messages, and cannot be unarchived. **Delete** deletes the Session on the
  Platform and removes it from the list. Both ask for confirmation first.

The header shows whether the Agent is ready, and while it is, what the open conversation is
doing ("Working…", "Waiting for your approval").

Replies arrive as whole messages, with tool activity shown live. The Platform API does not
currently stream replies token by token.

If the page says the Agent is out of date, run `npm run setup`. `npm run status` shows the
Agent's ID, state, whether it matches the file, and any tool policy fields setup would remove.

## 5. Make it yours

Most apps only need two files: `agent_starter/agent.md` for what the Agent does, and `app.config.ts`
for what the page says and looks like. Changing those two turns the starter into a different
app; the code stays the same.

| Change | Where |
| --- | --- |
| What the Agent does | `agent_starter/agent.md` (then `npm run setup`) |
| Product name, description, greeting, placeholder, starter prompts, brand color (buttons and links), logo, footnote | `app.config.ts` (no code changes; the fields are described in `lib/app-config.ts`) |
| A custom card for one of your tools | `components/tool-uis/` (register it in `index.tsx`) |
| Layout and components | `components/chat-screen.tsx`, `components/` |
| Colours, radii and dark mode | `app/globals.css` |

`app.config.ts` is sent to the browser. Never put keys or private data in it. For a logo, put
the image in `public/` (create the folder) and set `icon: "/logo.svg"`; it becomes the sidebar
mark and the browser tab icon. Without it, both show the name's first letter on the brand color.

## Before you go public

This starter is a local, single-user development app. Before anyone else can reach it, add
these. Every visitor's message runs your Agent and **uses your Organization's prepaid credits**.

1. **End-user authentication.** The Project key authorizes your server, not your users. Sign
   users in with your own auth.
2. **Session ownership.** Store which user owns each Session in your database and check it on
   every route that takes a Session ID. The Platform cannot list Sessions by end user.
   `ownedSession()` in `lib/zoowork/runtime.ts` is where that check goes; replace the local
   `.local/sessions.json` index with a table keyed by user.
3. **Per-user limits and abuse protection.** Daily message or credit limits per user, rate
   limits, and bot protection on sign-up. Without them, one visitor can spend your balance.
4. **Remove the local-only guard deliberately.** `guard()` in `lib/zoowork/http.ts` refuses any
   host other than `localhost`. Remove it only after 1 to 3 are in place.
5. **Review what reaches the browser.** The stream route forwards event payloads, including tool
   arguments and result previews. Filter fields your users should not see.

For the backend pattern, see step 9, “Wire up your own front end”, in the
[ZooWork coding Skill's deployment reference](https://github.com/SerendipityOneInc/zoowork-sdk-skills/blob/main/skills/zoowork-managed-agents/references/deploy-your-agent.md).
The starter's [How it works](#how-it-works) section shows which files to reuse.

## Clean up

```sh
# Stop npm run dev first.
npm run cleanup
```

Cleanup deletes the conversations this app recorded, then stops and deletes its Agent. It only
touches IDs in `.local/`; it never lists your Project to choose what to delete. If anything is
uncertain or fails, it keeps `.local/` so you can run it again. After success the old records
are renamed to `*.retired-<instance>.json`, and the next `npm run setup` creates a new Agent.

## Troubleshooting

Errors print a stable code and never the key or a provider response body.

| Code | What to do |
| --- | --- |
| `platform_project_key_required` | Copy `.env.example` to `.env` and set `ZOOWORK_API_KEY` to a `zwp_live_` Project key. Restart `npm run dev` after editing `.env`. |
| `run_setup_first` | Run `npm run setup`, then reload the page. |
| `agent_changed_restart_dev` | `.local/agent.json` now records a different Agent than the running server uses. Restart `npm run dev`. |
| `invalid_agent_definition: …` | Fix the named problem in `agent_starter/agent.md` and run setup again. |
| `cannot_remove_model` / `cannot_remove_max_tokens` / `cannot_remove_timezone` | Put the line back in `agent_starter/agent.md`, or run `npm run cleanup` then `npm run setup` to recreate the Agent without it. |
| `skills_removed_set_empty_list` | Write `skills: []` to remove all Skills, then run setup. |
| `agent_update_not_confirmed` | The Platform accepted the update but the Agent does not show it. Run `npm run status`, check the changed values (model id, Skill names, tool names), and run setup again. |
| `cleanup_incomplete_run_cleanup` | A cleanup stopped partway. Run `npm run cleanup` again to finish it, then `npm run setup`. |
| `session_creation_uncertain_retry_cleanup` | A conversation was being created when its response was lost, and it could not be confirmed now. Keep `.local/`, check the connection and key, and run `npm run cleanup` again. |
| `request_failed` / `timeout_or_cancelled` | The app server could not reach ZooWork Platform (a network failure, or no answer in time). Calls that are safe to repeat were already retried once. Check your internet connection and try again. If `npm run status` fails the same way, the problem is the connection to the Platform, not the app. |
| `http_401` | The key was rejected. Check it was copied completely. |
| `http_402` | Set up billing or add credits for the Organization in Platform Console. |
| `http_403` | Check the key's Project and permissions. |
| `http_400` during setup | Check billing, the `model` value (`npm run models`), skills and tool names. Keep `.local/`. |
| `state_scope_mismatch` / recorded `http_404` | `.local/` belongs to another key or Project. Restore the original `.env`. Do not delete `.local/` to force a new Agent; run cleanup with the original settings instead. |
| `agent_label_mismatch` | The recorded Agent is not this app's. Check that `.local/` and the key belong together. |
| `state_locked_review_before_retry` | Another setup or cleanup is running. If none is, delete `.local/agent.json.lock`. |
| `web_server_running` | Stop `npm run dev` before `npm run cleanup`. |
| `local_only_add_authentication_first` | You opened the app through a host other than `localhost`. See [Before you go public](#before-you-go-public). |
| "The message was accepted, but the Agent has not started" | Run `npm run status`. If the Agent is stopped, run `npm run setup`. |
| `EADDRINUSE` | Another process uses port 3000. Stop it or run `npm run dev -- --port 3001`. |

## How it works

```text
browser (assistant-ui)            this app's server (Next.js)            ZooWork Platform
──────────────────────            ───────────────────────────            ────────────────
sidebar  ── GET/POST /api/sessions ──▶ local Session index ──createSession──▶ Session
thread   ── GET  …/[id]/stream (SSE) ──▶ relay ◀────── streamEvents(cursor) ── event log
composer ── POST …/[id]/messages ─────▶ postEvents(user.message, idempotency key)
stop     ── POST …/[id]/interrupt ────▶ postEvents(user.interrupt)
allow    ── POST …/[id]/approvals/… ──▶ listApprovals check, then resolveApproval
archive  ── POST …/[id]/archive ──────▶ interrupt if running, then archiveSession
status   ── GET  /api/status ─────────▶ getAgent (definition and state)
```

- **One stream for history and live updates.** Opened without a cursor, the Platform stream
  replays the Session from its first event, then continues live. The relay forwards each event
  with its Platform cursor as the SSE event id. When the connection drops, the browser
  reconnects with that id and the stream resumes right after it, so nothing is lost or shown
  twice. `lib/zoowork/relay.ts`
- **One fold for every view.** `lib/chat/reducer.ts` is a pure function from Session events to
  assistant-ui messages, used for both the replay and live events. It skips any event at or
  below the last sequence number it applied, so an overlapping replay changes nothing.
  `lib/chat/session-controller.ts` owns the stream, the optimistic bubble for a sent message and
  the stop/approval actions. `lib/chat/runtime-provider.tsx` connects both to assistant-ui, with
  the sidebar backed by the Session index (`lib/chat/session-index.ts`). The open conversation
  keeps a stream; one you switch away from keeps it only while its run is in flight
  (`lib/chat/controller-pool.ts`). Switching back resumes from the last cursor.
- **Safe retries.** Each sent message carries an idempotency key, so a retried request is
  delivered once and never starts a second paid run. Calls that are safe to repeat (reads,
  keyed creates and events, archive) are retried once after a network failure
  (`lib/zoowork/retry.ts`); Agent updates and approval decisions are sent once. Setup saves the exact create request and
  key before creating the Agent, and the app does the same before creating each conversation's
  Session, so a lost response is resolved by repeating the same request instead of creating a
  duplicate. Listing conversations and cleanup both finish such pending creates.
- **Ownership.** Each route checks that the Session is one this app created
  (`ownedSession()`), and approvals are checked against the Session before they are resolved.

| Path | Responsibility |
| --- | --- |
| `agent_starter/agent.md` | The Agent definition |
| `app.config.ts` | Page text and brand |
| `app/api/` | Routes: sessions, stream, messages, interrupt, archive, approvals, status |
| `lib/zoowork/` | Server: configuration, client, setup/cleanup, Session index, relay (`relay.ts`), route operations (`chat.ts`) |
| `lib/chat/` | Browser: API client, event reducer, session controller, Session index, assistant-ui runtime |
| `components/` | Chat screen, sidebar, status bar, tool cards (`tool-uis/`), assistant-ui and shadcn/ui components |
| `scripts/` | `setup` / `status` / `cleanup` / `models` |
| `test/` | Offline tests and the in-memory fake Platform they use |

## Checks

```sh
npm ci
npm run check
```

`check` runs the TypeScript check, the offline tests and a production build. It needs no key
and makes no network request to ZooWork. The tests check that `agent_starter/agent.md` has a valid
structure, not its example values, so `check` stays green after you customize it. They cover
the agent definition parser, the
event reducer (tools, approvals, stop, failures, replay overlap), the route operations
(ownership, idempotent sends, approval checks, archive), setup and cleanup recovery, and stream
relay and resume against an in-memory fake of the Session API in `test/`.

`npm ci` reports no install-script warning: `allowScripts` in `package.json` records that the
install scripts of `esbuild` and `fsevents` (pulled in by `tsx`) are not run, which they do not
need. Older npm versions ignore the field and run them as before.

`npm run build` and `npm start` build and serve the app locally. They are not a deployment
setup; see [Before you go public](#before-you-go-public).

## Sources and licenses

The chat UI is built with [assistant-ui](https://www.assistant-ui.com/) and
[shadcn/ui](https://ui.shadcn.com/). Their generated components under `components/` (and
`lib/utils.ts`, `hooks/`) are MIT-licensed by AgentbaseAI Inc. and shadcn; see
[`components/NOTICE.md`](components/NOTICE.md) for what was generated and the license texts.

## MarginGuard agent and API

MarginGuard is a Python ZooWork agent that reprices SKUs after a tariff change. It lives in
`agent/run.py`, with its tools in `tools/` and sample data in `data/` and `market/`. Every price
change needs a human approval, and only approved changes are written to
`data/store_prices.json`, the single source of truth for current prices.

SKUs that MarginGuard marks REVIEW go to a second agent, SupplierNegotiator, through the
`request_supplier_negotiation` tool. It calculates the unit cost that restores a 60% margin
(`tools/supplier.py`), drafts a cost-reduction email to each supplier in `data/suppliers.csv`, and
asks a human to approve each draft. Approved drafts are written to `data/outbox/<sku>.md`; no email
is ever sent.

### Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# .env must contain ZOOWORK_API_KEY (see .env.example)
```

### Terminal mode

```bash
.venv/bin/python agent/run.py --reset "Duty goes from 12% to 37% on all products. What should we do?"
```

`--reset` copies `data/store_prices.original.json` over `data/store_prices.json` and clears
`data/outbox/` first. The run prints its event trace and asks y/n in the terminal for each proposed
price change, then for each supplier email draft.

### API mode

```bash
.venv/bin/uvicorn api.server:app --port 8000
```

One run at a time. CORS allows the UI dev server at `http://localhost:3000` and
`http://127.0.0.1:3000`; set `MARGINGUARD_CORS_ORIGINS` (comma-separated) to change that. Errors
use the starter app's shape, `{"error": "<code>"}`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/run` | Start a run. Body `{"request": "...", "reset": false}`. `202`; `409 run_in_progress` if one is running. |
| GET | `/events?since=0` | Event trace from index `since`; `next` is the index to poll from next. |
| GET | `/pending` | The price changes or supplier email drafts waiting for a decision, if any. |
| POST | `/approve` | Decide every pending item. Body `{"decisions": {"<sku>": true\|false}}`. |
| GET | `/plan` | Per-SKU decisions, comparables, baseline and scenario totals (fills in as the run goes). |
| GET | `/report` | Final report, applied and rejected changes, exposure, estimated recovery. `409 report_not_ready` until the run ends. |
| GET | `/prices` | Current `data/store_prices.json`. |
| GET | `/outbox` | Approved supplier email drafts in `data/outbox/`. None are sent. |

Run status moves `running` → `awaiting_approval` → `running` → `succeeded` or `failed`.

**POST /run**

```json
{ "request": "Duty goes from 12% to 37% on all products. What should we do?", "reset": true }
```
```json
{ "status": "running", "request": "Duty goes from 12% to 37% on all products. What should we do?", "reset": true }
```

**GET /events?since=48**

```json
{
  "status": "succeeded",
  "error": null,
  "session_id": "96bf6114f85f46cabc9aa2ae81a6e349",
  "events": [
    { "seq": 48, "t": 28.8, "label": "price.skipped", "detail": "DJ-02 -> 81 (rejected by reviewer)" },
    { "seq": 49, "t": 28.8, "label": "local.result", "detail": "apply_price_changes: applied 4, rejected 1, ..." }
  ],
  "next": 54
}
```

**GET /pending**

```json
{
  "status": "awaiting_approval",
  "pending": true,
  "kind": "price_changes",
  "emails": [],
  "changes": [
    { "sku": "LP-02", "old_price": 48, "new_price": 53, "reason": "Below Pants median $64; capped at +10% to $53" },
    { "sku": "DJ-02", "old_price": 74, "new_price": 81, "reason": "Below Jackets median $82; capped at +10% to $81" }
  ]
}
```

During the supplier step, `kind` is `"supplier_emails"`, `changes` is empty and `emails` holds
the drafts (addresses come from `data/suppliers.csv`). Approve them with the same `POST /approve`.

```json
{
  "status": "awaiting_approval",
  "pending": true,
  "kind": "supplier_emails",
  "changes": [],
  "emails": [
    { "sku": "CD-03", "supplier": "Dhaka Garments Ltd", "to": "sales@dhaka-garments.example.com",
      "subject": "CD-03 unit-cost request — 12-month volume agreement", "body": "Dear Dhaka Garments Ltd, ..." }
  ]
}
```

**POST /approve** must include every pending SKU (`400 decisions_mismatch` lists `missing` and
`unknown` SKUs; `409 no_pending_approval` when nothing is waiting). Answer within about 9.5
minutes, or every change is rejected (ZooWork denies an unanswered approval after 10 minutes).

```json
{ "decisions": { "LP-01": true, "LP-02": true, "CD-01": true, "CD-02": true, "DJ-02": false } }
```
```json
{ "approved": ["CD-01", "CD-02", "LP-01", "LP-02"], "rejected": ["DJ-02"] }
```

**GET /plan**

```json
{
  "status": "awaiting_approval",
  "decisions": [
    { "sku": "LP-02", "name": "Linen Drawstring Pant", "category": "Pants", "action": "REPRICE",
      "current_price": 48, "new_price": 53, "median": 64, "tariff_margin": 0.6719,
      "reason": "Below Pants median $64; capped at +10% to $53" },
    { "sku": "CD-03", "name": "Wrap Shirt Dress", "category": "Dresses", "action": "REVIEW",
      "current_price": 64, "new_price": 64, "median": 72, "tariff_margin": 0.5505,
      "reason": "Tariff margin 55.0% is below 56%; flag for sourcing review, do not reprice" }
  ],
  "reprice_prices": { "LP-01": 64, "LP-02": 53, "CD-01": 72, "CD-02": 48, "DJ-02": 81 },
  "comparables": {
    "women linen pants": { "n": 7, "low": 42, "median": 64, "high": 98, "data_source": "sample_fallback" }
  },
  "baseline_totals": { "exposure": 10317.5, "recovery": 0.0, "blended_margin": 0.6438 },
  "scenario_totals": { "exposure": 10317.5, "recovery": 7425.05, "blended_margin": 0.6624 },
  "scenario_input": { "duty_old": 0.12, "duty_new": 0.37, "volume_drop": 0.02, "prices": { "LP-01": 64 } }
}
```

**GET /report**

```json
{
  "status": "succeeded",
  "error": null,
  "request": "Duty goes from 12% to 37% on all products. What should we do?",
  "report": "## Recommended action\n...",
  "applied": [ { "sku": "LP-01", "old_price": 58, "new_price": 64 } ],
  "rejected": [ { "sku": "DJ-02", "new_price": 81.0, "status": "rejected by reviewer" } ],
  "exposure": 10317.5,
  "estimated_recovery": 6378.06
}
```

`estimated_recovery` covers only the approved changes. `negotiation` holds the supplier step:
`targets` (supplier, `current_cost`, `target_cost`, `reduction_pct` per REVIEW SKU), `written` and
`rejected` drafts, and `emails_sent: false`.

**GET /prices**

```json
{ "LP-01": 64, "LP-02": 53, "LP-03": 78, "CD-01": 72, "CD-02": 48, "CD-03": 64, "CD-04": 82, "DJ-01": 79, "DJ-02": 74, "DJ-03": 118 }
```

**GET /outbox**

```json
{
  "sent": false,
  "emails": [
    { "sku": "CD-03", "path": "data/outbox/CD-03.md",
      "content": "To: Dhaka Garments Ltd <sales@dhaka-garments.example.com>\nSubject: ...\nApproved: 2026-11-07 17:04 UTC (outbox only, not sent)\n\nDear Dhaka Garments Ltd, ..." }
  ]
}
```
