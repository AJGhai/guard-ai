// A pure fold from Session events to what the chat shows. The same function
// handles the history replay and live events, and it ignores any event at or
// below the last sequence number it applied, so a replay that overlaps what
// is already on screen changes nothing.

import type { ThreadMessageLike } from "@assistant-ui/react";
import type { WireEvent } from "./wire";

export type ApprovalDecision = "allow-once" | "allow-always" | "deny";

export type ToolPhase = "running" | "awaiting_approval" | "succeeded" | "failed" | "blocked";

export interface ToolActivity {
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  phase: ToolPhase;
  /** The Platform's result preview: a string cut at a fixed length. */
  resultPreview?: string;
  /** Why a blocked call did not run, such as `approval-denied` or `interrupted`. */
  blockedReason?: string;
  /** The approval this call waits or waited for. */
  approvalId?: string;
}

export interface Approval {
  approvalId: string;
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  /** `closed` means the wait ended without a resolution event (timeout, interrupt, run end). */
  state: "pending" | "resolved" | "closed";
  resolution?: string;
  timeoutAt?: string;
}

export type Part = { type: "text"; text: string } | { type: "reasoning"; text: string } | { type: "tool"; toolCallId: string };

export type RunStatus = "running" | "succeeded" | "failed" | "aborted";

export type UserTurn = { role: "user"; id: string; seq: number; text: string; createdAt?: string };

export type RunTurn = {
  role: "assistant";
  id: string;
  seq: number;
  runId: string;
  parts: Part[];
  status: RunStatus;
  /** The last `agent.error` message of the run, shown if the run fails. */
  error?: string;
  /** The run ended while work it started continues (`turnEndReason: "yielded"`). */
  yielded?: boolean;
  createdAt?: string;
};

export type Turn = UserTurn | RunTurn;

export interface SessionView {
  /** Highest sequence number applied. */
  lastSeq: number;
  turns: Turn[];
  tools: Record<string, ToolActivity>;
  approvals: Record<string, Approval>;
  /** The run in flight, if any. */
  activeRunId: string | null;
}

export const emptyView = (): SessionView => ({ lastSeq: 0, turns: [], tools: {}, approvals: {}, activeRunId: null });

const ERROR_MAX = 500;

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

const str = (value: unknown): string | undefined => (typeof value === "string" && value ? value : undefined);

/** Text of a content value: a string, or an array of blocks of which only text blocks count. */
export function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => (isObject(block) && block.type === "text" && typeof block.text === "string" ? block.text : ""))
    .join("");
}

const ENDED: ReadonlySet<ToolPhase> = new Set(["succeeded", "failed", "blocked"]);

/** Applies events to a copy of a view. Objects it has copied once are then changed in place. */
class Fold {
  readonly view: SessionView;
  private copied = new Set<object>();

  constructor(base: SessionView) {
    this.view = {
      ...base,
      turns: [...base.turns],
      tools: { ...base.tools },
      approvals: { ...base.approvals },
    };
  }

  private own<T extends object>(value: T, replace: (copy: T) => void): T {
    if (this.copied.has(value)) return value;
    const copy = Array.isArray(value) ? ([...value] as T) : { ...value };
    this.copied.add(copy);
    replace(copy);
    return copy;
  }

  /**
   * The turn of the event's run, created on first use. An event outside any
   * run (no run id, nothing in flight) gets a finished turn of its own.
   */
  private run(runId: string | undefined, event: WireEvent): RunTurn {
    const id = runId ?? this.view.activeRunId ?? `seq-${event.seq}`;
    const index = this.view.turns.findLastIndex((t) => t.role === "assistant" && t.runId === id);
    if (index === -1) {
      const standalone = !runId && !this.view.activeRunId;
      const turn: RunTurn = {
        role: "assistant",
        id: `run-${id}`,
        seq: event.seq,
        runId: id,
        parts: [],
        status: standalone ? "succeeded" : "running",
        ...(event.createdAt ? { createdAt: event.createdAt } : {}),
      };
      this.copied.add(turn);
      this.copied.add(turn.parts);
      this.view.turns.push(turn);
      if (!standalone && !this.view.activeRunId) this.view.activeRunId = id;
      return turn;
    }
    const turn = this.own(this.view.turns[index] as RunTurn, (copy) => (this.view.turns[index] = copy));
    turn.parts = this.own(turn.parts, (copy) => (turn.parts = copy));
    return turn;
  }

  private tool(toolCallId: string, defaults: { toolName?: string; args?: Record<string, unknown> }): ToolActivity {
    const existing = this.view.tools[toolCallId];
    if (!existing) {
      const created: ToolActivity = {
        toolCallId,
        toolName: defaults.toolName ?? "tool",
        args: defaults.args ?? {},
        phase: "running",
      };
      this.copied.add(created);
      this.view.tools[toolCallId] = created;
      return created;
    }
    return this.own(existing, (copy) => (this.view.tools[toolCallId] = copy));
  }

