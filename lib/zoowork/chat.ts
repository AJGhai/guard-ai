import { ZooworkError, type ApprovalDecision } from "@zoowork-ai/sdk";
import { MESSAGE_MAX, type AppStatus, type SessionSummary } from "../chat/wire";
import { archiveOnPlatform } from "./archive";
import { errorCode, isNotFound, StarterError } from "./errors";
import { ownedSession, type Runtime } from "./runtime";
import { createRecordedSession, recoverPendingCreates } from "./session-create";
import { titleFrom, type SessionEntry } from "./sessions";
import { APP } from "./state";

// The operations behind the chat routes. Each one that takes a Session ID
// checks it against this app's Session index first (ownedSession).

const THREAD_ID = /^[A-Za-z0-9_-]{1,128}$/;
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{8,200}$/;
const DECISIONS: readonly ApprovalDecision[] = ["allow-once", "allow-always", "deny"];

export function summary(entry: SessionEntry): SessionSummary {
  return {
    id: entry.id,
    title: entry.title,
    createdAt: entry.createdAt,
    updatedAt: entry.updatedAt,
    archived: !!entry.archived,
  };
}

function text(value: unknown, code: string, max: number): string {
  if (typeof value !== "string" || !value.trim()) throw new StarterError(code);
  if (value.length > max) throw new StarterError(code);
  return value;
}

function key(value: unknown): string {
  if (typeof value !== "string" || !IDEMPOTENCY_KEY.test(value)) throw new StarterError("invalid_idempotency_key");
  return value;
}

/** The Platform refused a write because the Session is archived. */
function archivedOnPlatform(error: unknown): boolean {
  return error instanceof ZooworkError && error.status === 409 && error.type === "session_archived";
}

/** The conversation list. Creates whose response was lost are resolved first, so they appear. */
export async function listSessions(runtime: Runtime): Promise<SessionSummary[]> {
  await recoverPendingCreates(runtime.client, runtime.agentId, runtime.sessions);
  return (await runtime.sessions.list({ includeArchived: true })).map(summary);
}

/**
 * Create the Session for a browser thread. The idempotency key is derived
 * from the thread id, so a retry from the same thread returns the same Session.
 */
export async function createSession(runtime: Runtime, body: Record<string, unknown>): Promise<SessionSummary> {
  const threadId = body.threadId;
  if (typeof threadId !== "string" || !THREAD_ID.test(threadId)) throw new StarterError("invalid_thread_id");
  const title = titleFrom(body.text);
  const entry = await createRecordedSession(runtime.client, runtime.agentId, runtime.sessions, {
    threadId,
    idempotencyKey: `${APP}:${runtime.instance}:session:${threadId}`,
    title,
    metadata: { starter_app: APP, starter_instance: runtime.instance, title },
    requestedAt: new Date().toISOString(),
  });
  return summary(entry);
}

export async function getSessionSummary(runtime: Runtime, id: string): Promise<SessionSummary> {
  return summary(await ownedSession(runtime, id));
}

/** Rename in the local index only; the Platform keeps the title it was created with. */
export async function renameSession(runtime: Runtime, id: string, body: Record<string, unknown>): Promise<SessionSummary> {
  await ownedSession(runtime, id);
  const title = titleFrom(text(body.title, "invalid_title", 500));
  const entry = await runtime.sessions.update(id, { title });
  if (!entry) throw new StarterError("session_not_found", 404);
  return summary(entry);
}

/** Delete the Session on the Platform (an in-flight run is cancelled there), then forget it. */
export async function deleteSession(runtime: Runtime, id: string): Promise<void> {
  await ownedSession(runtime, id);
  try {
    await runtime.client.deleteSession(runtime.agentId, id);
  } catch (error) {
    if (!isNotFound(error)) throw error;
  }
  await runtime.sessions.remove(id);
}

export async function archiveSession(runtime: Runtime, id: string): Promise<SessionSummary> {
  const entry = await ownedSession(runtime, id);
  if (!entry.archived) {
    await archiveOnPlatform(runtime.client, runtime.agentId, id);
  }
  const updated = await runtime.sessions.update(id, { archived: true });
  return summary(updated ?? { ...entry, archived: true });
}

/**
 * Post one user message. The browser sends an idempotency key per message
 * and reuses it when it retries, so a retry is delivered once.
 */
export async function sendMessage(
  runtime: Runtime,
  id: string,
  body: Record<string, unknown>,
): Promise<{ accepted: true; seq?: number }> {
  const entry = await ownedSession(runtime, id);
  if (entry.archived) throw new StarterError("session_archived", 409);
  const content = text(body.text, "invalid_message", MESSAGE_MAX);
  const idempotencyKey = key(body.idempotencyKey);
  let receipt;
  try {
    receipt = await runtime.client.postEvents(runtime.agentId, id, [
      { type: "user.message", content, idempotency_key: idempotencyKey },
    ]);
  } catch (error) {
    if (archivedOnPlatform(error)) {
      await runtime.sessions.update(id, { archived: true });
      throw new StarterError("session_archived", 409);
    }
    throw error;
  }
  const first = receipt.events[0];
  if (first?.accepted !== true) throw new StarterError("message_not_accepted", 409);
  await runtime.sessions.update(id, { updatedAt: new Date().toISOString() });
  return { accepted: true, ...(typeof first.seq === "number" ? { seq: first.seq } : {}) };
}

/** Interrupt the run in flight. With no run in flight the Platform answers accepted: false. */
export async function interruptSession(
  runtime: Runtime,
  id: string,
  body: Record<string, unknown>,
): Promise<{ accepted: boolean }> {
  await ownedSession(runtime, id);
  const idempotencyKey = key(body.idempotencyKey);
  const receipt = await runtime.client.postEvents(runtime.agentId, id, [
    { type: "user.interrupt", idempotency_key: idempotencyKey },
  ]);
  return { accepted: receipt.events[0]?.accepted === true };
}

/**
 * Resolve a pending approval. The approval must be pending on this Session
 * and allow the decision; an approval ID alone is not enough.
 */
export async function resolveSessionApproval(
  runtime: Runtime,
  id: string,
  approvalId: string,
  body: Record<string, unknown>,
): Promise<{ status: string; signaled: boolean }> {
  await ownedSession(runtime, id);
  const decision = body.decision;
  if (typeof decision !== "string" || !DECISIONS.includes(decision as ApprovalDecision)) {
    throw new StarterError("invalid_decision");
  }
  const pending = await runtime.client.listApprovals(runtime.agentId, { status: "pending", sessionId: id });
  const approval = pending.find((a) => a.approval_id === approvalId && (a.session_id === undefined || a.session_id === id));
  if (!approval) throw new StarterError("approval_not_pending", 409);
  if (Array.isArray(approval.allowed_decisions) && !approval.allowed_decisions.includes(decision as ApprovalDecision)) {
    throw new StarterError("decision_not_allowed", 409);
  }
  const resolved = await runtime.client.resolveApproval(runtime.agentId, approvalId, {
    decision: decision as ApprovalDecision,
  });
  return { status: resolved.status ?? "resolved", signaled: resolved.signaled === true };
}

/** Agent readiness for the status bar. Never throws: a failure is reported as a code. */
export async function appStatus(getRuntime: () => Promise<Runtime>): Promise<AppStatus> {
  try {
    const runtime = await getRuntime();
    const { definitionApplied, desiredState } = await runtime.checkAgent();
    if (desiredState !== "running") return { agent: "not_running" };
    if (!definitionApplied) return { agent: "definition_changed" };
    return { agent: "ready" };
  } catch (error) {
    return { agent: "unavailable", code: errorCode(error) };
  }
}
