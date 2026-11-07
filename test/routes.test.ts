import assert from "node:assert/strict";
import { test } from "node:test";
import { archiveOnPlatform } from "../lib/zoowork/archive";
import { guard } from "../lib/zoowork/http";
import { createFakePlatform } from "./fake-platform";

const req = (headers: Record<string, string>) => new Request("http://localhost:3000/api/x", { method: "POST", headers });

test("guard answers only on loopback hosts", () => {
  assert.doesNotThrow(() => guard(req({ host: "localhost:3000" })));
  assert.doesNotThrow(() => guard(req({ host: "127.0.0.1:3000" })));
  assert.doesNotThrow(() => guard(req({ host: "[::1]:3000" })));
  assert.throws(() => guard(req({ host: "example.com" })), /local_only_add_authentication_first/);
  assert.throws(() => guard(req({ host: "192.168.1.5:3000" })), /local_only_add_authentication_first/);
});

test("state-changing routes refuse other origins, including Origin: null, with 403", () => {
  const base = { host: "localhost:3000", "content-type": "application/json" };
  assert.doesNotThrow(() => guard(req({ ...base, origin: "http://localhost:3000" }), { write: true }));
  assert.doesNotThrow(() => guard(req(base), { write: true }));
  for (const origin of ["http://evil.example", "null", "not a url"]) {
    assert.throws(
      () => guard(req({ ...base, origin }), { sameOrigin: true }),
      (e: unknown) => (e as { code?: string; status?: number }).code === "cross_origin_request" && (e as { status: number }).status === 403,
    );
  }
  assert.throws(() => guard(req({ host: "localhost:3000" }), { write: true }), /expected_application_json/);
  assert.doesNotThrow(() => guard(req({ host: "localhost:3000" }), { sameOrigin: true }));
});

test("archive interrupts a running turn, waits for it to end, and is safe to repeat", async () => {
  const platform = createFakePlatform({ stepMs: 30 });
  const { session_id } = await platform.createSession("agt_test", { metadata: {} });
  await platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "hello" }]);
  assert.equal((await platform.getSession("agt_test", session_id)).run_status, "running");
  await archiveOnPlatform(platform, "agt_test", session_id, { pollMs: 10 });
  const after = await platform.getSession("agt_test", session_id);
  assert.equal(after.archived, true);
  assert.notEqual(after.run_status, "running");
  // A retry after a lost response or a failed local update succeeds.
  await archiveOnPlatform(platform, "agt_test", session_id, { pollMs: 10 });
  await assert.rejects(
    platform.postEvents("agt_test", session_id, [{ type: "user.message", content: "more" }]),
    (e: unknown) => (e as { status?: number }).status === 409,
  );
});
