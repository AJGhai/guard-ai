import { resolveSessionApproval } from "@/lib/zoowork/chat";
import { readJson, route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

type Context = { params: Promise<{ id: string; approvalId: string }> };

const APPROVAL_ID = /^[A-Za-z0-9_-]{1,128}$/;

export const POST = route<Context>(
  async (request, { params }) => {
    const { id, approvalId } = await params;
    if (!APPROVAL_ID.test(approvalId)) return Response.json({ error: "approval_not_pending" }, { status: 409 });
    return Response.json(await resolveSessionApproval(await getRuntime(), id, approvalId, await readJson(request)));
  },
  { write: true },
);
