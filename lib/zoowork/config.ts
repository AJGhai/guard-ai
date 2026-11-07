import { StarterError } from "./errors";

/**
 * Read the server-side Platform Project key. Work organization keys (`zct_`)
 * are rejected.
 */
export function readApiKey(env: NodeJS.ProcessEnv = process.env): string {
  const apiKey = env.ZOOWORK_API_KEY?.trim();
  if (!apiKey || !apiKey.startsWith("zwp_live_") || apiKey.length <= "zwp_live_".length) {
    throw new StarterError("platform_project_key_required", 500);
  }
  return apiKey;
}
