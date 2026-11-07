import { appConfig } from "@/app.config";
import { letterMarkSvg } from "@/lib/app-config";

// Some clients ask for /favicon.ico whatever the page declares. Answer with
// the app.config.ts icon, or the letter mark, instead of a 404.
export function GET(request: Request): Response {
  if (appConfig.icon) return Response.redirect(new URL(appConfig.icon, request.url), 307);
  return new Response(letterMarkSvg(appConfig), {
    headers: { "content-type": "image/svg+xml", "cache-control": "public, max-age=3600" },
  });
}
