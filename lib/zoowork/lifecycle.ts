import { rename } from "node:fs/promises";
import type { AgentRecord, AgentResource, ZooworkClient } from "@zoowork-ai/sdk";
import { isNotFound, StarterError } from "./errors";
import { desiredResource, labelsFor, newAgentState, saveAgentState, type AgentState } from "./state";
import { recoverPendingCreates } from "./session-create";
import type { SessionStore } from "./sessions";

export type LifecycleClient = Pick<
  ZooworkClient,
  "createAgent" | "getAgent" | "updateAgent" | "startAgent" | "stopAgent" | "deleteAgent" | "createSession" | "deleteSession"
>;

/**
 * Does `actual` declare everything in `desired`? Objects compare key by key,
 * arrays element by element. Fields the Platform adds (defaults, versions)
 * do not count as drift.
 */
export function declares(actual: unknown, desired: unknown): boolean {
  if (Array.isArray(desired)) {
    return Array.isArray(actual) && actual.length === desired.length && desired.every((v, i) => declares(actual[i], v));
  }
  if (desired && typeof desired === "object") {
    return (
      !!actual &&
      typeof actual === "object" &&
      Object.entries(desired).every(([k, v]) => declares((actual as Record<string, unknown>)[k], v))
    );
  }
  return actual === desired;
}

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

/** A policy section without empty values, so `permissions: {}` and no `permissions` compare equal. */
function compact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(compact);
  if (!isPlainObject(value)) return value;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    const c = compact(v);
    if (c === undefined || c === null || (isPlainObject(c) && !Object.keys(c).length)) continue;
    out[k] = c;
  }
  return out;
}

/**
 * Is the tool policy exactly the declared one? Unlike other sections, a
 * field the Platform has and agent/agent.md does not (an approval added in
 * Console or with the SDK) counts as drift: every setup writes the whole
 * policy and so replaces it. Key order does not matter.
 */
export function samePolicy(actual: unknown, desired: unknown): boolean {
  const a = compact(actual ?? {});
  const d = compact(desired ?? {});
  return declares(a, d) && declares(d, a);
}

/**
 * Tool policy fields on the Platform Agent that the definition does not
 * declare, such as `permissions.web_fetch` or `rules`. Writing the policy
 * removes them.
 */
export function undeclaredPolicyFields(actual: unknown, desired: unknown): string[] {
  const a = compact(actual ?? {});
  const d = compact(desired ?? {});
  if (!isPlainObject(a)) return [];
  const declared = isPlainObject(d) ? d : {};
  const fields: string[] = [];
  for (const [key, value] of Object.entries(a)) {
    if (key === "permissions" && isPlainObject(value)) {
      // Name each approval, the setting a reader is most likely to have added by hand.
      const kept = isPlainObject(declared.permissions) ? declared.permissions : {};
      for (const tool of Object.keys(value)) if (!(tool in kept)) fields.push(`permissions.${tool}`);
    } else if (!(key in declared)) fields.push(key);
  }
  return fields;
}

/** Read the recorded Agent and confirm it still carries this instance's labels. */
export async function ownedAgent(client: Pick<ZooworkClient, "getAgent">, state: AgentState): Promise<AgentRecord> {
  if (!state.agentId) throw new StarterError("agent_creation_uncertain_run_setup_again");
  const agent = await client.getAgent(state.agentId);
  const labels = agent.declared?.labels as Record<string, unknown> | undefined;
  const expected = labelsFor(state);
  if (labels?.starter_app !== expected.starter_app || labels?.starter_instance !== expected.starter_instance) {
    throw new StarterError("agent_label_mismatch");
  }
  return agent;
}

/**
 * Settings removed from agent/agent.md since the last setup.
 *
 * Writing only the remaining fields would leave a removed setting in place,
 * and the Platform documents a clearing value only for `model.thinking`
 * (`null`). Removing the thinking level is applied that way; removing the
 * model, max_tokens, timezone or the skills list is refused with a code that
 * says what to do instead.
 */
