// What happens when the composer sends. Kept free of React so the failure
// paths can be tested directly.

import { ApiError } from "./api";
import type { AppNotice } from "./app-notice";

export type ComposerSend = {
  text: string;
  /** The open conversation's Session, or null for a new chat. */
  sessionId: string | null;
  /** Create the Session for a new chat; returns its id. */
  ensureSession: (firstText: string) => Promise<string>;
  /** Send through the Session's controller. It shows its own notice on failure. */
  send: (sessionId: string, text: string) => Promise<void>;
  /**
   * Put the unsent text back in this conversation's composer if that is safe
   * (see restoreIfSafe). Returns whether it did.
   */
  restoreText: (text: string) => boolean;
  /** Show or clear this conversation's page notice. */
  setNotice: (notice: AppNotice | null) => void;
  /**
   * Show the first message of a new chat as a pending bubble while its
   * Session is created (the composer stays disabled), or clear it with null.
   */
  setStarting?: (text: string | null) => void;
};

/** What the composer looks like when a send fails. */
export type ComposerTarget = {
  /** The failed send's conversation is still the one on screen. */
  isActive: boolean;
  /** The composer's text now. */
  text: string;
  setText: (text: string) => void;
};

/**
 * Put an unsent message back only into its own conversation, and only if
 * the user has not started another draft while waiting.
 */
export function restoreIfSafe(target: ComposerTarget, unsent: string): boolean {
  if (!target.isActive || target.text.trim() !== "") return false;
  target.setText(unsent);
  return true;
}

/**
 * Never throws: a failure keeps the unsent text, either in the composer or
 * in the conversation's notice with a Restore action, rather than losing it
 * as an unhandled rejection. Sending the same text again reuses the failed
 * attempt's idempotency key, so it cannot be delivered twice.
 *
 * A new chat's first message shows at once as a pending bubble while the
 * Session is created. On failure the bubble goes away before the text is
 * restored, so the text ends up in exactly one place.
 */
export async function sendFromComposer(input: ComposerSend): Promise<boolean> {
  const { text, ensureSession, send, restoreText, setNotice, setStarting = () => {} } = input;
  setNotice(null);
  let sessionId = input.sessionId;
  if (!sessionId) {
    setStarting(text);
    try {
      sessionId = await ensureSession(text);
    } catch (error) {
      setStarting(null);
      const message =
        error instanceof ApiError
          ? error.message
          : "Could not start the conversation. Check that npm run dev is still running, then send again.";
      setNotice(restoreText(text) ? { message } : { message, unsentText: text });
      return false;
    }
  }
  // send() shows its own outgoing bubble before it awaits, so the pending
  // one is cleared only after that, without a frame where neither shows.
  const sending = send(sessionId, text);
  if (!input.sessionId) setStarting(null);
  try {
    await sending;
    return true;
  } catch {
    // The controller shows why. Keep the text where the user can get it back.
    if (!restoreText(text)) setNotice({ message: "Your message was not sent. Your new draft was kept.", unsentText: text });
    return false;
  }
}
