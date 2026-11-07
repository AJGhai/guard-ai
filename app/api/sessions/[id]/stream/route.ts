import { route } from "@/lib/zoowork/http";
import { relayErrorResponse, relayStream, resumeCursor, sseHeaders } from "@/lib/zoowork/relay";
import { getRuntime, ownedSession, type Runtime } from "@/lib/zoowork/runtime";

type Context = { params: Promise<{ id: string }> };

export const GET = route<Context>(async (request, { params }) => {
  const { id } = await params;
  let runtime: Runtime;
  try {
    runtime = await getRuntime();
    await ownedSession(runtime, id);
  } catch (error) {
    // Answer in SSE so the browser's EventSource can read the reason.
    return relayErrorResponse(error);
  }
  const stream = relayStream(runtime.client, runtime.agentId, id, resumeCursor(request), request.signal);
  return new Response(stream, { headers: sseHeaders() });
});
