// Types shared by the server routes and the browser. Type-only: nothing here
// pulls server code or the SDK into the browser bundle.

/** One durable Session event, as the stream route relays it to the browser. */
export type WireEvent = {
  /** Strictly increasing per Session; the browser deduplicates on it. */
  seq: number;
  eventType: string;
  payload: Record<string, unknown>;
  runId?: string;
  createdAt?: string;
  processedAt?: string | null;
};

/**
 * One SSE `data:` frame from `GET /api/sessions/[id]/stream`. Event frames also
 * carry the Platform resume cursor in the SSE `id:` line, so the browser's
 * automatic reconnect resumes exactly after the last event it received.
 */
export type RelayEnvelope =
  | { kind: "event"; event: WireEvent }
  /** The relay lost the upstream stream. `fatal` means reconnecting cannot help (e.g. the Session is gone). */
  | { kind: "relay_error"; code: string; fatal: boolean };

/** A row in the conversation list. */
export type SessionSummary = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  archived: boolean;
};

export type AgentHealth = "ready" | "definition_changed" | "not_running" | "unavailable";

export type AppStatus = {
  agent: AgentHealth;
  /** Safe error code when the Agent is unavailable (see the README troubleshooting table). */
  code?: string;
};

/** Message length limit enforced by the messages route. */
export const MESSAGE_MAX = 20_000;
