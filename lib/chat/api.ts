// The browser's client for this app's own /api routes. It never talks to the
// Platform and never sees the key.

import { describe } from "./notices";
import type { AppStatus, SessionSummary } from "./wire";

/** A failed API call: a stable code from the server and a message safe to show. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const UNREACHABLE = "Could not reach the app server. Check that npm run dev is still running and that this computer is online.";

async function call<T>(path: string, fallback: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: init.method ?? "GET",
      headers: init.body === undefined ? {} : { "content-type": "application/json" },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError("network_error", 0, UNREACHABLE);
  }
  if (response.status === 204) return undefined as T;
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const code =
      body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string"
        ? (body as { error: string }).error
        : `http_${response.status}`;
    throw new ApiError(code, response.status, describe(code, fallback));
  }
  return body as T;
}

const session = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;

export const api = {
  status: () => call<AppStatus>("/api/status", "Could not read the app status"),

  listSessions: () => call<SessionSummary[]>("/api/sessions", "Could not load the conversations"),

  createSession: (threadId: string, text: string) =>
    call<SessionSummary>("/api/sessions", "Could not start the conversation", {
      method: "POST",
      body: { threadId, text },
    }),

  renameSession: (id: string, title: string) =>
    call<SessionSummary>(session(id), "Could not rename the conversation", { method: "PATCH", body: { title } }),

  archiveSession: (id: string) =>
    call<SessionSummary>(`${session(id)}/archive`, "Could not archive the conversation", { method: "POST" }),

  deleteSession: (id: string) => call<void>(session(id), "Could not delete the conversation", { method: "DELETE" }),

  sendMessage: (id: string, text: string, idempotencyKey: string) =>
    call<{ accepted: true; seq?: number }>(`${session(id)}/messages`, "Your message was not sent", {
      method: "POST",
      body: { text, idempotencyKey },
    }),

  interrupt: (id: string, idempotencyKey: string) =>
    call<{ accepted: boolean }>(`${session(id)}/interrupt`, "Could not stop the reply", {
      method: "POST",
      body: { idempotencyKey },
    }),

  resolveApproval: (id: string, approvalId: string, decision: "allow-once" | "allow-always" | "deny") =>
    call<{ status: string; signaled: boolean }>(
      `${session(id)}/approvals/${encodeURIComponent(approvalId)}`,
      "Could not send the decision",
      { method: "POST", body: { decision } },
    ),

  /** The event stream URL. A cursor resumes after that event; without one the Session replays from the start. */
  streamUrl: (id: string, cursor?: string) =>
    `${session(id)}/stream${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
};
