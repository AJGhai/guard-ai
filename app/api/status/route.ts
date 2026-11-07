import { appStatus } from "@/lib/zoowork/chat";
import { route } from "@/lib/zoowork/http";
import { getRuntime } from "@/lib/zoowork/runtime";

export const GET = route(async () => Response.json(await appStatus(getRuntime)));
