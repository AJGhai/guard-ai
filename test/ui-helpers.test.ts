import assert from "node:assert/strict";
import { test } from "node:test";
import { brandVariables, initialOf, letterMarkSvg, pageIconUrl } from "../lib/app-config";
import { clearAppNotice, getAppNotice, setAppNotice } from "../lib/chat/app-notice";
import { blockedDetail, blockedLabel } from "../lib/chat/tool-outcome";

test("a blocked call says why: denied, stopped, or blocked by policy", () => {
  assert.equal(blockedLabel("approval-denied"), "Denied");
  assert.equal(blockedLabel("interrupted"), "Stopped");
  assert.equal(blockedLabel("approval-timeout"), "Not approved in time");
  assert.equal(blockedLabel("policy:deny-web"), "Blocked");
  assert.equal(blockedLabel(undefined), "Blocked");
  assert.equal(blockedDetail("approval-denied"), "Not run: the call was denied.");
  assert.equal(blockedDetail("policy:x"), "Not run (policy:x).");
});

test("an operation's success clears only the notice its own failure left", () => {
  setAppNotice("t1", { message: "Could not archive the conversation (request_failed)", op: "archive" });
  clearAppNotice("t1", "rename");
  assert.equal(getAppNotice("t1")?.op, "archive", "another operation leaves it");
  clearAppNotice("t1", "archive");
  assert.equal(getAppNotice("t1"), null);

  setAppNotice("t2", { message: "Your message was not sent.", unsentText: "hi" });
  clearAppNotice("t2", "archive");
  assert.equal(getAppNotice("t2")?.unsentText, "hi", "a notice holding an unsent message stays");
  setAppNotice("t2", null);
});

test("accentColor drives buttons and links", () => {
  assert.deepEqual(brandVariables({ accentColor: "#0f766e", accentForeground: "#fff" }), {
    "--primary": "#0f766e",
    "--primary-foreground": "#fff",
    "--zw-brand": "#0f766e",
  });
});

test("the page icon is the configured image, or a letter mark in the brand colors", () => {
  const config = { name: " wayfinder", accentColor: "#0f766e", accentForeground: "#ffffff" };
  assert.equal(pageIconUrl({ ...config, icon: "/logo.svg" }), "/logo.svg");
  const url = pageIconUrl(config);
  assert.ok(url.startsWith("data:image/svg+xml,"));
  const svg = decodeURIComponent(url.slice("data:image/svg+xml,".length));
  assert.match(svg, /fill="#0f766e"/);
  assert.match(svg, />W<\/text>/);
  assert.equal(initialOf("ünicorn"), "Ü");
  assert.equal(initialOf("  "), "?");
  // Config text is escaped into the SVG.
  assert.match(letterMarkSvg({ name: "<b>", accentColor: 'red" onload="x', accentForeground: "#fff" }), /&lt;/);
  assert.doesNotMatch(letterMarkSvg({ name: "A", accentColor: 'red" onload="x', accentForeground: "#fff" }), /" onload="/);
});

test("/favicon.ico answers with the letter mark instead of a 404", async () => {
  const { GET } = await import("../app/favicon.ico/route");
  const response = GET(new Request("http://localhost:3000/favicon.ico"));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "image/svg+xml");
  assert.match(await response.text(), /^<svg /);
});
