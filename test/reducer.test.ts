import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applyEvent,
  applyEvents,
  emptyView,
  foldEvents,
  pendingApprovals,
  settleOutgoing,
  toThreadMessages,
  type RunTurn,
} from "../lib/chat/reducer";
import type { WireEvent } from "../lib/chat/wire";
import { createFakePlatform } from "./fake-platform";

/** Builds events with increasing seq, the shapes the Platform stream sends. */
function script() {
  let seq = 0;
  const events: WireEvent[] = [];
  const add = (eventType: string, payload: Record<string, unknown> = {}, runId?: string) => {
    seq += 2; // gaps are normal
    const event: WireEvent = { seq, eventType, payload, ...(runId ? { runId } : {}) };
    events.push(event);
    return event;
  };
  return {
    events,
    user: (text: string) => add("user.message", { content: [{ type: "text", text }] }),
    started: (runId: string) => add("run.started", { trigger: "user" }, runId),
    thinking: (runId: string, text: string) => add("agent.thinking", { text }, runId),
    assistant: (runId: string, text: string) =>
      add("agent.assistant", { segment: 1, message: { role: "assistant", content: [{ type: "text", text }] } }, runId),
    tool: (runId: string, payload: Record<string, unknown>) => add("agent.tool", payload, runId),
    approval: (runId: string, payload: Record<string, unknown>) => add("agent.approval", payload, runId),
    error: (runId: string, errorMessage: string) => add("agent.error", { errorMessage }, runId),
    finished: (runId: string, status: string) => add("run.finished", { status }, runId),
  };
}

type PartLike = { type: string; toolName?: string; args?: unknown; result?: unknown; isError?: boolean };

const partsOf = (message: { content: unknown } | undefined) => (message?.content ?? []) as readonly PartLike[];

const lastRun = (view: ReturnType<typeof emptyView>) => view.turns.findLast((t) => t.role === "assistant") as RunTurn;

test("a turn folds into a user message and an assistant message; reasoning and text keep their order", () => {
  const s = script();
  s.user("hello");
  s.started("run_1");
  s.thinking("run_1", "");
  s.thinking("run_1", "Thinking about it.");
  s.assistant("run_1", "Hi there.");
  let view = foldEvents(s.events);

  assert.equal(view.activeRunId, "run_1");
  let messages = toThreadMessages(view);
  assert.equal(messages.length, 2);
  assert.deepEqual(messages[0]!.content, [{ type: "text", text: "hello" }]);
  assert.equal(messages[0]!.role, "user");
  assert.equal(messages[1]!.role, "assistant");
  assert.deepEqual(messages[1]!.content, [
    { type: "reasoning", text: "Thinking about it." },
    { type: "text", text: "Hi there." },
  ]);
  assert.deepEqual(messages[1]!.status, { type: "running" });

  view = applyEvent(view, s.finished("run_1", "succeeded"));
  assert.equal(view.activeRunId, null);
  messages = toThreadMessages(view);
  assert.deepEqual(messages[1]!.status, { type: "complete", reason: "stop" });
});

test("a tool call starts without a result and ends with its preview; an error result is marked", () => {
  const s = script();
  s.user("search");
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "web_search", toolCallId: "call_1", args: { query: "http3" } });
  s.tool("run_1", { phase: "start", toolName: "web_fetch", toolCallId: "call_2", args: { url: "https://a.example" } });
  let view = foldEvents(s.events);
  let parts = partsOf(toThreadMessages(view)[1]);
  assert.equal(parts.length, 2);
  assert.equal(parts[0]!.type, "tool-call");
  assert.equal(parts[0]!.toolName, "web_search");
  assert.deepEqual(parts[0]!.args, { query: "http3" });
  assert.equal(parts[0]!.result, undefined);
  assert.equal(view.tools.call_1!.phase, "running");

  // Ends arrive out of order: pairing is by toolCallId.
  view = applyEvents(view, [
    s.tool("run_1", { phase: "end", toolName: "web_fetch", toolCallId: "call_2", isError: true, resultPreview: "timeout" }),
    s.tool("run_1", { phase: "end", toolName: "web_search", toolCallId: "call_1", isError: false, resultPreview: "{\"results\":[" }),
  ]);
  assert.equal(view.tools.call_1!.phase, "succeeded");
  assert.equal(view.tools.call_1!.resultPreview, "{\"results\":[");
  assert.equal(view.tools.call_2!.phase, "failed");
  parts = partsOf(toThreadMessages(view)[1]);
  assert.equal(parts[0]!.result, "{\"results\":[");
  assert.equal(parts[0]!.isError, false);
  assert.equal(parts[1]!.isError, true);
});

