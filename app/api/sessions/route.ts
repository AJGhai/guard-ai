import { createSession, listSessions } from "@/lib/zoowork/chat";
import { readJson, route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

export const GET = route(async () => Response.json(await listSessions(await getRuntime())));

export const POST = route(
  async (request) => Response.json(await createSession(await getRuntime(), await readJson(request)), { status: 201 }),
  { write: true },
);
