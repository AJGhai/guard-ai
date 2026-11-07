import type { SessionEvent } from "@zoowork-ai/sdk";
import type { RelayEnvelope, WireEvent } from "../chat/wire";
import { errorCode, errorStatus } from "./errors";
import type { ChatClient } from "./runtime";

/** Comment frame interval. Proxies and browsers drop SSE connections that stay silent too long. */
const KEEPALIVE_MS = 15_000;
/** Reconnect delay the browser's EventSource uses after the response ends. */
const RETRY_MS = 2_000;
/** Platform cursors are opaque tokens such as `pse1:42`. Anything else is not forwarded. */
const CURSOR = /^[A-Za-z0-9._:~+/=-]{1,256}$/;

const encoder = new TextEncoder();

/**
 * The cursor to resume from. EventSource sends the last received `id:` as
 * `Last-Event-ID` when it reconnects by itself; a page that opens a new
 * EventSource passes it as `?cursor=` instead. Malformed values are ignored.
 */
export function resumeCursor(request: Request): string | undefined {
  const header = request.headers.get("last-event-id")?.trim();
  if (header && CURSOR.test(header)) return header;
  const query = new URL(request.url).searchParams.get("cursor")?.trim();
  if (query && CURSOR.test(query)) return query;
  return undefined;
}

function toWire(event: SessionEvent): WireEvent {
  return {
    seq: event.seq,
    eventType: event.eventType,
    payload: event.payload ?? {},
    ...(event.runId ? { runId: event.runId } : {}),
    ...(event.createdAt ? { createdAt: event.createdAt } : {}),
    ...(event.processedAt !== undefined ? { processedAt: event.processedAt } : {}),
  };
}

function frameText(envelope: RelayEnvelope, id?: string): string {
  return `${id ? `id: ${id}\n` : ""}data: ${JSON.stringify(envelope)}\n\n`;
}

const frame = (envelope: RelayEnvelope, id?: string) => encoder.encode(frameText(envelope, id));

/** A 4xx other than timeout or rate limiting will fail the same way on every reconnect. */
export function isFatal(error: unknown): boolean {
  const status = errorStatus(error);
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

/** An SSE response that carries one error frame. Used when the stream cannot start. */
export function relayErrorResponse(error: unknown): Response {
  const body = frameText({ kind: "relay_error", code: errorCode(error), fatal: isFatal(error) });
  return new Response(body, { headers: sseHeaders() });
}

export function sseHeaders(): HeadersInit {
  return {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  };
}

/**
 * Relay one Session's durable event stream as SSE. Without a cursor the
 * Platform replays the Session from its first event and then continues live,
 * so the same stream serves history and updates. Each event frame carries its
 * Platform cursor as the SSE id. When the upstream stream ends (the Platform
 * closes idle streams), the response ends too and the browser reconnects from
 * the last id. An upstream failure becomes one `relay_error` frame.
 */
export function relayStream(
  client: Pick<ChatClient, "streamEvents">,
  agentId: string,
  sessionId: string,
  cursor: string | undefined,
  signal?: AbortSignal,
): ReadableStream<Uint8Array> {
  const upstream = new AbortController();
  const abort = () => upstream.abort();
  signal?.addEventListener("abort", abort, { once: true });

  let events: AsyncIterator<SessionEvent> | undefined;
  let next: Promise<IteratorResult<SessionEvent>> | undefined;

  const finish = (controller: ReadableStreamDefaultController<Uint8Array>) => {
    signal?.removeEventListener("abort", abort);
    upstream.abort();
    try {
      controller.close();
    } catch {
      // Already closed or cancelled.
    }
  };

  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(`retry: ${RETRY_MS}\n\n`));
    },
    async pull(controller) {
      if (upstream.signal.aborted) return finish(controller);
      events ??= client
        .streamEvents(agentId, sessionId, { ...(cursor ? { cursor } : {}), signal: upstream.signal })
        [Symbol.asyncIterator]();
      next ??= events.next();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const tick = new Promise<"tick">((resolve) => {
        timer = setTimeout(() => resolve("tick"), KEEPALIVE_MS);
      });
      try {
        const result = await Promise.race([next, tick]);
        if (result === "tick") {
          controller.enqueue(encoder.encode(": keepalive\n\n"));
          return;
        }
        next = undefined;
        if (result.done) return finish(controller);
        controller.enqueue(frame({ kind: "event", event: toWire(result.value) }, result.value.cursor));
      } catch (error) {
        next = undefined;
        if (!upstream.signal.aborted) {
          if (!isFatal(error)) console.error(`[starter] relay ${errorCode(error)}`);
          controller.enqueue(frame({ kind: "relay_error", code: errorCode(error), fatal: isFatal(error) }));
        }
        finish(controller);
      } finally {
        clearTimeout(timer);
      }
    },
    cancel() {
      signal?.removeEventListener("abort", abort);
      upstream.abort();
      void events?.return?.()?.catch(() => {});
    },
  });
}