test("an approval request makes the call wait; the resolution and a denied block end the wait", () => {
  const s = script();
  s.user("fetch with approval");
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "web_fetch", toolCallId: "call_1", args: { url: "https://example.com/" } });
  s.approval("run_1", {
    phase: "requested",
    approvalId: "apr_1",
    toolCallId: "call_1",
    toolName: "web_fetch",
    arguments: { url: "https://example.com/" },
  });
  let view = foldEvents(s.events);
  assert.deepEqual(
    pendingApprovals(view).map((a) => a.approvalId),
    ["apr_1"],
  );
  assert.equal(view.tools.call_1!.phase, "awaiting_approval");
  assert.equal(view.tools.call_1!.approvalId, "apr_1");
  assert.deepEqual(toThreadMessages(view)[1]!.status, { type: "requires-action", reason: "tool-calls" });

  view = applyEvents(view, [
    s.approval("run_1", { phase: "resolved", approvalId: "apr_1", toolCallId: "call_1", resolution: "deny" }),
    s.tool("run_1", { phase: "blocked", toolName: "web_fetch", toolCallId: "call_1", deniedReason: "approval-denied" }),
  ]);
  assert.equal(pendingApprovals(view).length, 0);
  assert.equal(view.approvals.apr_1!.state, "resolved");
  assert.equal(view.approvals.apr_1!.resolution, "deny");
  assert.equal(view.tools.call_1!.phase, "blocked");
  assert.equal(view.tools.call_1!.blockedReason, "approval-denied");
  assert.deepEqual(toThreadMessages(view)[1]!.status, { type: "running" });
  const part = partsOf(toThreadMessages(view)[1])[0]!;
  assert.deepEqual(part.result, { blocked: "approval-denied" });
  assert.equal(part.isError, true);
});

test("an approval that arrives before its tool start still creates the call", () => {
  const s = script();
  s.started("run_1");
  s.approval("run_1", { phase: "requested", approvalId: "apr_1", toolCallId: "call_9", toolName: "exec", arguments: { cmd: "ls" } });
  s.tool("run_1", { phase: "start", toolName: "exec", toolCallId: "call_9", args: { cmd: "ls" } });
  const view = foldEvents(s.events);
  assert.equal(view.tools.call_9!.phase, "awaiting_approval");
  assert.equal(lastRun(view).parts.length, 1);
});

test("a failed run shows the last agent error", () => {
  const s = script();
  s.user("hi");
  s.started("run_1");
  s.error("run_1", "model unavailable");
  s.finished("run_1", "failed");
  const view = foldEvents(s.events);
  assert.equal(lastRun(view).status, "failed");
  assert.deepEqual(toThreadMessages(view)[1]!.status, { type: "incomplete", reason: "error", error: "model unavailable" });
});

test("an interrupted run is cancelled: running calls are blocked and pending approvals closed", () => {
  const s = script();
  s.user("long task");
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "web_search", toolCallId: "call_1", args: {} });
  s.approval("run_1", { phase: "requested", approvalId: "apr_1", toolCallId: "call_2", toolName: "web_fetch", arguments: {} });
  s.events.push({ seq: 100, eventType: "user.interrupt", payload: {} });
  const view = applyEvent(foldEvents(s.events), { seq: 101, eventType: "run.finished", payload: { status: "aborted" }, runId: "run_1" });
  assert.equal(view.activeRunId, null);
  assert.deepEqual(toThreadMessages(view)[1]!.status, { type: "incomplete", reason: "cancelled" });
  assert.equal(view.tools.call_1!.phase, "blocked");
  assert.equal(view.tools.call_1!.blockedReason, "interrupted");
  assert.equal(view.approvals.apr_1!.state, "closed");
  assert.equal(pendingApprovals(view).length, 0);
});

