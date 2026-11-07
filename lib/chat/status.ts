// What the status pill in the header says. Kept free of React so the rules
// can be tested directly.
//
// The pill answers two questions: is the Agent ready (from /api/status),
// and, when it is, what the open conversation is doing. A single failed
// status check on a flaky connection does not turn the page red: a failure
// that may pass (a network error, a Platform timeout) shows "Checking…" and
// is checked again soon; only a second failure in a row is shown.

import { describe, isTransientCode } from "./notices";
import { pendingApprovals } from "./reducer";
import { isBusy, type SessionSnapshot } from "./session-controller";
import type { AppStatus } from "./wire";

/** Transient failures in a row before the pill reports them. */
export const CONFIRM_FAILURES = 2;

export type StatusCheck = {
  /** The last result shown; null while checking. */
  shown: { status: AppStatus } | { serverError: string } | null;
  /** Transient failures in a row. */
  failures: number;
};

export const initialCheck: StatusCheck = { shown: null, failures: 0 };

/** One status poll: the server's answer, or why /api/status could not be read. */
export type StatusPoll = { status: AppStatus } | { error: { code: string; message: string } };

/**
 * Fold one poll into the check. Returns the new check and whether to poll
 * again soon instead of at the normal interval (an unconfirmed failure).
 */
export function recordPoll(check: StatusCheck, poll: StatusPoll): { check: StatusCheck; recheckSoon: boolean } {
  const code = "status" in poll ? (poll.status.agent === "unavailable" ? poll.status.code : undefined) : poll.error.code;
  const transient = isTransientCode(code);
  const failures = transient ? check.failures + 1 : 0;
  if (transient && failures < CONFIRM_FAILURES) {
    return { check: { shown: null, failures }, recheckSoon: true };
  }
  const shown = "status" in poll ? { status: poll.status } : { serverError: poll.error.message };
  return { check: { shown, failures }, recheckSoon: false };
}

export type Tone = "ok" | "warn" | "error" | "idle";

export type Pill = {
  tone: Tone;
  label: string;
  /** A banner under the header saying what to do. */
  detail?: string;
  /** The conversation is working; the dot pulses. */
  active?: boolean;
};

/** The Agent's readiness. */
export function agentPill(check: StatusCheck): Pill {
  const { shown } = check;
  if (!shown) return { tone: "idle", label: "Checking…" };
  if ("serverError" in shown) return { tone: "error", label: "Server unreachable", detail: shown.serverError };
  const { status } = shown;
  switch (status.agent) {
    case "ready":
      return { tone: "ok", label: "Agent ready" };
    case "definition_changed":
      return {
        tone: "warn",
        label: "Agent out of date",
        detail: "The Agent on ZooWork Platform does not match agent/agent.md. Run npm run setup.",
      };
    case "not_running":
      return { tone: "warn", label: "Agent stopped", detail: "The Agent is not running. Run npm run setup." };
    default:
      return { tone: "error", label: "Agent unavailable", detail: describe(status.code, "The Agent is unavailable") };
  }
}

export type Activity = "idle" | "starting" | "working" | "approval" | "stopping";

/** What the open conversation is doing. `starting` is a first message whose Session is being created. */
export function activityOf(snapshot: SessionSnapshot, starting: boolean): Activity {
  if (starting) return "starting";
  if (snapshot.stopping) return "stopping";
  if (pendingApprovals(snapshot.view).length > 0) return "approval";
  if (isBusy(snapshot)) return "working";
  return "idle";
}

/**
 * The pill: an Agent problem first, otherwise the open conversation's state
 * when it has one, otherwise "Agent ready".
 */
export function statusPill(agent: Pill, activity: Activity): Pill {
  if (agent.tone !== "ok") return agent;
  switch (activity) {
    case "approval":
      return { tone: "warn", label: "Waiting for your approval" };
    case "stopping":
      return { tone: "idle", label: "Stopping…", active: true };
    case "starting":
    case "working":
      return { tone: "ok", label: "Working…", active: true };
    default:
      return agent;
  }
}
