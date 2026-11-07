import { errorCode, errorStatus, StarterError } from "./errors";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

export type GuardOptions = {
  /** Reject cross-origin requests (any state-changing route). */
  sameOrigin?: boolean;
  /** Also require a JSON body (routes that read one). Implies sameOrigin. */
  write?: boolean;
};

/**
 * Local-only guard for every API route. The starter has no end-user
 * authentication, so it answers only on loopback hosts: a request that
 * reaches it through another host name (a tunnel, a LAN address, a public
 * deployment) is refused. Remove this only after adding authentication and
 * per-user Session ownership (README: "Before you go public").
 *
 * State-changing routes also refuse other origins, including the opaque
 * `Origin: null`. Routes with a body require JSON, which a cross-site form
 * cannot send.
 */
export function guard(request: Request, { sameOrigin = false, write = false }: GuardOptions = {}): void {
  const host = request.headers.get("host") ?? new URL(request.url).host;
  const hostname = host.startsWith("[") ? host.slice(0, host.indexOf("]") + 1) : host.split(":")[0]!;
  if (!LOOPBACK.has(hostname)) throw new StarterError("local_only_add_authentication_first", 403);
  if (!sameOrigin && !write) return;
  const origin = request.headers.get("origin");
  if (origin !== null) {
    let originHost: string | undefined;
    try {
      originHost = new URL(origin).host;
    } catch {
      originHost = undefined;
    }
    if (originHost !== host) throw new StarterError("cross_origin_request", 403);
  }
  if (write && !request.headers.get("content-type")?.includes("application/json")) {
    throw new StarterError("expected_application_json", 415);
  }
}

export async function readJson(request: Request): Promise<Record<string, unknown>> {
  const body = (await request.json().catch(() => null)) as unknown;
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new StarterError("invalid_json_body");
  return body as Record<string, unknown>;
}

/** A safe JSON error: a stable code, never a provider body or credential. */
export function errorResponse(error: unknown): Response {
  const code = errorCode(error);
  if (!(error instanceof StarterError)) console.error(`[starter] ${code}`);
  return Response.json({ error: code }, { status: errorStatus(error) });
}

/** Wrap a route handler with the guard and error mapping. */
export function route<C>(
  handler: (request: Request, context: C) => Promise<Response>,
  options: GuardOptions = {},
): (request: Request, context: C) => Promise<Response> {
  return async (request, context) => {
    try {
      guard(request, options);
      return await handler(request, context);
    } catch (error) {
      return errorResponse(error);
    }
  };
}
