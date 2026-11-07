import { interruptSession } from "@/lib/zoowork/chat";
import { readJson, route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

type Context = { params: Promise<{ id: string }> };

export const POST = route<Context>(
  async (request, { params }) => {
    const { id } = await params;
    return Response.json(await interruptSession(await getRuntime(), id, await readJson(request)), { status: 202 });
  },
  { write: true },
);
