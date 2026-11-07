// How a tool call that ended without running is labelled. The Platform
// reports why in `agent.tool` phase `blocked` as `deniedReason` (docs:
// Build → Events and streaming, tool call phases).

const LABEL: Record<string, string> = {
  "approval-denied": "Denied",
  "approval-timeout": "Not approved in time",
  "approval-cancelled": "Approval cancelled",
  interrupted: "Stopped",
  unfinished: "Not finished",
};

const DETAIL: Record<string, string> = {
  "approval-denied": "Not run: the call was denied.",
  "approval-timeout": "Not run: the approval request timed out.",
  "approval-cancelled": "Not run: the approval request was cancelled.",
  interrupted: "Not run: the reply was stopped.",
  unfinished: "The run ended before this call returned.",
};

/** The card title's verb, as in "Denied: web_fetch". A policy block or an unknown reason is "Blocked". */
export const blockedLabel = (reason: string | undefined) => (reason && LABEL[reason]) || "Blocked";

/** The sentence inside the card. */
export const blockedDetail = (reason: string | undefined) =>
  (reason && DETAIL[reason]) || `Not run (${reason ?? "blocked"}).`;