  private approval(approvalId: string): Approval | undefined {
    const existing = this.view.approvals[approvalId];
    return existing && this.own(existing, (copy) => (this.view.approvals[approvalId] = copy));
  }

  private attach(turn: RunTurn, toolCallId: string) {
    if (!turn.parts.some((p) => p.type === "tool" && p.toolCallId === toolCallId)) {
      turn.parts.push({ type: "tool", toolCallId });
    }
  }

  private closeApproval(approvalId: string | undefined) {
    if (!approvalId) return;
    const approval = this.approval(approvalId);
    if (approval?.state === "pending") approval.state = "closed";
  }

  apply(event: WireEvent) {
    const p = isObject(event.payload) ? event.payload : {};
    switch (event.eventType) {
      case "user.message":
        this.view.turns.push({
          role: "user",
          id: `user-${event.seq}`,
          seq: event.seq,
          text: contentText(p.content),
          ...(event.createdAt ? { createdAt: event.createdAt } : {}),
        });
        break;

      case "run.started": {
        const turn = this.run(event.runId, event);
        this.view.activeRunId = turn.runId;
        break;
      }

      case "agent.thinking": {
        const text = str(p.text)?.trim() ? (p.text as string) : undefined;
        if (text) this.run(event.runId, event).parts.push({ type: "reasoning", text });
        break;
      }

      case "agent.assistant": {
        const text = contentText(isObject(p.message) ? p.message.content : undefined);
        if (text.trim()) this.run(event.runId, event).parts.push({ type: "text", text });
        break;
      }

      case "agent.tool": {
        const toolCallId = str(p.toolCallId);
        if (!toolCallId) break;
        const args = isObject(p.args) ? p.args : undefined;
        const tool = this.tool(toolCallId, { toolName: str(p.toolName), ...(args ? { args } : {}) });
        if (str(p.toolName)) tool.toolName = p.toolName as string;
        if (args) tool.args = args;
        if (p.phase === "end") {
          tool.phase = p.isError === true ? "failed" : "succeeded";
          const preview = str(p.resultPreview);
          if (preview) tool.resultPreview = preview;
        } else if (p.phase === "blocked") {
          tool.phase = "blocked";
          tool.blockedReason = str(p.deniedReason) ?? str(p.policyId) ?? "blocked";
          this.closeApproval(tool.approvalId);
        } else if (!ENDED.has(tool.phase)) {
          const waiting = tool.approvalId && this.view.approvals[tool.approvalId]?.state === "pending";
          tool.phase = waiting ? "awaiting_approval" : "running";
        }
        this.attach(this.run(event.runId, event), toolCallId);
        break;
      }

      case "agent.approval": {
        const approvalId = str(p.approvalId);
        if (!approvalId) break;
        if (p.phase === "resolved") {
          const approval = this.approval(approvalId);
          if (approval) {
            approval.state = "resolved";
            const resolution = str(p.resolution);
            if (resolution) approval.resolution = resolution;
          }
          const toolCallId = approval?.toolCallId ?? str(p.toolCallId);
          const tool = toolCallId ? this.view.tools[toolCallId] : undefined;
          if (tool?.phase === "awaiting_approval") this.tool(tool.toolCallId, {}).phase = "running";
          break;
        }
        const toolCallId = str(p.toolCallId) ?? `approval-${approvalId}`;
        const args = isObject(p.arguments) ? p.arguments : {};
        const toolName = str(p.toolName) ?? this.view.tools[toolCallId]?.toolName ?? "tool";
        this.view.approvals[approvalId] = {
          approvalId,
          toolCallId,
          toolName,
          args,
          state: "pending",
          ...(str(p.timeoutAt) ? { timeoutAt: p.timeoutAt as string } : {}),
        };
        const tool = this.tool(toolCallId, { toolName, args });
        tool.approvalId = approvalId;
        if (!ENDED.has(tool.phase)) tool.phase = "awaiting_approval";
        this.attach(this.run(event.runId, event), toolCallId);
        break;
      }

      case "agent.error": {
        const message = str(p.errorMessage);
        if (message && (event.runId || this.view.activeRunId)) {
          this.run(event.runId, event).error = message.slice(0, ERROR_MAX);
        }
        break;
      }

      case "run.finished": {
        const turn = this.run(event.runId, event);
        const status = p.status;
        turn.status = status === "failed" || status === "aborted" ? status : "succeeded";
        turn.yielded = p.turnEndReason === "yielded";
        for (const part of turn.parts) {
          if (part.type !== "tool") continue;
          const current = this.view.tools[part.toolCallId];
          if (!current) continue;
          this.closeApproval(current.approvalId);
          if (ENDED.has(current.phase) || turn.yielded) continue;
          const tool = this.tool(part.toolCallId, {});
          tool.phase = "blocked";
          tool.blockedReason = turn.status === "aborted" ? "interrupted" : "unfinished";
        }
        if (this.view.activeRunId === turn.runId) this.view.activeRunId = null;
        break;
      }

      default:
        // Other events (lifecycle markers, inputs echoed back, ...) do not change the chat.
        break;
    }
    this.view.lastSeq = event.seq;
  }
}