export function removedSettings(
  previous: AgentResource | undefined,
  desired: AgentResource,
): { clearThinking: boolean; unsupported?: string } {
  if (!previous) return { clearThinking: false };
  if (previous.model && !desired.model) return { clearThinking: false, unsupported: "cannot_remove_model" };
  if (previous.model?.max_tokens !== undefined && desired.model?.max_tokens === undefined) {
    return { clearThinking: false, unsupported: "cannot_remove_max_tokens" };
  }
  if (previous.userTimezone && !desired.userTimezone) return { clearThinking: false, unsupported: "cannot_remove_timezone" };
  if (previous.skills?.length && desired.skills === undefined) {
    return { clearThinking: false, unsupported: "skills_removed_set_empty_list" };
  }
  // A pending update's thinking: null is itself a clear, so it does not count as set.
  return { clearThinking: previous.model?.thinking != null && desired.model?.thinking == null };
}

/**
 * Settings removed relative to anything that may now be on the Agent: the
 * last confirmed definition, and an update whose response was lost (it may
 * have been applied even though setup never confirmed it).
 */
export function removedSinceRecorded(state: AgentState, desired: AgentResource): { clearThinking: boolean; unsupported?: string } {
  const recorded = [state.applied ?? state.createBody, state.pendingUpdate as AgentResource | undefined];
  let clearThinking = false;
  for (const previous of recorded) {
    const removed = removedSettings(previous, desired);
    if (removed.unsupported) return removed;
    clearThinking ||= removed.clearThinking;
  }
  return { clearThinking };
}

const thinkingOf = (declared: Record<string, unknown> | undefined) =>
  (declared?.model as { thinking?: unknown } | undefined)?.thinking ?? null;

/** Does the Agent's declaration match the definition, including settings that were removed? */
function matches(declared: Record<string, unknown> | undefined, desired: AgentResource, clearThinking: boolean): boolean {
  const { tool_policy: policy, ...rest } = desired;
  return (
    declares(declared, rest) &&
    samePolicy(declared?.tool_policy, policy) &&
    !(clearThinking && thinkingOf(declared) !== null)
  );
}

export type ApplyResult = { state: AgentState; agentId: string; action: "created" | "updated" | "unchanged" };

export type ApplyOptions = {
  /**
   * Called before an update that removes tool policy fields agent/agent.md
   * does not declare (for example an approval added in Console), with their names.
   */
  onReplacePolicy?: (fields: string[]) => void;
};

/**
 * Make the Platform Agent match the agent definition, then start it.
 *
 * - No recorded Agent: save the exact create body and idempotency key first,
 *   then create. If the response is lost, running setup again repeats the same
 *   request, which the Platform deduplicates.
 * - Recorded Agent with a different declaration: write the definition's
 *   sections with `updateAgent` and read the Agent back to confirm. A write
 *   the Platform did not apply fails as `agent_update_not_confirmed`.
 *   The tool policy is replaced as a whole, so `onReplacePolicy` is told
 *   first about any policy field the definition does not declare.
 *
 * Caller holds the state lock.
 */
export async function applyAgent(
  client: LifecycleClient,
  path: string,
  existing: AgentState | undefined,
  resource: AgentResource,
  { onReplacePolicy }: ApplyOptions = {},
): Promise<ApplyResult> {
  const state = existing ?? newAgentState(resource);
  if (state.agentDeleteRequestedAt || state.agentDeletedAt) throw new StarterError("cleanup_incomplete_run_cleanup");
  const desired = desiredResource(state, resource);
  const removed = removedSinceRecorded(state, desired);
  if (removed.unsupported) throw new StarterError(removed.unsupported);

  let action: ApplyResult["action"] = "unchanged";
  if (!state.agentId) {
    await saveAgentState(path, state);
    const created = await client.createAgent({ resource: state.createBody }, state.createIdempotencyKey);
    if (!created.agent_id) throw new StarterError("invalid_create_receipt");
    state.agentId = created.agent_id;
    await saveAgentState(path, state);
    action = "created";
  }

  const current = await ownedAgent(client, state);
  if (!matches(current.declared, desired, removed.clearThinking)) {
    const replaced = undeclaredPolicyFields(current.declared?.tool_policy, desired.tool_policy);
    if (replaced.length) onReplacePolicy?.(replaced);
    const sections: Record<string, unknown> = removed.clearThinking
      ? { ...desired, model: { ...desired.model, thinking: null } }
      : { ...desired };
    state.pendingUpdate = sections;
    await saveAgentState(path, state);
    await client.updateAgent(state.agentId, sections);
    const updated = await ownedAgent(client, state);
    if (!matches(updated.declared, desired, removed.clearThinking)) throw new StarterError("agent_update_not_confirmed");
    if (action === "unchanged") action = "updated";
  }
  state.applied = desired;
  delete state.pendingUpdate;
  await saveAgentState(path, state);
  await client.startAgent(state.agentId);
  return { state, agentId: state.agentId, action };
}

