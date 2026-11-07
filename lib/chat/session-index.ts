// The browser's copy of this app's conversation index (GET /api/sessions),
// plus the link between a new chat's local thread id and the Session created
// for it. A new chat keeps its local id after its Session exists, so its
// composer and notices stay with it.

import { api, ApiError } from "./api";
import type { SessionSummary } from "./wire";

export interface IndexSnapshot {
  sessions: readonly SessionSummary[];
  loading: boolean;
  error: string | null;
  /** Local thread id → Session id, for chats created on this page. */
  links: Readonly<Record<string, string>>;
}

const byRecent = (a: SessionSummary, b: SessionSummary) => b.updatedAt.localeCompare(a.updatedAt);

export class SessionIndex {
  private snapshot: IndexSnapshot = { sessions: [], loading: true, error: null, links: {} };
  private readonly listeners = new Set<() => void>();
  /** One create per thread at a time, and one server-side thread id per local thread. */
  private readonly creating = new Map<string, Promise<string>>();
  private readonly createIds = new Map<string, string>();

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  private update(patch: Partial<IndexSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  private replace(summary: SessionSummary) {
    const sessions = this.snapshot.sessions.filter((s) => s.id !== summary.id);
    this.update({ sessions: [summary, ...sessions].sort(byRecent) });
  }

  async load(): Promise<void> {
    this.update({ loading: true, error: null });
    try {
      const sessions = await api.listSessions();
      this.update({ sessions: [...sessions].sort(byRecent), loading: false });
    } catch (error) {
      this.update({ loading: false, error: error instanceof ApiError ? error.message : "Could not load the conversations." });
    }
  }

  find(sessionId: string | null): SessionSummary | undefined {
    return sessionId ? this.snapshot.sessions.find((s) => s.id === sessionId) : undefined;
  }

  sessionIdFor(threadId: string): string | null {
    return this.snapshot.links[threadId] ?? (this.find(threadId) ? threadId : null);
  }

  threadIdFor(sessionId: string): string {
    for (const [threadId, id] of Object.entries(this.snapshot.links)) if (id === sessionId) return threadId;
    return sessionId;
  }

  /**
   * Create the Session for a new chat. The server derives the create's
   * idempotency key from the id sent here, so it is random per chat and
   * reused when the same chat retries.
   */
  create(threadId: string, firstText: string): Promise<string> {
    const linked = this.sessionIdFor(threadId);
    if (linked) return Promise.resolve(linked);
    const running = this.creating.get(threadId);
    if (running) return running;
    let createId = this.createIds.get(threadId);
    if (!createId) {
      createId = `chat-${crypto.randomUUID()}`;
      this.createIds.set(threadId, createId);
    }
    const promise = api
      .createSession(createId, firstText)
      .then((summary) => {
        this.update({ links: { ...this.snapshot.links, [threadId]: summary.id } });
        this.replace(summary);
        return summary.id;
      })
      .finally(() => this.creating.delete(threadId));
    this.creating.set(threadId, promise);
    return promise;
  }

  /** Move a conversation to the top after a message. */
  touch(sessionId: string) {
    const current = this.find(sessionId);
    if (current) this.replace({ ...current, updatedAt: new Date().toISOString() });
  }

  async rename(sessionId: string, title: string): Promise<void> {
    this.replace(await api.renameSession(sessionId, title));
  }

  async archive(sessionId: string): Promise<void> {
    this.replace(await api.archiveSession(sessionId));
  }

  async remove(sessionId: string): Promise<void> {
    await api.deleteSession(sessionId);
    this.update({ sessions: this.snapshot.sessions.filter((s) => s.id !== sessionId) });
  }
}