/** Apply events in order. Events at or below `view.lastSeq` are skipped; with none left, the same view is returned. */
export function applyEvents(view: SessionView, events: readonly WireEvent[]): SessionView {
  let fold: Fold | undefined;
  for (const event of events) {
    if (!Number.isFinite(event.seq) || event.seq <= (fold?.view ?? view).lastSeq) continue;
    fold ??= new Fold(view);
    fold.apply(event);
  }
  return fold?.view ?? view;
}

export const applyEvent = (view: SessionView, event: WireEvent): SessionView => applyEvents(view, [event]);

export const foldEvents = (events: readonly WireEvent[]): SessionView => applyEvents(emptyView(), events);

/** Pending approvals, oldest first. */
export function pendingApprovals(view: SessionView): Approval[] {
  return Object.values(view.approvals).filter((a) => a.state === "pending");
}

/** A message the user sent that the event stream has not echoed yet. */
export type OutgoingMessage = { key: string; text: string };

/**
 * Drop outgoing messages that the stream has echoed: the first outgoing
 * message with the same text as each user turn after `sinceSeq`.
 */
export function settleOutgoing(
  outgoing: readonly OutgoingMessage[],
  view: SessionView,
  sinceSeq: number,
): readonly OutgoingMessage[] {
  if (!outgoing.length) return outgoing;
  const left = [...outgoing];
  for (const turn of view.turns) {
    if (turn.role !== "user" || turn.seq <= sinceSeq) continue;
    const index = left.findIndex((m) => m.text.trim() === turn.text.trim());
    if (index !== -1) left.splice(index, 1);
  }
  return left.length === outgoing.length ? outgoing : left;
}

const toolResult = (tool: ToolActivity) =>
  tool.phase === "blocked" ? { blocked: tool.blockedReason ?? "blocked" } : (tool.resultPreview ?? "");

function runStatus(turn: RunTurn, view: SessionView): NonNullable<ThreadMessageLike["status"]> {
  switch (turn.status) {
    case "running": {
      const waiting = turn.parts.some((p) => p.type === "tool" && view.tools[p.toolCallId]?.phase === "awaiting_approval");
      return waiting ? { type: "requires-action", reason: "tool-calls" } : { type: "running" };
    }
    case "failed":
      return { type: "incomplete", reason: "error", error: turn.error ?? "The run failed." };
    case "aborted":
      return { type: "incomplete", reason: "cancelled" };
    default:
      return { type: "complete", reason: "stop" };
  }
}

/** The view and any not-yet-echoed outgoing messages as assistant-ui messages. */
export function toThreadMessages(view: SessionView, outgoing: readonly OutgoingMessage[] = []): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = view.turns.map((turn) => {
    const createdAt = turn.createdAt ? new Date(turn.createdAt) : undefined;
    if (turn.role === "user") {
      return { id: turn.id, role: "user", content: [{ type: "text", text: turn.text }], ...(createdAt ? { createdAt } : {}) };
    }
    const content = turn.parts.map((part) => {
      if (part.type !== "tool") return part;
      const tool = view.tools[part.toolCallId]!;
      const ended = ENDED.has(tool.phase);
      return {
        type: "tool-call" as const,
        toolCallId: tool.toolCallId,
        toolName: tool.toolName,
        args: tool.args as NonNullable<Extract<ThreadMessageLike["content"][number], { type: "tool-call" }>["args"]>,
        argsText: JSON.stringify(tool.args, null, 2),
        ...(ended ? { result: toolResult(tool), isError: tool.phase !== "succeeded" } : {}),
        artifact: tool,
      };
    });
    return {
      id: turn.id,
      role: "assistant",
      content,
      status: runStatus(turn, view),
      metadata: { runId: turn.runId },
      ...(createdAt ? { createdAt } : {}),
    };
  });
  for (const message of outgoing) {
    messages.push({ id: `outgoing-${message.key}`, role: "user", content: [{ type: "text", text: message.text }] });
  }
  return messages;
}
