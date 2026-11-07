import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  appStatus,
  archiveSession,
  createSession,
  interruptSession,
  listSessions,
  resolveSessionApproval,
  sendMessage,
} from "../lib/zoowork/chat";
import type { Runtime } from "../lib/zoowork/runtime";
import { fileSessionStore } from "../lib/zoowork/sessions";
import { StarterError } from "../lib/zoowork/errors";
import { createFakePlatform } from "./fake-platform";

async function runtime(): Promise<Runtime & { platform: ReturnType<typeof createFakePlatform> }> {
  const dir = await mkdtemp(join(tmpdir(), "starter-chat-"));
  const platform = createFakePlatform({ stepMs: 2 });
  return {
    platform,
    client: platform,
    agentId: "agt_test",
    instance: "inst",
    sessions: fileSessionStore(join(dir, "sessions.json"), { instance: "inst", agentId: "agt_test" }),
    checkAgent: async () => ({ definitionApplied: true, desiredState: "running" }),
  };
}

const code = (expected: string) => (e: unknown) => e instanceof StarterError && e.code === expected;

async function waitFor<T>(read: () => Promise<T>, done: (value: T) => boolean): Promise<T> {
  for (let i = 0; i < 200; i++) {
    const value = await read();
    if (done(value)) return value;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("timed out");
}

test("a thread's create is idempotent and the Session is listed with its first message as title", async () => {
  const rt = await runtime();
  const first = await createSession(rt, { threadId: "chat-1", text: "  What is   QUIC? " });
  const again = await createSession(rt, { threadId: "chat-1", text: "What is QUIC?" });
  assert.equal(again.id, first.id);
  assert.equal(rt.platform.sessionCount(), 1);
  const listed = await listSessions(rt);
  assert.deepEqual(
    listed.map((s) => [s.id, s.title, s.archived]),
    [[first.id, "What is QUIC?", false]],
  );
  await assert.rejects(createSession(rt, { threadId: "bad id!", text: "x" }), code("invalid_thread_id"));
});

test("a message retried with the same key is delivered once; other Sessions are refused", async () => {
  const rt = await runtime();
  const { id } = await createSession(rt, { threadId: "chat-1", text: "hi" });
  const sent = await sendMessage(rt, id, { text: "hi", idempotencyKey: "msg-0001" });
  const retried = await sendMessage(rt, id, { text: "hi", idempotencyKey: "msg-0001" });
  assert.equal(sent.seq, retried.seq);
  await assert.rejects(sendMessage(rt, id, { text: "", idempotencyKey: "msg-0002" }), code("invalid_message"));
  await assert.rejects(sendMessage(rt, id, { text: "hi", idempotencyKey: "short" }), code("invalid_idempotency_key"));

  const stranger = await rt.platform.createSession("agt_test", { metadata: {} });
  await assert.rejects(
    sendMessage(rt, stranger.session_id, { text: "hi", idempotencyKey: "msg-0003" }),
    code("session_not_found"),
  );
});

test("an approval is resolved only when it is pending on that Session and the decision is allowed", async () => {
  const rt = await runtime();
  const a = await createSession(rt, { threadId: "chat-a", text: "a" });
  const b = await createSession(rt, { threadId: "chat-b", text: "b" });
  await sendMessage(rt, a.id, { text: "needs approval", idempotencyKey: "msg-a-0001" });
  const [approval] = await waitFor(
    () => rt.platform.listApprovals("agt_test", { sessionId: a.id }),
    (list) => list.length > 0,
  );
  const approvalId = approval!.approval_id!;

  await assert.rejects(resolveSessionApproval(rt, b.id, approvalId, { decision: "deny" }), code("approval_not_pending"));
  await assert.rejects(
    resolveSessionApproval(rt, a.id, approvalId, { decision: "allow-always" }),
    code("decision_not_allowed"),
  );
  await assert.rejects(resolveSessionApproval(rt, a.id, approvalId, { decision: "maybe" }), code("invalid_decision"));
  const resolved = await resolveSessionApproval(rt, a.id, approvalId, { decision: "deny" });
  assert.equal(resolved.signaled, true);
});

test("interrupt reports whether a run was in flight; an archived Session takes no messages", async () => {
  const rt = await runtime();
  const { id } = await createSession(rt, { threadId: "chat-1", text: "hi" });
  assert.deepEqual(await interruptSession(rt, id, { idempotencyKey: "stop-0001" }), { accepted: false });
  // A message that asks for approval: the run waits for a decision, so it is still in flight below.
  await sendMessage(rt, id, { text: "needs approval", idempotencyKey: "msg-0001" });
  assert.deepEqual(await interruptSession(rt, id, { idempotencyKey: "stop-0002" }), { accepted: true });

  const archived = await archiveSession(rt, id);
  assert.equal(archived.archived, true);
  await assert.rejects(sendMessage(rt, id, { text: "more", idempotencyKey: "msg-0002" }), code("session_archived"));
  assert.equal((await listSessions(rt))[0]!.archived, true);
});

test("app status reports readiness and turns failures into codes", async () => {
  const rt = await runtime();
  assert.deepEqual(await appStatus(async () => rt), { agent: "ready" });
  const changed = { ...rt, checkAgent: async () => ({ definitionApplied: false, desiredState: "running" }) };
  assert.deepEqual(await appStatus(async () => changed), { agent: "definition_changed" });
  const stopped = { ...rt, checkAgent: async () => ({ definitionApplied: true, desiredState: "stopped" }) };
  assert.deepEqual(await appStatus(async () => stopped), { agent: "not_running" });
  assert.deepEqual(await appStatus(async () => Promise.reject(new StarterError("run_setup_first", 503))), {
    agent: "unavailable",
    code: "run_setup_first",
  });
});
