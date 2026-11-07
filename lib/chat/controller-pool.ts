// The page's SessionControllers, one per conversation opened on this page,
// and which of them have a run in flight. The open conversation follows its
// stream; a conversation the user switched away from keeps following only
// while its run is in flight, so the sidebar can show it working. Each such
// stream holds a browser connection, so only a few stay open in the
// background; the oldest is closed beyond that (its run continues on the
// Platform, the sidebar just stops showing it).

import { isBusy, SessionController } from "./session-controller";

/** Background streams kept open at most. Browsers allow about six connections per host over HTTP/1.1. */
export const MAX_BACKGROUND = 3;

export class ControllerPool {
  private readonly controllers = new Map<string, SessionController>();
  private readonly unsubscribe = new Map<string, () => void>();
  /** Conversations released while busy, oldest first. */
  private background: string[] = [];
  private running: readonly string[] = [];
  private readonly listeners = new Set<() => void>();

  constructor(
    private readonly create: (sessionId: string) => SessionController = (id) => new SessionController(id),
    private readonly maxBackground = MAX_BACKGROUND,
  ) {}

  get(sessionId: string): SessionController {
    let controller = this.controllers.get(sessionId);
    if (!controller) {
      controller = this.create(sessionId);
      this.controllers.set(sessionId, controller);
      this.unsubscribe.set(sessionId, controller.subscribe(this.recompute));
    }
    return controller;
  }

  /** The conversation is on screen. */
  open(sessionId: string) {
    this.background = this.background.filter((id) => id !== sessionId);
    this.get(sessionId).open();
  }

  /** The conversation left the screen. It keeps following while its run is in flight. */
  release(sessionId: string) {
    const controller = this.controllers.get(sessionId);
    if (!controller) return;
    controller.release();
    if (!controller.following) return;
    this.background = [...this.background.filter((id) => id !== sessionId && this.controllers.get(id)?.following), sessionId];
    while (this.background.length > this.maxBackground) {
      this.controllers.get(this.background.shift()!)?.close();
    }
  }

  /** Forget a conversation (it was deleted). */
  remove(sessionId: string) {
    this.unsubscribe.get(sessionId)?.();
    this.unsubscribe.delete(sessionId);
    this.controllers.get(sessionId)?.dispose();
    this.controllers.delete(sessionId);
    this.background = this.background.filter((id) => id !== sessionId);
    this.recompute();
  }

  disposeAll() {
    for (const id of [...this.controllers.keys()]) this.remove(id);
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Session ids with a run in flight that this page is following, sorted. Stable until it changes. */
  getRunning = () => this.running;

  private recompute = () => {
    const running = [...this.controllers]
      .filter(([, c]) => c.following && isBusy(c.getSnapshot()))
      .map(([id]) => id)
      .sort();
    if (running.length === this.running.length && running.every((id, i) => id === this.running[i])) return;
    this.running = running;
    for (const listener of this.listeners) listener();
  };
}
