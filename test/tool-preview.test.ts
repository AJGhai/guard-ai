import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { looksLikeJson, searchHits } from "../lib/chat/tool-preview";

// Two web_search result previews recorded from production on 2026-10-22.
const previews = JSON.parse(readFileSync("test/fixtures/web-search-previews.json", "utf8")) as string[];

test("extracts the complete search results from a truncated production preview", () => {
  const hits = searchHits(previews[0]);
  assert.ok(hits.length >= 1);
  assert.deepEqual(hits[0], { title: "RFC 9114: HTTP/3", url: "https://www.rfc-editor.org/info/rfc9114/" });
  for (const preview of previews) {
    assert.ok(looksLikeJson(preview));
    assert.throws(() => JSON.parse(preview), "the preview is cut, so it is not parseable JSON");
    for (const hit of searchHits(preview)) assert.match(hit.url, /^https?:\/\//);
  }
});

test("ignores a result cut before its URL, non-http URLs and duplicates", () => {
  const preview = '{"results":[{"title":"A \\"quoted\\" one","url":"https://a.example/x"},{"url":"https://a.example/x","title":"dup"},{"title":"bad","url":"javascript:alert(1)"},{"title":"cut","url":"https://b.exa';
  assert.deepEqual(searchHits(preview), [{ title: 'A "quoted" one', url: "https://a.example/x" }]);
  assert.deepEqual(searchHits(undefined), []);
  assert.equal(looksLikeJson("plain page text"), false);
});
