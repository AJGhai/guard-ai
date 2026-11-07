import { randomUUID } from "node:crypto";
import {
  ZooworkError,
  type ApprovalDecision,
  type ApprovalRecord,
  type OutboundEvent,
  type PostEventReceipt,
  type SessionEvent,
  type SessionRecord,
} from "@zoowork-ai/sdk";
import type { ChatClient } from "../lib/zoowork/runtime";

/**
 * An in-memory fake of the Platform Session API, for the offline tests only.
 * Application code must never import it. It emits the same event shapes the
 * real stream does (`user.message` echo, `run.started`, `agent.tool`,
 * `agent.assistant`, `run.finished`, ...) on the same cursor contract, so the
 * relay, archive and Session-creation code run unchanged. Replies are
 * scripted; no model is called.
 *
 * A message containing "approval" makes the tool call wait for a decision.
 */
export interface FakePlatformOptions {
  /** Delay between scripted events, in milliseconds. */
  stepMs?: number;
}

interface FakeSession {
  id: string;
  metadata: Record<string, unknown>;
  events: SessionEvent[];
  archived: boolean;
  deleted: boolean;
  run?: { id: string; aborted: boolean; approval?: PendingApproval };
  keys: Map<string, PostEventReceipt>;
}

interface PendingApproval {
  record: ApprovalRecord;
  resolve: (decision: ApprovalDecision | "interrupted") => void;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createFakePlatform(options: FakePlatformOptions = {}): ChatClient & { sessionCount(): number } {
  const stepMs = options.stepMs ?? 450;
  const sessions = new Map<string, FakeSession>();
  const waiters = new Map<string, Set<() => void>>();
  const idempotentCreates = new Map<string, string>();
  let seq = 0;

  const wake = (id: string) => {
    for (const fn of waiters.get(id) ?? []) fn();
  };

  function session(agentId: string, sessionId: string): FakeSession {
    const s = sessions.get(sessionId);
    if (!s || s.deleted || !agentId) throw new ZooworkError(404, "session not found", "session_not_found");
    return s;
  }

  function emit(s: FakeSession, eventType: string, payload: Record<string, unknown>, extra: Partial<SessionEvent> = {}) {
    seq += 1;
    const event: SessionEvent = {
      seq,
      eventType,
      payload,
      createdAt: new Date().toISOString(),
      id: `pev_${seq}`,
      cursor: `pse1:${seq}`,
      ...(s.run && !eventType.startsWith("user.") ? { runId: s.run.id, turn: 1 } : {}),
      ...extra,
    };
    s.events.push(event);
    wake(s.id);
    return event;
  }

  async function step(s: FakeSession, run: NonNullable<FakeSession["run"]>) {
    await sleep(stepMs);
    return !run.aborted && !s.deleted;
  }

  async function runTurn(s: FakeSession, text: string) {
    const run = { id: `run_${randomUUID().slice(0, 8)}`, aborted: false } as NonNullable<FakeSession["run"]>;
    s.run = run;
    emit(s, "run.started", { trigger: "user", agentId: "agt_test" });
    let outcome: "succeeded" | "aborted" = "succeeded";
    try {
      if (!(await step(s, run))) return (outcome = "aborted");
      emit(s, "agent.thinking", { text: "" });

      const wantsApproval = /approv/i.test(text);
      const toolCallId = `call_${randomUUID().slice(0, 8)}`;
      const toolName = wantsApproval ? "web_fetch" : "web_search";
      const args = wantsApproval ? { url: "https://example.com/" } : { query: text.slice(0, 80) };
      if (!(await step(s, run))) return (outcome = "aborted");
      emit(s, "agent.tool", { phase: "start", toolName, toolCallId, args });

      let toolRan = true;
      if (wantsApproval) {
        const approvalId = `apr_${randomUUID().slice(0, 8)}`;
        const decision = await new Promise<ApprovalDecision | "interrupted">((resolve) => {
          run.approval = {
            resolve,
            record: {
              approval_id: approvalId,
              session_id: s.id,
              tool_call_id: toolCallId,
              tool_name: toolName,
              status: "pending",
              allowed_decisions: ["allow-once", "deny"],
            },
          };
          emit(s, "agent.approval", { phase: "requested", approvalId, toolCallId, toolName, arguments: args });
        });
        delete run.approval;
        if (decision === "interrupted") {
          emit(s, "agent.tool", { phase: "blocked", toolName, toolCallId, deniedReason: "interrupted" });
          return (outcome = "aborted");
        }
        emit(s, "agent.approval", { phase: "resolved", approvalId, toolCallId, resolution: decision });
        if (decision === "deny") {
          toolRan = false;
          emit(s, "agent.tool", { phase: "blocked", toolName, toolCallId, deniedReason: "approval-denied" });
        }
      }
      if (toolRan) {
        if (!(await step(s, run))) return (outcome = "aborted");
        emit(s, "agent.tool", {
          phase: "end",
          toolName,
          toolCallId,
          isError: false,
          resultPreview: "Scripted tool output.",
        });
      }

      if (!(await step(s, run))) return (outcome = "aborted");
      const reply = `Scripted reply to: "${text.length > 200 ? `${text.slice(0, 199)}…` : text}"`;
      emit(s, "agent.assistant", { segment: 1, message: { role: "assistant", content: [{ type: "text", text: reply }] } });
      return outcome;
    } finally {
      emit(s, "run.finished", { status: run.aborted ? "aborted" : outcome });
      if (s.run === run) delete s.run;
    }
  }

  return {
    sessionCount: () => sessions.size,

    async createSession(_agentId, input, idempotencyKey) {
      const existing = idempotencyKey ? idempotentCreates.get(idempotencyKey) : undefined;
      if (existing) return { session_id: existing, status: "running", metadata: sessions.get(existing)!.metadata };
      const id = `ses_test_${randomUUID().replace(/-/g, "").slice(0, 16)}`;
      sessions.set(id, { id, metadata: { ...input.metadata }, events: [], archived: false, deleted: false, keys: new Map() });
      if (idempotencyKey) idempotentCreates.set(idempotencyKey, id);
      return { session_id: id, status: "running", metadata: { ...input.metadata } };
    },

    async getSession(agentId, sessionId): Promise<SessionRecord> {
      const s = session(agentId, sessionId);
      return {
        session_id: s.id,
        metadata: s.metadata,
        archived: s.archived,
        run_status: s.run ? (s.run.approval ? "awaiting_approval" : "running") : "succeeded",
      };
    },

    async postEvents(agentId, sessionId, events: OutboundEvent[]) {
      const s = session(agentId, sessionId);
      if (s.archived) throw new ZooworkError(409, "session archived", "session_archived");
      const receipts: PostEventReceipt[] = [];
      for (const input of events) {
        const key = typeof input.idempotency_key === "string" ? input.idempotency_key : undefined;
        if (key && s.keys.has(key)) {
          receipts.push(s.keys.get(key)!);
          continue;
        }
        if (input.type === "user.message") {
          if (typeof input.content !== "string" || !input.content.trim()) throw new ZooworkError(400, "content required", "invalid_request");
          if (s.run) throw new ZooworkError(409, "a run is in progress", "run_in_progress");
          const echo = emit(s, "user.message", { content: [{ type: "text", text: input.content }] }, { processedAt: new Date().toISOString() });
          const receipt: PostEventReceipt = { id: echo.id, type: "user.message", accepted: true, seq: echo.seq };
          if (key) s.keys.set(key, receipt);
          receipts.push(receipt);
          void runTurn(s, input.content);
        } else if (input.type === "user.interrupt") {
          if (!s.run) {
            receipts.push({ id: null, type: "user.interrupt", accepted: false });
            continue;
          }
          s.run.aborted = true;
          s.run.approval?.resolve("interrupted");
          const echo = emit(s, "user.interrupt", {});
          const receipt: PostEventReceipt = { id: echo.id, type: "user.interrupt", accepted: true, seq: echo.seq };
          if (key) s.keys.set(key, receipt);
          receipts.push(receipt);
        } else {
          throw new ZooworkError(400, "unsupported event type in the fake", "invalid_request");
        }
      }
      return { events: receipts };
    },

    async *streamEvents(agentId, sessionId, opts = {}) {
      const s = session(agentId, sessionId);
      const match = /^pse1:(\d+)$/.exec(opts.cursor ?? "");
      if (opts.cursor && !match) throw new ZooworkError(400, "invalid cursor", "invalid_request");
      let after = match ? Number(match[1]) : 0;
      const signal = opts.signal;
      while (!signal?.aborted) {
        if (s.deleted) return;
        const next = s.events.filter((e) => e.seq > after);
        for (const event of next) {
          after = event.seq;
          yield event;
          if (signal?.aborted) return;
        }
        await new Promise<void>((resolve) => {
          const set = waiters.get(s.id) ?? new Set();
          const done = () => {
            set.delete(done);
            signal?.removeEventListener("abort", done);
            resolve();
          };
          set.add(done);
          waiters.set(s.id, set);
          signal?.addEventListener("abort", done, { once: true });
        });
      }
    },

    async archiveSession(agentId, sessionId) {
      const s = session(agentId, sessionId);
      if (s.archived) throw new ZooworkError(409, "session archived", "session_archived");
      s.archived = true;
      return { session_id: s.id, archived: true };
    },

    async deleteSession(agentId, sessionId) {
      const s = session(agentId, sessionId);
      if (s.run) {
        s.run.aborted = true;
        s.run.approval?.resolve("interrupted");
      }
      s.deleted = true;
      wake(s.id);
    },

    async listApprovals(agentId, opts = {}) {
      const out: ApprovalRecord[] = [];
      for (const s of sessions.values()) {
        if (s.deleted || !agentId || (opts.sessionId && s.id !== opts.sessionId)) continue;
        if (s.run?.approval) out.push({ ...s.run.approval.record });
      }
      return out;
    },

    async resolveApproval(_agentId, approvalId, input) {
      for (const s of sessions.values()) {
        const pending = s.run?.approval;
        if (pending && pending.record.approval_id === approvalId) {
          pending.resolve(input.decision);
          return { ...pending.record, status: "resolved", decision: input.decision, signaled: true };
        }
      }
      throw new ZooworkError(404, "approval not found", "not_found");
    },
  };
}
