import assert from "node:assert/strict";
import { test } from "node:test";
import { describe } from "../lib/chat/notices";
import { foldEvents } from "../lib/chat/reducer";
import { initialSnapshot } from "../lib/chat/session-controller";
import {
  activityOf,
  agentPill,
  initialCheck,
  recordPoll,
  statusPill,
  type StatusCheck,
  type StatusPoll,
} from "../lib/chat/status";
import type { WireEvent } from "../lib/chat/wire";

const ready: StatusPoll = { status: { agent: "ready" } };
const platformDown: StatusPoll = { status: { agent: "unavailable", code: "request_failed" } };
const serverDown: StatusPoll = { error: { code: "network_error", message: "Could not reach the app server." } };

function run(polls: StatusPoll[]): { check: StatusCheck; recheckSoon: boolean }[] {
  let check = initialCheck;
  return polls.map((poll) => {
    const result = recordPoll(check, poll);
    check = result.check;
    return result;
  });
}

test("one failed status check shows Checking… and asks for a quick recheck; a second one is shown", () => {
  for (const failure of [platformDown, serverDown]) {
    const [first, second] = run([ready, failure, failure]).slice(1);
    assert.equal(agentPill(first!.check).label, "Checking…");
    assert.equal(agentPill(first!.check).tone, "idle");
    assert.equal(first!.recheckSoon, true);
    assert.equal(agentPill(second!.check).tone, "error");
    assert.equal(second!.recheckSoon, false);
  }
});

test("a failure followed by a success never turns the pill red", () => {
  const results = run([ready, platformDown, ready]);
  assert.deepEqual(
    results.map((r) => agentPill(r.check).label),
    ["Agent ready", "Checking…", "Agent ready"],
  );
  // The count starts over after a success.
  assert.equal(agentPill(run([platformDown, ready, platformDown]).at(-1)!.check).label, "Checking…");
});

test("a failure that will not pass by itself is shown at once", () => {
  for (const code of ["run_setup_first", "platform_project_key_required", "http_401"]) {
    const [result] = run([{ status: { agent: "unavailable", code } }]);
    assert.equal(agentPill(result!.check).tone, "error");
    assert.equal(result!.recheckSoon, false);
  }
  assert.equal(agentPill(run([{ status: { agent: "definition_changed" } }])[0]!.check).label, "Agent out of date");
});

test("an unreachable Platform is explained, with the code", () => {
  const pill = agentPill(run([platformDown, platformDown]).at(-1)!.check);
  assert.match(pill.detail!, /could not reach ZooWork Platform/);
  assert.match(pill.detail!, /try again/);
  assert.match(pill.detail!, /request_failed/);
  assert.match(describe("timeout_or_cancelled", "Could not archive the conversation"), /^Could not archive the conversation: .*did not answer in time/);
  assert.match(describe("http_504", "Your message was not sent"), /temporarily|did not answer/);
});

const event = (seq: number, eventType: string, payload: Record<string, unknown> = {}, runId?: string): WireEvent => ({
  seq,
  eventType,
  payload,
  ...(runId ? { runId } : {}),
});

test("while the Agent is ready, the pill shows what the open conversation is doing", () => {
  const ok = agentPill(run([ready])[0]!.check);
  const working = { ...initialSnapshot, view: foldEvents([event(1, "user.message", { content: "hi" }), event(2, "run.started", {}, "r1")]) };
  const waiting = {
    ...working,
    view: foldEvents([
      event(1, "user.message", { content: "hi" }),
      event(2, "run.started", {}, "r1"),
      event(3, "agent.approval", { phase: "requested", approvalId: "apr_1", toolCallId: "c1", toolName: "web_fetch" }, "r1"),
    ]),
  };
  assert.equal(statusPill(ok, activityOf(initialSnapshot, false)).label, "Agent ready");
  assert.equal(statusPill(ok, activityOf(initialSnapshot, true)).label, "Working…");
  assert.equal(statusPill(ok, activityOf(working, false)).label, "Working…");
  assert.equal(statusPill(ok, activityOf(waiting, false)).label, "Waiting for your approval");
  assert.equal(statusPill(ok, activityOf({ ...working, stopping: true }, false)).label, "Stopping…");
  // An Agent problem comes first.
  const stopped = agentPill(run([{ status: { agent: "not_running" } }])[0]!.check);
  assert.equal(statusPill(stopped, activityOf(working, false)).label, "Agent stopped");
});
