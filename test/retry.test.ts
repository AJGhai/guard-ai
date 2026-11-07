import assert from "node:assert/strict";
import { test } from "node:test";
import { ZooworkError } from "@zoowork-ai/sdk";
import { isTransient, withTransientRetry } from "../lib/zoowork/retry";
import { archiveOnPlatform } from "../lib/zoowork/archive";
import { createFakePlatform } from "./fake-platform";

/** A client whose methods fail with the queued errors first, then succeed. Counts calls. */
function flaky(failures: unknown[]) {
  const calls: string[] = [];
  const queue = [...failures];
  const answer = (name: string) => async (..._args: unknown[]) => {
    calls.push(name);
    const failure = queue.shift();
    if (failure) throw failure;
    return { ok: name };
  };
  const client = {
    getAgent: answer("getAgent"),
    getSession: answer("getSession"),
    createSession: answer("createSession"),
    postEvents: answer("postEvents"),
    archiveSession: answer("archiveSession"),
    updateAgent: answer("updateAgent"),
    resolveApproval: answer("resolveApproval"),
    deleteSession: answer("deleteSession"),
  };
  return { client: withTransientRetry(client, { delayMs: 0 }), calls };
}

const network = () => new TypeError("fetch failed");

test("transient means in transit: network errors, timeouts and gateway 502/503/504, never an API answer", () => {
  assert.ok(isTransient(network()));
  assert.ok(isTransient(Object.assign(new Error("deadline"), { name: "TimeoutError" })));
  for (const status of [502, 503, 504]) assert.ok(isTransient(new ZooworkError(status, "gateway")));
  for (const status of [400, 401, 402, 403, 404, 408, 409, 429, 500]) assert.ok(!isTransient(new ZooworkError(status, "answer")));
  assert.ok(!isTransient(Object.assign(new Error("aborted"), { name: "AbortError" })));
  assert.ok(!isTransient("not an error"));
});

test("a read that fails on the network is retried once and succeeds", async () => {
  const { client, calls } = flaky([network()]);
  assert.deepEqual(await client.getAgent("agt_1"), { ok: "getAgent" });
  assert.deepEqual(calls, ["getAgent", "getAgent"]);
});

test("only one retry: a second network failure is reported", async () => {
  const { client, calls } = flaky([network(), network()]);
  await assert.rejects(client.getSession("agt_1", "ses_1"), TypeError);
  assert.equal(calls.length, 2);
});

test("an HTTP 4xx is never retried", async () => {
  const { client, calls } = flaky([new ZooworkError(409, "busy", "agent_not_running")]);
  await assert.rejects(client.getAgent("agt_1"), (e: unknown) => (e as ZooworkError).status === 409);
  assert.deepEqual(calls, ["getAgent"]);
});

test("writes are retried only when repeating them cannot act twice", async () => {
  // A Session create with its idempotency key: retried.
  let f = flaky([network()]);
  await f.client.createSession("agt_1", { metadata: {} }, "starter:key");
  assert.equal(f.calls.length, 2);
  // Without a key: sent once.
  f = flaky([network()]);
  await assert.rejects(f.client.createSession("agt_1", { metadata: {} }));
  assert.equal(f.calls.length, 1);

  // Events that all carry keys: retried. One without: sent once.
  f = flaky([network()]);
  await f.client.postEvents("agt_1", "ses_1", [{ type: "user.message", content: "hi", idempotency_key: "msg-1" }]);
  assert.equal(f.calls.length, 2);
  f = flaky([network()]);
  await assert.rejects(f.client.postEvents("agt_1", "ses_1", [{ type: "user.interrupt" }]));
  assert.equal(f.calls.length, 1);

  // Archive: a repeat finds the Session archived. Retried.
  f = flaky([new ZooworkError(503, "unavailable")]);
  await f.client.archiveSession("agt_1", "ses_1");
  assert.equal(f.calls.length, 2);

  // Agent updates, approval decisions and deletes: sent once.
  for (const call of [
    (c: typeof f.client) => c.updateAgent("agt_1", {}),
    (c: typeof f.client) => c.resolveApproval("agt_1", "apr_1", { decision: "deny" }),
    (c: typeof f.client) => c.deleteSession("agt_1", "ses_1"),
  ]) {
    f = flaky([network()]);
    await assert.rejects(call(f.client));
    assert.equal(f.calls.length, 1);
  }
});

test("archive's interrupt carries an idempotency key, so the whole archive is retry-safe", async () => {
  const platform = createFakePlatform({ stepMs: 30 });
  const { session_id } = await platform.createSession("agt_test", { metadata: {} });
  await platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "hello" }]);
  const sent: unknown[] = [];
  let failed = false;
  const client = withTransientRetry(
    {
      getSession: platform.getSession,
      archiveSession: platform.archiveSession,
      postEvents: async (...args: Parameters<typeof platform.postEvents>) => {
        sent.push(args[2]);
        // The first interrupt is lost on the way back.
        if (!failed) {
          failed = true;
          await platform.postEvents(...args);
          throw network();
        }
        return platform.postEvents(...args);
      },
    },
    { delayMs: 0 },
  );
  await archiveOnPlatform(client, "agt_test", session_id, { pollMs: 10 });
  assert.equal(sent.length, 2, "the lost interrupt was repeated");
  const [first, second] = sent as { idempotency_key?: string }[][];
  assert.ok(first![0]!.idempotency_key);
  assert.equal(first![0]!.idempotency_key, second![0]!.idempotency_key, "with the same key");
  assert.equal((await platform.getSession("agt_test", session_id)).archived, true);
});