test("a yielded run ends the turn but leaves its calls running", () => {
  const s = script();
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "spawn", toolCallId: "call_1", args: {} });
  const view = applyEvent(foldEvents(s.events), {
    seq: 50,
    eventType: "run.finished",
    payload: { status: "succeeded", turnEndReason: "yielded" },
    runId: "run_1",
  });
  assert.equal(lastRun(view).yielded, true);
  assert.equal(view.tools.call_1!.phase, "running");
});

test("replaying history is idempotent, including a replay that overlaps what was applied", () => {
  const s = script();
  s.user("one");
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "web_search", toolCallId: "call_1", args: { query: "q" } });
  s.tool("run_1", { phase: "end", toolName: "web_search", toolCallId: "call_1", resultPreview: "ok" });
  s.assistant("run_1", "Answer one.");
  s.finished("run_1", "succeeded");
  s.user("two");
  s.started("run_2");
  s.assistant("run_2", "Answer two.");
  s.finished("run_2", "succeeded");

  const once = foldEvents(s.events);
  // Applying the same events again changes nothing and keeps the same object.
  assert.equal(applyEvents(once, s.events), once);

  // Live events up to the middle, then a reconnect that replays from an earlier point.
  const middle = foldEvents(s.events.slice(0, 6));
  const overlapped = applyEvents(middle, s.events.slice(3));
  assert.deepEqual(overlapped, once);
  assert.deepEqual(toThreadMessages(overlapped), toThreadMessages(once));
  assert.equal(toThreadMessages(once).length, 4);
});

test("folding does not change the view it started from", () => {
  const s = script();
  s.user("hi");
  s.started("run_1");
  s.tool("run_1", { phase: "start", toolName: "web_search", toolCallId: "call_1", args: {} });
  const before = foldEvents(s.events);
  const snapshot = structuredClone(before);
  applyEvents(before, [
    s.tool("run_1", { phase: "end", toolName: "web_search", toolCallId: "call_1", resultPreview: "x" }),
    s.assistant("run_1", "done"),
    s.finished("run_1", "succeeded"),
  ]);
  assert.deepEqual(before, snapshot);
});

test("an outgoing message is dropped once the stream echoes it", () => {
  const s = script();
  s.user("earlier");
  const base = foldEvents(s.events);
  const outgoing = [
    { key: "k1", text: "hello" },
    { key: "k2", text: "hello" },
  ];
  assert.equal(settleOutgoing(outgoing, base, 0).length, 2, "an older identical message does not count");
  const after = applyEvent(base, s.user("hello"));
  assert.deepEqual(settleOutgoing(outgoing, after, base.lastSeq), [{ key: "k2", text: "hello" }]);
  const messages = toThreadMessages(after, [{ key: "k2", text: "hello" }]);
  assert.equal(messages.at(-1)!.id, "outgoing-k2");
});

test("the fake Platform's approval turn folds into a resolved approval and a finished run", async () => {
  const platform = createFakePlatform({ stepMs: 1 });
  const { session_id } = await platform.createSession("agt_test", { metadata: {} });
  await platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "needs approval" }]);
  const abort = new AbortController();
  let view = emptyView();
  for await (const event of platform.streamEvents("agt_test", session_id, { signal: abort.signal })) {
    view = applyEvent(view, event);
    if (event.eventType === "agent.approval" && event.payload.phase === "requested") {
      const [approval] = pendingApprovals(view);
      await platform.resolveApproval("agt_test", approval!.approvalId, { decision: "allow-once" });
    }
    if (event.eventType === "run.finished") break;
  }
  abort.abort();
  assert.equal(view.activeRunId, null);
  const approvals = Object.values(view.approvals);
  assert.equal(approvals.length, 1);
  assert.equal(approvals[0]!.resolution, "allow-once");
  const tool = view.tools[approvals[0]!.toolCallId]!;
  assert.equal(tool.phase, "succeeded");
  assert.equal(tool.resultPreview, "Scripted tool output.");
  const messages = toThreadMessages(view);
  assert.deepEqual(
    messages.map((m) => m.role),
    ["user", "assistant"],
  );
  assert.deepEqual(messages[1]!.status, { type: "complete", reason: "stop" });
});
