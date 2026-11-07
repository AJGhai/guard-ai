// One conversation in the browser: its event stream, the messages sent from
// this page that the stream has not echoed yet, and the stop and approval
// actions. React reads it through subscribe/getSnapshot.

import { api, ApiError } from "./api";
import { describe } from "./notices";
import {
  applyEvents,
  emptyView,
  pendingApprovals,
  settleOutgoing,
  type ApprovalDecision,
  type OutgoingMessage,
  type SessionView,
} from "./reducer";
import type { RelayEnvelope, WireEvent } from "./wire";

export type Connection = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export interface SessionSnapshot {
  view: SessionView;
  /** Sent from this page and not yet echoed by the stream. */
  outgoing: readonly OutgoingMessage[];
  connection: Connection;
  /** The stream has not delivered the Session's history yet. */
  loading: boolean;
  /**
   * The history replay is still arriving: true from a fresh connect until the
   * stream has been quiet for a moment. The thread keeps to the latest
   * message meanwhile.
   */
  replaying: boolean;
  /** A message was accepted and its run has not started yet. */
  awaitingRun: boolean;
  /** A stop was requested and the run has not ended yet. */
  stopping: boolean;
  /** Approvals with a decision on the way. */
  deciding: Readonly<Record<string, ApprovalDecision>>;
  /** Why the last action or the stream failed. */
  notice: string | null;
}

export const initialSnapshot: SessionSnapshot = {
  view: emptyView(),
  outgoing: [],
  connection: "idle",
  loading: true,
  replaying: false,
  awaitingRun: false,
  stopping: false,
  deciding: {},
  notice: null,
};

/** A run that has not started this long after its message was accepted is reported. */
const START_WARNING_MS = 30_000;
/** An empty Session sends no history; stop showing it as loading after this long. */
const EMPTY_HISTORY_MS = 800;
/** The replay counts as done once the stream has been quiet this long. */
const REPLAY_SETTLE_MS = 400;
const FLUSH_MS = 30;
const RETRY_MAX_MS = 15_000;

const NOT_STARTED =
  "The message was accepted, but the Agent has not started. Run npm run status; if the Agent is stopped, run npm run setup.";

const newKey = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;

export class SessionController {
  private snapshot: SessionSnapshot = initialSnapshot;
  private readonly listeners = new Set<() => void>();
  private source: EventSource | null = null;
  private wanted = false;
  /** The last Platform cursor received; a reopened stream resumes after it. */
  private cursor: string | undefined;
  private queue: WireEvent[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | undefined;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private loadingTimer: ReturnType<typeof setTimeout> | undefined;
  private startTimer: ReturnType<typeof setTimeout> | undefined;
  private settleTimer: ReturnType<typeof setTimeout> | undefined;
  private retries = 0;
  /** The conversation is on screen. When it is not, the stream stays open only while a run is in flight. */
  private foreground = false;
  /** A send that failed. Sending the same text again reuses its key, so it is delivered at most once. */
  private failedSend: { text: string; key: string } | null = null;
  /** Kept across a failed interrupt so the retry is the same request. */
  private stopKey: string | null = null;
  /** Assistant turns after this sequence number belong to the last accepted message. */
  private sentAfterSeq = 0;

  constructor(readonly sessionId: string) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  private update(patch: Partial<SessionSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
    // A conversation in the background stops following once its run is over.
    if (this.wanted && !this.foreground && !isBusy(this.snapshot)) this.close();
  }

  /** Whether the stream is followed (on screen, or in the background while a run is in flight). */
  get following(): boolean {
    return this.wanted;
  }

  /** Start (or resume) following the Session, as the conversation on screen. */
  open() {
    this.foreground = true;
    this.wanted = true;
    if (!this.source && !this.retryTimer) this.connect();
  }

  /**
   * The conversation left the screen. A run in flight keeps the stream open,
   * so the sidebar still shows it working, until the run ends; otherwise the
   * stream closes now.
   */
  release() {
    this.foreground = false;
    if (!isBusy(this.snapshot)) this.close();
  }

  /** Stop following. The Agent keeps working; open() resumes from the last cursor. */
  close() {
    this.wanted = false;
    this.foreground = false;
    this.source?.close();
    this.source = null;
    clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
    clearTimeout(this.loadingTimer);
    clearTimeout(this.settleTimer);
    this.flush();
    this.update({ connection: "idle", replaying: false });
  }

  /** Mark the replay done once no event has arrived for a moment. */
  private settleLater() {
    if (!this.snapshot.replaying) return;
    clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => this.update({ replaying: false }), REPLAY_SETTLE_MS);
  }

  private connect() {
    this.retryTimer = undefined;
    const source = new EventSource(api.streamUrl(this.sessionId, this.cursor));
    this.source = source;
    // Without a cursor the stream replays the whole history first.
    this.update({ connection: this.cursor ? "reconnecting" : "connecting", ...(this.cursor ? {} : { replaying: true }) });
    source.onopen = () => {
      this.retries = 0;
      this.update({ connection: "live" });
      this.settleLater();
      if (this.snapshot.loading) {
        clearTimeout(this.loadingTimer);
        this.loadingTimer = setTimeout(() => {
          if (this.snapshot.loading && !this.queue.length) this.update({ loading: false });
        }, EMPTY_HISTORY_MS);
      }
    };
    source.onmessage = (message) => this.receive(source, message);
    source.onerror = () => {
      if (source !== this.source) return;
      if (source.readyState === EventSource.CLOSED) {
        // The browser gave up (for example a non-SSE error response); retry with backoff.
        this.source = null;
        this.reconnectLater();
      } else {
        // EventSource reconnects by itself and sends the last id as Last-Event-ID.
        this.update({ connection: "reconnecting" });
      }
    };
  }

