"use client";

// Page-level notices for a conversation, for failures that happen before it
// has a Session (and so a controller that could show them), or that leave an
// unsent message the composer could not take back. Kept per local thread id,
// so a notice never shows on, or restores into, another conversation.

import { useSyncExternalStore } from "react";

export type AppNotice = {
  message: string;
  /** A message that was not sent and could not be put back in the composer. */
  unsentText?: string;
  /** The operation that failed (`archive`, `rename`, ...). Its next success clears the notice. */
  op?: string;
};

const notices = new Map<string, AppNotice>();
const listeners = new Set<() => void>();

export function setAppNotice(threadId: string, notice: AppNotice | null): void {
  const current = notices.get(threadId);
  if (!notice && !current) return;
  if (notice) notices.set(threadId, notice);
  else notices.delete(threadId);
  for (const l of listeners) l();
}

/** An operation succeeded: clear the notice its earlier failure left, and nothing else. */
export function clearAppNotice(threadId: string, op: string): void {
  if (notices.get(threadId)?.op === op) setAppNotice(threadId, null);
}

export function getAppNotice(threadId: string): AppNotice | null {
  return notices.get(threadId) ?? null;
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function useAppNotice(threadId: string | undefined): AppNotice | null {
  return useSyncExternalStore(
    subscribe,
    () => (threadId ? (notices.get(threadId) ?? null) : null),
    () => null,
  );
}
