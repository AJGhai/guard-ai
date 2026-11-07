import assert from "node:assert/strict";
import { test } from "node:test";
import { readApiKey } from "../lib/zoowork/config";

// Next.js types NODE_ENV as required; these tests only need the ZooWork variables.
const read = (vars: Record<string, string | undefined>) => readApiKey(vars as NodeJS.ProcessEnv);

test("the Project key is the only setting", () => {
  assert.equal(read({ ZOOWORK_API_KEY: " zwp_live_test " }), "zwp_live_test");
});

test("the key must be a Platform Project key", () => {
  for (const key of [undefined, "", "zwp_live_", "zct_work_key"]) {
    assert.throws(() => read({ ZOOWORK_API_KEY: key }), /platform_project_key_required/);
  }
});
