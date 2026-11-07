import { ZooworkError } from "@zoowork-ai/sdk";

/**
 * One automatic retry for Platform calls that are safe to repeat.
 *
 * A slow or dropped connection to ZooWork Platform usually fails as a
 * network error after several seconds and often succeeds on the next
 * attempt. The SDK does not retry by itself, so the starter repeats a call
 * once, after a short pause, when both are true:
 *
 * - the failure happened in transit: a network error or timeout, or a
 *   gateway 502/503/504. An answer from the Platform (any 4xx, a 500) is
 *   never retried; repeating it would fail the same way.
 * - repeating the call cannot do anything twice: a read, a create that
 *   carries an idempotency key, events that each carry an idempotency key,
 *   or an archive (a second archive finds the Session already archived).
 *
 * Everything else, such as an Agent update or an approval decision, is sent
 * once. See "What is safe to retry" in the ZooWork errors reference.
 */
export const RETRY_DELAY_MS = 500;

const GATEWAY = new Set([502, 503, 504]);

/** A failure in transit that a second attempt can fix. */
export function isTransient(error: unknown): boolean {
  if (error instanceof ZooworkError) return GATEWAY.has(error.status);
  // An abort is a decision (the caller cancelled), not a fault.
  if (error instanceof Error && error.name === "AbortError") return false;
  // fetch rejects with a TypeError on DNS, connect and socket failures; a
  // TimeoutError when a deadline passes.
  return error instanceof TypeError || (error instanceof Error && error.name === "TimeoutError");
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Run `attempt`, and once more after `delayMs` if it failed in transit. */
export async function retryOnce<T>(attempt: () => Promise<T>, delayMs = RETRY_DELAY_MS): Promise<T> {
  try {
    return await attempt();
  } catch (error) {
    if (!isTransient(error)) throw error;
    await sleep(delayMs);
    return attempt();
  }
}

const hasKey = (value: unknown) => typeof value === "string" && value.length > 0;
const always = () => true;

/** The client methods retried, and when a call to each is safe to repeat. */
const REPEATABLE: Record<string, (args: unknown[]) => boolean> = {
  getAgent: always,
  getSession: always,
  listApprovals: always,
  listModels: always,
  archiveSession: always,
  // (input, idempotencyKey)
  createAgent: (args) => hasKey(args[1]),
  // (agentId, input, idempotencyKey)
  createSession: (args) => hasKey(args[2]),
  // (agentId, sessionId, events): only when every event carries its own key
  postEvents: (args) =>
    Array.isArray(args[2]) &&
    args[2].length > 0 &&
    args[2].every((event) => hasKey((event as { idempotency_key?: unknown } | null)?.idempotency_key)),
};

/**
 * The same client, with the calls above retried once on a transient
 * failure. Other methods, and calls that are not safe to repeat (a create
 * without a key, an event without one), pass through unchanged.
 */
export function withTransientRetry<C extends object>(client: C, { delayMs = RETRY_DELAY_MS } = {}): C {
  return new Proxy(client, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver) as unknown;
      const repeatable = typeof property === "string" && Object.hasOwn(REPEATABLE, property) ? REPEATABLE[property] : undefined;
      if (typeof value !== "function" || !repeatable) return value;
      return (...args: unknown[]) => {
        const call = () => (value as (...a: unknown[]) => Promise<unknown>).apply(target, args);
        return repeatable(args) ? retryOnce(call, delayMs) : call();
      };
    },
  });
}
