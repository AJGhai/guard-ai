import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "../lib/chat/api";
import type { AppNotice } from "../lib/chat/app-notice";
import { restoreIfSafe, sendFromComposer, type ComposerSend, type ComposerTarget } from "../lib/chat/send-message";

/** A composer and conversation that can change while a send is in flight, as in the browser. */
function harness(overrides: Partial<ComposerSend> = {}) {
  const composer: ComposerTarget = { isActive: true, text: "", setText: (t) => (composer.text = t) };
  const log = { notices: [] as (AppNotice | null)[], sent: [] as [string, string][] };
  const input: ComposerSend = {
    text: "hello",
    sessionId: null,
    ensureSession: async () => "ses_1",
    send: async (id, text) => {
      log.sent.push([id, text]);
    },
    restoreText: (text) => restoreIfSafe(composer, text),
    setNotice: (notice) => log.notices.push(notice),
    ...overrides,
  };
  return { input, log, composer };
}

/** A request whose result arrives when the test says so. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

const createFailure = () => new ApiError("http_502", 502, "Could not start the conversation (http_502)");

test("a new chat creates its Session, then sends", async () => {
  const { input, log, composer } = harness();
  assert.equal(await sendFromComposer(input), true);
  assert.deepEqual(log.sent, [["ses_1", "hello"]]);
  assert.equal(composer.text, "");
});

test("a failed create puts the text back in an empty composer and shows why", async () => {
  const { input, log, composer } = harness({ ensureSession: async () => Promise.reject(createFailure()) });
  assert.equal(await sendFromComposer(input), false);
  assert.equal(composer.text, "hello");
  assert.deepEqual(log.notices.at(-1), { message: "Could not start the conversation (http_502)" });
});

test("a draft typed while the create was pending is kept; the unsent message is offered in the notice", async () => {
  const create = deferred<string>();
  const { input, log, composer } = harness({ ensureSession: () => create.promise });
  const sending = sendFromComposer(input);
  composer.text = "a newer draft"; // typed while waiting
  create.reject(createFailure());
  assert.equal(await sending, false);
  assert.equal(composer.text, "a newer draft");
  assert.deepEqual(log.notices.at(-1), { message: "Could not start the conversation (http_502)", unsentText: "hello" });
});

test("after switching to another conversation, the unsent message is not written into it", async () => {
  const create = deferred<string>();
  const { input, log, composer } = harness({ ensureSession: () => create.promise });
  const sending = sendFromComposer(input);
  composer.isActive = false; // the user opened another conversation
  create.reject(new TypeError("Failed to fetch"));
  assert.equal(await sending, false);
  assert.equal(composer.text, "");
  assert.equal(log.notices.at(-1)?.unsentText, "hello");
  assert.match(log.notices.at(-1)?.message ?? "", /Could not start the conversation/);
});

test("a failed send to an existing Session keeps a newer draft and offers the unsent message", async () => {
  const send = deferred<void>();
  const { input, log, composer } = harness({ sessionId: "ses_9", send: () => send.promise });
  const sending = sendFromComposer(input);
  composer.text = "typed meanwhile";
  send.reject(new Error("http_402"));
  assert.equal(await sending, false);
  assert.equal(composer.text, "typed meanwhile");
  assert.equal(log.notices.at(-1)?.unsentText, "hello");
});

test("a failed send with an empty composer puts the text back; the controller shows the reason", async () => {
  const { input, log, composer } = harness({ sessionId: "ses_9", send: async () => Promise.reject(new Error("http_402")) });
  assert.equal(await sendFromComposer(input), false);
  assert.equal(composer.text, "hello");
  assert.deepEqual(log.notices, [null], "only the earlier notice is cleared");
});

test("restoreIfSafe treats a whitespace-only composer as empty", () => {
  const composer: ComposerTarget = { isActive: true, text: "  \n", setText: (t) => (composer.text = t) };
  assert.equal(restoreIfSafe(composer, "x"), true);
  assert.equal(composer.text, "x");
});

test("a new chat's first message shows as pending until it is handed to its Session", async () => {
  const create = deferred<string>();
  const events: string[] = [];
  const { input, log } = harness({
    ensureSession: () => create.promise,
    setStarting: (text) => events.push(text === null ? "clear" : `pending:${text}`),
    send: async (id, text) => {
      events.push("send");
      log.sent.push([id, text]);
    },
  });
  const sending = sendFromComposer(input);
  assert.deepEqual(events, ["pending:hello"], "shown at once, before the Session exists");
  create.resolve("ses_1");
  assert.equal(await sending, true);
  assert.deepEqual(events, ["pending:hello", "send", "clear"], "cleared only after send() put up its own bubble");
});

test("when the create fails, the pending bubble goes and the text comes back exactly once", async () => {
  const events: string[] = [];
  const { input, log, composer } = harness({
    ensureSession: async () => Promise.reject(createFailure()),
    setStarting: (text) => events.push(text === null ? "clear" : "pending"),
    restoreText: (text) => {
      events.push("restore");
      return restoreIfSafe(composer, text);
    },
  });
  assert.equal(await sendFromComposer(input), false);
  assert.deepEqual(events, ["pending", "clear", "restore"], "the bubble is removed before the text is restored");
  assert.equal(composer.text, "hello");
  assert.equal(log.notices.at(-1)?.unsentText, undefined, "not also offered in the notice");
});

test("a message to an existing Session has no pending state", async () => {
  const events: string[] = [];
  const { input } = harness({ sessionId: "ses_9", setStarting: () => events.push("starting") });
  assert.equal(await sendFromComposer(input), true);
  assert.deepEqual(events, []);
});
