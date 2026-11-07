import { deleteSession, getSessionSummary, renameSession } from "@/lib/zoowork/chat";
import { readJson, route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

type Context = { params: Promise<{ id: string }> };

export const GET = route<Context>(async (_request, { params }) => {
  const { id } = await params;
  return Response.json(await getSessionSummary(await getRuntime(), id));
});

export const PATCH = route<Context>(
  async (request, { params }) => {
    const { id } = await params;
    return Response.json(await renameSession(await getRuntime(), id, await readJson(request)));
  },
  { write: true },
);

export const DELETE = route<Context>(
  async (_request, { params }) => {
    const { id } = await params;
    await deleteSession(await getRuntime(), id);
    return new Response(null, { status: 204 });
  },
  { sameOrigin: true },
);