  private reconnectLater() {
    if (!this.wanted || this.retryTimer) return;
    const delay = Math.min(1_000 * 2 ** this.retries, RETRY_MAX_MS);
    this.retries += 1;
    this.update({ connection: "reconnecting" });
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  private receive(source: EventSource, message: MessageEvent<string>) {
    if (source !== this.source) return;
    let envelope: RelayEnvelope;
    try {
      envelope = JSON.parse(message.data) as RelayEnvelope;
    } catch {
      return;
    }
    if (envelope.kind === "event") {
      if (message.lastEventId) this.cursor = message.lastEventId;
      this.queue.push(envelope.event);
      this.flushTimer ??= setTimeout(() => this.flush(), FLUSH_MS);
      return;
    }
    source.close();
    this.source = null;
    this.flush();
    if (envelope.fatal) {
      this.update({
        connection: "closed",
        loading: false,
        replaying: false,
        notice: describe(envelope.code, "This conversation can no longer be loaded"),
      });
    } else {
      this.reconnectLater();
    }
  }

  /** Apply queued events in one update, so a long replay renders once. */
  private flush() {
    clearTimeout(this.flushTimer);
    this.flushTimer = undefined;
    if (!this.queue.length) return;
    const events = this.queue;
    this.queue = [];
    const before = this.snapshot.view;
    const view = applyEvents(before, events);
    const patch: Partial<SessionSnapshot> = { view, loading: false };
    patch.outgoing = settleOutgoing(this.snapshot.outgoing, view, before.lastSeq);

    if (
      this.snapshot.awaitingRun &&
      view.turns.some((t) => t.role === "assistant" && t.seq > this.sentAfterSeq)
    ) {
      patch.awaitingRun = false;
      clearTimeout(this.startTimer);
      if (this.snapshot.notice === NOT_STARTED) patch.notice = null;
    }
    if (before.activeRunId && !view.activeRunId) {
      patch.stopping = false;
      this.stopKey = null;
    }
    const pending = new Set(pendingApprovals(view).map((a) => a.approvalId));
    const deciding = Object.fromEntries(Object.entries(this.snapshot.deciding).filter(([id]) => pending.has(id)));
    if (Object.keys(deciding).length !== Object.keys(this.snapshot.deciding).length) patch.deciding = deciding;
    this.update(patch);
    this.settleLater();
  }

  /**
   * Post a message, shown at once as an outgoing bubble. Throws on failure
   * after setting the notice, so the composer can keep the text.
   */
  async send(text: string): Promise<void> {
    const key = this.failedSend?.text === text ? this.failedSend.key : newKey("msg");
    const baseSeq = this.snapshot.view.lastSeq;
    this.update({ outgoing: [...this.snapshot.outgoing, { key, text }], notice: null });
    let receipt: { seq?: number };
    try {
      receipt = await api.sendMessage(this.sessionId, text, key);
    } catch (error) {
      this.failedSend = { text, key };
      this.update({
        outgoing: this.snapshot.outgoing.filter((m) => m.key !== key),
        notice: error instanceof ApiError ? error.message : "Your message was not sent.",
      });
      throw error;
    }
    this.failedSend = null;
    this.sentAfterSeq = receipt.seq ?? baseSeq;
    const { view } = this.snapshot;
    const started = view.turns.some((t) => t.role === "assistant" && t.seq > this.sentAfterSeq);
    const echoed = receipt.seq !== undefined && view.lastSeq >= receipt.seq;
    this.update({
      awaitingRun: !started && !view.activeRunId,
      ...(echoed ? { outgoing: this.snapshot.outgoing.filter((m) => m.key !== key) } : {}),
    });
    clearTimeout(this.startTimer);
    if (!started) {
      this.startTimer = setTimeout(() => {
        if (this.snapshot.awaitingRun) this.update({ notice: NOT_STARTED });
      }, START_WARNING_MS);
    }
  }

  /** Interrupt the run on the Platform. Closing the page does not. */
  async interrupt(): Promise<void> {
    if (this.snapshot.stopping) return;
    this.stopKey ??= newKey("stop");
    this.update({ stopping: true, notice: null });
    try {
      const { accepted } = await api.interrupt(this.sessionId, this.stopKey);
      this.stopKey = null;
      if (!accepted) this.update({ stopping: false, awaitingRun: false });
    } catch (error) {
      this.update({ stopping: false, notice: error instanceof ApiError ? error.message : "Could not stop the reply." });
    }
  }

  /** Send an approval decision. The approval stays pending on screen until the stream reports it resolved. */
  async decide(approvalId: string, decision: ApprovalDecision): Promise<void> {
    if (this.snapshot.deciding[approvalId]) return;
    this.update({ deciding: { ...this.snapshot.deciding, [approvalId]: decision }, notice: null });
    try {
      await api.resolveApproval(this.sessionId, approvalId, decision);
    } catch (error) {
      const { [approvalId]: _, ...rest } = this.snapshot.deciding;
      this.update({
        deciding: rest,
        notice: error instanceof ApiError ? error.message : "Could not send the decision.",
      });
    }
  }

  dismissNotice() {
    if (this.snapshot.notice) this.update({ notice: null });
  }

  dispose() {
    this.close();
    clearTimeout(this.startTimer);
    this.listeners.clear();
  }
}

/** Whether the composer should treat the conversation as busy. */
export const isBusy = (s: SessionSnapshot) => !!s.view.activeRunId || s.awaitingRun || s.outgoing.length > 0;
