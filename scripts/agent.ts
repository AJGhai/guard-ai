// npm run setup | status | cleanup | models
//
// setup    create the Agent from agent/agent.md (or update it after the file
//          changed), then start it
// status   show the recorded Agent and whether it matches agent/agent.md
// cleanup  delete this app's recorded conversations, then stop and delete
//          its Agent (stop npm run dev first)
// models   list the models this key can use, for agent/agent.md's model:
//
// Output never includes the key. On failure the command prints a code from
// the README troubleshooting table and keeps .local/ for the next attempt.

import { loadAgentDefinition } from "../lib/zoowork/agent-definition";
import { createZooworkClient } from "@zoowork-ai/sdk";
import { readApiKey } from "../lib/zoowork/config";
import { errorCode, StarterError } from "../lib/zoowork/errors";
import { agentStatus, applyAgent, cleanupAgent } from "../lib/zoowork/lifecycle";
import { withTransientRetry } from "../lib/zoowork/retry";
import { fileSessionStore, sessionsPath } from "../lib/zoowork/sessions";
import { agentStatePath, loadAgentState, runningWebPid, withStateLock } from "../lib/zoowork/state";

const NEXT_STEP: Record<string, string> = {
  platform_project_key_required: "Copy .env.example to .env and set ZOOWORK_API_KEY to your Platform Project key (zwp_live_).",
  run_setup_first: "Run npm run setup first.",
  state_scope_mismatch: "The conversations in .local/ belong to another Agent. Keep .local/ and check it was not copied from another checkout.",
  state_locked_review_before_retry: "Another setup or cleanup is running. If none is, delete .local/agent.json.lock and retry.",
  agent_label_mismatch: "The recorded Agent is not this app's. Check that .local/ and the key belong together.",
  http_401: "Check that ZOOWORK_API_KEY was copied correctly.",
  http_402: "The Organization needs billing set up and credits. Check Billing in Platform Console.",
  http_403: "Check the key's Project and permissions.",
  http_404: "The recorded resource was not found. Check the key's Project before deleting .local/.",
  request_failed: "Could not reach ZooWork Platform (network failure). Keep .local/ as it is, check your connection and run the command again.",
  timeout_or_cancelled: "ZooWork Platform did not answer in time. Keep .local/ as it is, check your connection and run the command again.",
  web_server_running: "Stop npm run dev, then run cleanup again.",
  cannot_remove_model: "Put model back in agent/agent.md, or run npm run cleanup then npm run setup to recreate the Agent.",
  cannot_remove_max_tokens: "Put max_tokens back in agent/agent.md, or run npm run cleanup then npm run setup to recreate the Agent.",
  cannot_remove_timezone: "Put timezone back in agent/agent.md, or run npm run cleanup then npm run setup to recreate the Agent.",
  skills_removed_set_empty_list: "Write skills: [] in agent/agent.md to remove all Skills, then run setup.",
  agent_update_not_confirmed: "The Agent does not show the update. Run npm run status, check model, Skill and tool names, and run setup again.",
  cleanup_incomplete_run_cleanup: "A cleanup stopped partway. Run npm run cleanup to finish it, then npm run setup.",
  session_creation_uncertain_retry_cleanup: "A conversation create could not be confirmed. Keep .local/, check the connection and key, and run cleanup again.",
};

const list = (fields: string[]) => fields.map((f) => `"${f}"`).join(", ");

async function main(command: string | undefined) {
  if (!["setup", "status", "cleanup", "models"].includes(command ?? "")) {
    throw new StarterError("expected_setup_status_cleanup_or_models");
  }
  // Reads and keyed creates are retried once on a network failure; updates are not.
  const client = withTransientRetry(createZooworkClient({ apiKey: readApiKey() }));

  if (command === "models") {
    const models = await client.listModels();
    for (const m of models) {
      if (m.selectable === false) continue;
      const levels = m.thinking?.levels?.length ? `  thinking: ${m.thinking.levels.join(", ")}` : "";
      console.log(`${m.model}${m.display_name ? `  (${m.display_name})` : ""}${levels}`);
    }
    return;
  }

  const path = agentStatePath();
  if (command === "status") {
    const state = await loadAgentState(path);
    if (!state) throw new StarterError("run_setup_first");
    const report = await agentStatus(client, state, loadAgentDefinition().resource);
    console.log(`Agent:      ${report.agentId}`);
    console.log(`State:      ${report.desiredState}`);
    console.log(`Definition: ${report.definitionApplied ? "matches agent/agent.md" : "differs from agent/agent.md; run npm run setup"}`);
    if (report.unsupportedRemoval) console.log(`Note:       ${NEXT_STEP[report.unsupportedRemoval]}`);
    if (report.undeclaredPolicy.length) {
      console.log(`Note:       The Agent's tool policy has ${list(report.undeclaredPolicy)}, which agent/agent.md does not declare.`);
      console.log("            npm run setup will remove it. To keep an approval, add it under permissions: in agent/agent.md.");
    }
    return;
  }

  await withStateLock(path, async () => {
    const state = await loadAgentState(path);
    if (command === "setup") {
      const definition = loadAgentDefinition();
      const result = await applyAgent(client, path, state, definition.resource, {
        onReplacePolicy: (fields) => {
          console.warn(`WARNING: The Agent's tool policy has ${list(fields)}, which agent/agent.md does not declare.`);
          console.warn("         This update replaces the whole tool policy and removes it. To keep an approval,");
          console.warn("         add it under permissions: in agent/agent.md and run npm run setup again.");
        },
      });
      const verb = { created: "created", updated: "updated from agent/agent.md", unchanged: "already matches agent/agent.md" }[result.action];
      console.log(`Agent ${verb}: ${result.agentId}`);
      console.log("Agent ready. Run npm run dev and open the URL it prints (http://localhost:3000 unless that port is in use).");
      return;
    }
    if (!state) throw new StarterError("run_setup_first");
    const pid = await runningWebPid();
    if (pid) throw new StarterError("web_server_running");
    const sessions = fileSessionStore(sessionsPath(), { instance: state.instance, agentId: state.agentId ?? "" });
    const { sessionsDeleted } = await cleanupAgent(client, path, state, sessions);
    console.log(`Deleted ${sessionsDeleted} conversation(s), then stopped and deleted Agent ${state.agentId}.`);
    console.log("Run npm run setup to create a new Agent.");
  });
}

main(process.argv[2]).catch((error: unknown) => {
  const code = errorCode(error);
  const head = code.split(":")[0]!;
  console.error(`FAIL: ${code}`);
  if (error instanceof StarterError && head === "invalid_agent_definition") console.error("Fix agent/agent.md and run the command again.");
  else if (NEXT_STEP[head]) console.error(NEXT_STEP[head]);
  else console.error("Check .env and keep .local/ as it is while investigating.");
  process.exitCode = 1;
});
