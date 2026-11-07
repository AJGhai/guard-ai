import assert from "node:assert/strict";
import { test } from "node:test";
import type { RelayEnvelope } from "../lib/chat/wire";
import { createFakePlatform } from "./fake-platform";
import { relayStream, resumeCursor } from "../lib/zoowork/relay";

type Frame = { id?: string; envelope?: RelayEnvelope };

/** Read SSE frames until `until` returns true or the stream ends. */
async function readFrames(stream: ReadableStream<Uint8Array>, until: (frames: Frame[]) => boolean): Promise<Frame[]> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const frames: Frame[] = [];
  try {
    while (!until(frames)) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const frame: Frame = {};
        for (const line of block.split("\n")) {
          if (line.startsWith("id: ")) frame.id = line.slice(4);
          if (line.startsWith("data: ")) frame.envelope = JSON.parse(line.slice(6)) as RelayEnvelope;
        }
        if (frame.envelope) frames.push(frame);
      }
    }
  } finally {
    await reader.cancel();
  }
  return frames;
}

const finished = (frames: Frame[]) =>
  frames.some((f) => f.envelope?.kind === "event" && f.envelope.event.eventType === "run.finished");

async function sessionWithOneTurn() {
  const platform = createFakePlatform({ stepMs: 1 });
  const { session_id } = await platform.createSession("agt_test", { metadata: {} });
  await platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "hello", idempotency_key: "k1" }]);
  return { platform, sessionId: session_id };
}

test("without a cursor the relay replays the Session from the start, each frame carrying its cursor", async () => {
  const { platform, sessionId } = await sessionWithOneTurn();
  const abort = new AbortController();
  const frames = await readFrames(relayStream(platform, "agt_test", sessionId, undefined, abort.signal), finished);
  abort.abort();
  const types = frames.map((f) => (f.envelope?.kind === "event" ? f.envelope.event.eventType : f.envelope?.kind));
  assert.equal(types[0], "user.message");
  assert.ok(types.includes("agent.assistant"));
  assert.equal(types.at(-1), "run.finished");
  for (const f of frames) assert.match(f.id ?? "", /^pse1:\d+$/);
});

test("with a cursor the relay resumes after it, without repeating earlier events", async () => {
  const { platform, sessionId } = await sessionWithOneTurn();
  const abort = new AbortController();
  const all = await readFrames(relayStream(platform, "agt_test", sessionId, undefined, abort.signal), finished);
  const middle = all[2]!.id!;
  const resumed = await readFrames(relayStream(platform, "agt_test", sessionId, middle, abort.signal), finished);
  abort.abort();
  const seqs = resumed.map((f) => (f.envelope?.kind === "event" ? f.envelope.event.seq : -1));
  const middleSeq = Number(middle.slice(5));
  assert.ok(seqs.length > 0 && seqs.every((s) => s > middleSeq));
});

test("an idempotent retry of the same message does not start a second run", async () => {
  const { platform, sessionId } = await sessionWithOneTurn();
  const abort = new AbortController();
  await readFrames(relayStream(platform, "agt_test", sessionId, undefined, abort.signal), finished);
  const again = await platform.postEvents("agt_test", sessionId, [{ type: "user.message", content: "hello", idempotency_key: "k1" }]);
  assert.equal(again.events[0]?.accepted, true);
  const frames = await readFrames(relayStream(platform, "agt_test", sessionId, undefined, abort.signal), finished);
  abort.abort();
  const starts = frames.filter((f) => f.envelope?.kind === "event" && f.envelope.event.eventType === "run.started");
  assert.equal(starts.length, 1);
});

test("a missing Session ends the relay with a fatal error frame", async () => {
  const platform = createFakePlatform({ stepMs: 1 });
  const abort = new AbortController();
  const frames = await readFrames(relayStream(platform, "agt_test", "ses_missing", undefined, abort.signal), (f) => f.length > 0);
  assert.deepEqual(frames[0]?.envelope, { kind: "relay_error", code: "http_404", fatal: true });
});

test("resumeCursor prefers Last-Event-ID and rejects malformed values", () => {
  const req = (headers: Record<string, string>, query = "") => new Request(`http://localhost/x${query}`, { headers });
  assert.equal(resumeCursor(req({ "last-event-id": "pse1:9" }, "?cursor=pse1:3")), "pse1:9");
  assert.equal(resumeCursor(req({}, "?cursor=pse1:3")), "pse1:3");
  assert.equal(resumeCursor(req({}, "?cursor=bad%20value%0A")), undefined);
  assert.equal(resumeCursor(req({})), undefined);
});

test("approval: a decision resolves the waiting call", async () => {
  const platform = createFakePlatform({ stepMs: 1 });
  const { session_id } = await platform.createSession("agt_test", { metadata: {} });
  await platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "needs approval" }]);
  const abort = new AbortController();
  const stream = relayStream(platform, "agt_test", session_id, undefined, abort.signal);
  const reader = readFrames(stream, finished);
  let approvals = await platform.listApprovals("agt_test", { sessionId: session_id });
  for (let i = 0; i < 50 && approvals.length === 0; i++) {
    await new Promise((r) => setTimeout(r, 5));
    approvals = await platform.listApprovals("agt_test", { sessionId: session_id });
  }
  assert.equal(approvals.length, 1);
  await platform.resolveApproval("agt_test", approvals[0]!.approval_id!, { decision: "deny" });
  const frames = await reader;
  abort.abort();
  const blocked = frames.find(
    (f) => f.envelope?.kind === "event" && f.envelope.event.eventType === "agent.tool" && f.envelope.event.payload.phase === "blocked",
  );
  assert.ok(blocked);
});
