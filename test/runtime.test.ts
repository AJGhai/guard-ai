import assert from "node:assert/strict";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { type AgentRecord, type AgentResource } from "@zoowork-ai/sdk";
import { loadAgentDefinition } from "../lib/zoowork/agent-definition";
import { applyAgent, type LifecycleClient } from "../lib/zoowork/lifecycle";
import { createFakePlatform } from "./fake-platform";
import { createPlatformRuntime } from "../lib/zoowork/runtime";
import { agentStatePath, loadAgentState, newAgentState, saveAgentState } from "../lib/zoowork/state";

const agentMd = (thinking?: string) =>
  ["---", "name: Test", "model: m1", ...(thinking ? [`thinking: ${thinking}`] : []), "tools: [web_search]", "---", "", "Be helpful.", ""].join("\n");

/** One Agent on a fake Platform; updates that apply but lose their response can be switched on. */
function fakeAgents() {
  const declared = new Map<string, Record<string, unknown>>();
  let loseUpdateResponse = false;
  const record = (id: string) => ({ agent_id: id, declared: structuredClone(declared.get(id)), status: { desired_state: "running" } }) as AgentRecord;
  const client: LifecycleClient = {
    async createAgent(input) {
      declared.set("agt_1", structuredClone(input.resource) as unknown as Record<string, unknown>);
      return { agent_id: "agt_1" };
    },
    getAgent: async (id) => record(id),
    async updateAgent(id, sections) {
      const model = { ...(sections.model as Record<string, unknown>) };
      if (model.thinking === null) delete model.thinking;
      declared.set(id, { ...declared.get(id), ...structuredClone(sections), model });
      if (loseUpdateResponse) throw new TypeError("fetch failed");
      return record(id);
    },
    startAgent: async () => ({ warnings: [] }),
    stopAgent: async () => ({ warnings: [] }),
    deleteAgent: async () => {},
    createSession: async () => ({ session_id: "ses_1" }),
    deleteSession: async () => {},
  };
  return { client, declared, loseResponses: (v: boolean) => (loseUpdateResponse = v) };
}

async function project() {
  const cwd = await mkdtemp(join(tmpdir(), "starter-runtime-"));
  await mkdir(join(cwd, "agent"));
  const writeAgent = (thinking?: string) => writeFile(join(cwd, "agent", "agent.md"), agentMd(thinking));
  const definition = () => loadAgentDefinition(cwd).resource as AgentResource;
  return { cwd, writeAgent, definition };
}

test("the web status check reads the latest agent.json, including an update whose response was lost", async () => {
  const { cwd, writeAgent, definition } = await project();
  const platform = fakeAgents();
  const path = agentStatePath(cwd);
  await writeAgent();
  await applyAgent(platform.client, path, undefined, definition());

  // The dev server starts and caches its runtime.
  const runtime = await createPlatformRuntime({ ...createFakePlatform(), getAgent: platform.client.getAgent }, cwd);
  assert.equal((await runtime.checkAgent!()).definitionApplied, true);

  // In another terminal: add thinking, run setup; the update applies but its response is lost.
  await writeAgent("high");
  platform.loseResponses(true);
  await assert.rejects(applyAgent(platform.client, path, await loadAgentState(path), definition()));
  platform.loseResponses(false);
  assert.equal((platform.declared.get("agt_1")!.model as Record<string, unknown>).thinking, "high");

  // Revert agent.md. The running server must not report a match.
  await writeAgent();
  assert.equal((await runtime.checkAgent!()).definitionApplied, false);

  // After setup clears it, the same running server sees the match again.
  await applyAgent(platform.client, path, await loadAgentState(path), definition());
  assert.equal((await runtime.checkAgent!()).definitionApplied, true);
});

test("the web status check notices that agent.json now records a different Agent", async () => {
  const { cwd, writeAgent, definition } = await project();
  const platform = fakeAgents();
  const path = agentStatePath(cwd);
  await writeAgent();
  await applyAgent(platform.client, path, undefined, definition());
  const runtime = await createPlatformRuntime({ ...createFakePlatform(), getAgent: platform.client.getAgent }, cwd);
  await saveAgentState(path, { ...newAgentState(definition()), agentId: "agt_other" });
  await assert.rejects(runtime.checkAgent!(), /agent_changed_restart_dev/);
});
