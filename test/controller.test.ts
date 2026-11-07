import assert from "node:assert/strict";
import { test } from "node:test";
import { ControllerPool } from "../lib/chat/controller-pool";
import { SessionController } from "../lib/chat/session-controller";
import type { WireEvent } from "../lib/chat/wire";

/** Just enough of the browser's EventSource for the controller, driven by the test. */
class FakeEventSource {
  static readonly CLOSED = 2;
  static all: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string; lastEventId: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
  get closed() {
    return this.readyState === FakeEventSource.CLOSED;
  }
  connect() {
    this.readyState = 1;
    this.onopen?.();
  }
  send(event: WireEvent) {
    this.onmessage?.({ data: JSON.stringify({ kind: "event", event }), lastEventId: `pse1:${event.seq}` });
  }
}
Object.assign(globalThis, { EventSource: FakeEventSource });

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** Longer than the controller's 30 ms flush. */
const flushed = () => sleep(60);
const sourceFor = (sessionId: string) => FakeEventSource.all.filter((s) => s.url.includes(sessionId)).at(-1)!;

const ev = (seq: number, eventType: string, runId?: string): WireEvent => ({
  seq,
  eventType,
  payload: eventType === "user.message" ? { content: "hi" } : eventType === "run.finished" ? { status: "succeeded" } : {},
  ...(runId ? { runId } : {}),
});

/** Open a conversation and start a run in it. */
async function busy(pool: ControllerPool, id: string) {
  pool.open(id);
  const source = sourceFor(id);
  source.connect();
  source.send(ev(1, "user.message"));
  source.send(ev(2, "run.started", "r1"));
  await flushed();
  return source;
}

test("a conversation switched away from keeps following while its run is in flight, then closes", async () => {
  const pool = new ControllerPool();
  const source = await busy(pool, "ses_a");
  assert.deepEqual(pool.getRunning(), ["ses_a"]);

  pool.release("ses_a");
  assert.equal(source.closed, false, "the stream stays open in the background");
  assert.equal(pool.get("ses_a").following, true);
  assert.deepEqual(pool.getRunning(), ["ses_a"], "the sidebar still shows it running");

  source.send(ev(3, "run.finished", "r1"));
  await flushed();
  assert.equal(source.closed, true, "closed once the run ended");
  assert.equal(pool.get("ses_a").following, false);
  assert.deepEqual(pool.getRunning(), []);
  pool.disposeAll();
});

test("an idle conversation stops following as soon as it is switched away from", async () => {
  const pool = new ControllerPool();
  const source = await busy(pool, "ses_b");
  source.send(ev(3, "run.finished", "r1"));
  await flushed();
  pool.release("ses_b");
  assert.equal(source.closed, true);
  assert.deepEqual(pool.getRunning(), []);
  pool.disposeAll();
});

test("switching back to a background conversation keeps its stream", async () => {
  const pool = new ControllerPool();
  const source = await busy(pool, "ses_c");
  pool.release("ses_c");
  pool.open("ses_c");
  source.send(ev(3, "run.finished", "r1"));
  await flushed();
  assert.equal(source.closed, false, "on screen again, so it keeps following after the run");
  pool.disposeAll();
});

test("only a few background streams stay open; the oldest is closed first", async () => {
  const pool = new ControllerPool(undefined, 1);
  const first = await busy(pool, "ses_d1");
  pool.release("ses_d1");
  const second = await busy(pool, "ses_d2");
  pool.release("ses_d2");
  assert.equal(first.closed, true);
  assert.equal(second.closed, false);
  assert.deepEqual(pool.getRunning(), ["ses_d2"]);
  pool.disposeAll();
});

test("a fresh stream is replaying until it has been quiet for a moment; a resume is not", async () => {
  const controller = new SessionController("ses_e");
  controller.open();
  const source = sourceFor("ses_e");
  assert.equal(controller.getSnapshot().replaying, true);
  source.connect();
  source.send(ev(1, "user.message"));
  await flushed();
  assert.equal(controller.getSnapshot().replaying, true, "events are still arriving");
  await sleep(500);
  assert.equal(controller.getSnapshot().replaying, false);

  controller.close();
  controller.open();
  assert.ok(sourceFor("ses_e").url.includes("cursor="), "reopened after the last cursor");
  assert.equal(controller.getSnapshot().replaying, false);
  controller.dispose();
});
