import { randomUUID } from "node:crypto";
import { ZooworkError } from "@zoowork-ai/sdk";
import type { ChatClient } from "./runtime";

const ENDED = new Set(["succeeded", "failed", "aborted", "cancelled"]);
const isActive = (runStatus: string | null | undefined) => !!runStatus && !ENDED.has(runStatus);

/**
 * Archive a Session on the Platform. The Platform is the source of truth, so
 * a retry after a partial failure finds the Session already archived and
 * returns. A run in flight is interrupted first, and the archive waits
 * (bounded) for it to end so the two do not race. The interrupt carries an
 * idempotency key, so a network retry of it is delivered once.
 */
export async function archiveOnPlatform(
  client: Pick<ChatClient, "getSession" | "postEvents" | "archiveSession">,
  agentId: string,
  sessionId: string,
  { waitMs = 10_000, pollMs = 500 }: { waitMs?: number; pollMs?: number } = {},
): Promise<void> {
  let session = await client.getSession(agentId, sessionId);
  if (session.archived) return;
  if (isActive(session.run_status)) {
    await client.postEvents(agentId, sessionId, [{ type: "user.interrupt", idempotency_key: `archive-${randomUUID()}` }]);
    const deadline = Date.now() + waitMs;
    while (isActive(session.run_status) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, pollMs));
      session = await client.getSession(agentId, sessionId);
    }
  }
  try {
    await client.archiveSession(agentId, sessionId);
  } catch (error) {
    // Archived by an earlier attempt that lost its response.
    if (!(error instanceof ZooworkError && error.status === 409 && error.type === "session_archived")) throw error;
  }
}

