import { ZooworkError } from "@zoowork-ai/sdk";

/**
 * A failure with a stable, printable code. Codes are safe to show in a
 * terminal or a browser: they never carry keys, request bodies or provider
 * error text. The README's troubleshooting table is keyed by these codes.
 */
export class StarterError extends Error {
  constructor(
    readonly code: string,
    readonly status = 400,
  ) {
    super(code);
  }
}

/** Reduce any thrown value to a safe code. */
export function errorCode(error: unknown): string {
  if (error instanceof StarterError) return error.code;
  if (error instanceof ZooworkError) return `http_${error.status}`;
  if (error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError")) {
    return "timeout_or_cancelled";
  }
  return "request_failed";
}

/** HTTP status for a route response. Platform 4xx keep their status; anything else is 502. */
export function errorStatus(error: unknown): number {
  if (error instanceof StarterError) return error.status;
  if (error instanceof ZooworkError && error.status >= 400 && error.status < 500) return error.status;
  return 502;
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ZooworkError && error.status === 404;
}
