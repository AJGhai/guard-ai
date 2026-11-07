import { randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import type { AgentResource } from "@zoowork-ai/sdk";
import { StarterError } from "./errors";

/**
 * Local state lives in the ignored `.local/` directory. It holds resource IDs
 * and recovery facts, never credentials. Keep it after any failed or uncertain
 * operation: it is what lets setup and cleanup act only on resources this
 * starter created.
 */
export const APP = "starter";
export const LOCAL_DIR = ".local";

export interface AgentState {
  version: 1;
  app: typeof APP;
  /** Random per-setup identity, written into the Agent's labels and each Session's metadata. */
  instance: string;
  /** Saved before the create request, so an uncertain create is repeated with the same key and body. */
  createIdempotencyKey: string;
  createBody: AgentResource;
  agentId?: string;
  /** The definition last confirmed on the Agent (labels included). Used to notice removed settings. */
  applied?: AgentResource;
  /** Cleanup sent the Agent delete request. A later 404 for this Agent then means it is gone. */
  agentDeleteRequestedAt?: string;
  /** Cleanup deleted the Agent; only the local renames remain. A rerun of cleanup finishes them. */
  agentDeletedAt?: string;
  /** Sections being written by an update whose result is not yet confirmed. */
  pendingUpdate?: Record<string, unknown>;
}

export const agentStatePath = (cwd = process.cwd()) => join(cwd, LOCAL_DIR, "agent.json");

export function labelsFor(state: Pick<AgentState, "instance">): Record<string, string> {
  return { starter_app: APP, starter_instance: state.instance };
}

/** The agent definition plus this instance's ownership labels: what the Agent should declare. */
export function desiredResource(state: Pick<AgentState, "instance">, resource: AgentResource): AgentResource {
  return { ...resource, labels: { ...resource.labels, ...labelsFor(state) } };
}

export function newAgentState(resource: AgentResource): AgentState {
  const instance = randomUUID();
  return {
    version: 1,
    app: APP,
    instance,
    createIdempotencyKey: `zoowork-starter:${instance}`,
    createBody: desiredResource({ instance }, resource),
  };
}

async function privateDir(path: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
}

/** Atomic write with private permissions. */
export async function writePrivateJson(path: string, value: unknown): Promise<void> {
  await privateDir(path);
  const temp = `${path}.${randomUUID()}.tmp`;
  await writeFile(temp, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600, flag: "wx" });
  await rename(temp, path);
}

export async function saveAgentState(path: string, state: AgentState): Promise<void> {
  await writePrivateJson(path, state);
}

/** Load the recorded state. */
export async function loadAgentState(path: string): Promise<AgentState | undefined> {
  let raw: string;
  try {
    raw = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw new StarterError("invalid_local_state", 500);
  }
  let state: AgentState;
  try {
    state = JSON.parse(raw) as AgentState;
  } catch {
    throw new StarterError("invalid_local_state", 500);
  }
  if (
    state.version !== 1 ||
    state.app !== APP ||
    typeof state.instance !== "string" ||
    !state.instance ||
    typeof state.createIdempotencyKey !== "string" ||
    !state.createBody ||
    state.createBody.labels?.starter_instance !== state.instance ||
    (state.agentId !== undefined && (typeof state.agentId !== "string" || !state.agentId))
  ) {
    throw new StarterError("invalid_local_state", 500);
  }
  return state;
}

/**
 * Exclusive lock for setup/cleanup. A lock left by a killed process is not
 * removed automatically: confirm no command is running, then delete it.
 */
export async function withStateLock<T>(path: string, action: () => Promise<T>): Promise<T> {
  await privateDir(path);
  const lockPath = `${path}.lock`;
  let handle;
  try {
    handle = await open(lockPath, "wx", 0o600);
  } catch {
    throw new StarterError("state_locked_review_before_retry");
  }
  try {
    return await action();
  } finally {
    await handle.close();
    await rm(lockPath, { force: true });
  }
}

/** The dev server records its PID so cleanup can refuse to run underneath it. */
export const webPidPath = (cwd = process.cwd()) => join(cwd, LOCAL_DIR, "web.pid");

export async function runningWebPid(cwd = process.cwd()): Promise<number | undefined> {
  let pid: number;
  try {
    pid = Number((await readFile(webPidPath(cwd), "utf8")).trim());
  } catch {
    return undefined;
  }
  if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) return undefined;
  try {
    process.kill(pid, 0);
    return pid;
  } catch {
    return undefined;
  }
}
