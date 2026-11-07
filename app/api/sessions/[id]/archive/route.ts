import { archiveSession } from "@/lib/zoowork/chat";
import { route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

type Context = { params: Promise<{ id: string }> };

export const POST = route<Context>(
  async (_request, { params }) => {
    const { id } = await params;
    return Response.json(await archiveSession(await getRuntime(), id));
  },
  { sameOrigin: true },
);