export type AgentStatusReport = {
  agentId: string;
  desiredState: string;
  definitionApplied: boolean;
  /** A removal setup will refuse, as an error code. */
  unsupportedRemoval?: string;
  /** Tool policy fields on the Agent that agent/agent.md does not declare; the next setup removes them. */
  undeclaredPolicy: string[];
};

export async function agentStatus(
  client: Pick<ZooworkClient, "getAgent">,
  state: AgentState,
  resource: AgentResource,
): Promise<AgentStatusReport> {
  const agent = await ownedAgent(client, state);
  const desired = desiredResource(state, resource);
  const removed = removedSinceRecorded(state, desired);
  return {
    agentId: agent.agent_id,
    desiredState: agent.status?.desired_state ?? "unknown",
    definitionApplied: !removed.unsupported && matches(agent.declared, desired, removed.clearThinking),
    ...(removed.unsupported ? { unsupportedRemoval: removed.unsupported } : {}),
    undeclaredPolicy: undeclaredPolicyFields(agent.declared?.tool_policy, desired.tool_policy),
  };
}

/**
 * Delete this starter's recorded Sessions, then stop and delete its Agent.
 * Only IDs in local state are touched; the Project is never listed to choose
 * deletion targets. Progress is saved as it goes: each deleted Session leaves
 * the index, and a deleted Agent is recorded before the local files are
 * renamed, so an interrupted cleanup can be run again to finish. A 404 for
 * the Agent counts as "already deleted" only after this app sent the delete,
 * because a key from another Project also gets 404. After success the state files are renamed
 * to `*.retired-<instance>.json`, so the next setup creates a fresh Agent.
 */
export async function cleanupAgent(
  client: LifecycleClient,
  path: string,
  state: AgentState,
  sessions: SessionStore,
): Promise<{ sessionsDeleted: number }> {
  if (!state.agentId) throw new StarterError("agent_creation_uncertain_run_setup_again");
  let sessionsDeleted = 0;
  if (!state.agentDeletedAt) {
    let gone = false;
    try {
      await ownedAgent(client, state);
    } catch (error) {
      // Only after this app sent the delete does a 404 mean "already deleted".
      if (!(state.agentDeleteRequestedAt && isNotFound(error))) throw error;
      gone = true;
    }
    if (!gone) {
      // A create whose response was lost may have made a Session; resolve it
      // first so it is deleted too. Any doubt stops cleanup with state kept.
      await recoverPendingCreates(client, state.agentId, sessions, { strict: true });
      for (const session of await sessions.list({ includeArchived: true })) {
        try {
          await client.deleteSession(state.agentId, session.id);
        } catch (error) {
          if (!isNotFound(error)) throw error;
        }
        await sessions.remove(session.id);
        sessionsDeleted += 1;
      }
      await client.stopAgent(state.agentId);
      state.agentDeleteRequestedAt = new Date().toISOString();
      await saveAgentState(path, state);
      await client.deleteAgent(state.agentId);
    }
    state.agentDeletedAt = new Date().toISOString();
    await saveAgentState(path, state);
  }
  const suffix = state.instance.replace(/[^A-Za-z0-9_-]/g, "_");
  await sessions.retire(suffix);
  await rename(path, path.replace(/\.json$/, `.retired-${suffix}.json`));
  return { sessionsDeleted };
}
