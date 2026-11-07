// Readable summaries of tool result previews. The Platform sends each tool's
// result as a short preview string, cut at a fixed length, so a JSON result
// usually cannot be parsed. These helpers pull out only the complete fields.

export type SearchHit = { title: string; url: string };

const STRING = String.raw`"((?:[^"\\]|\\.)*)"`;
const TITLE_THEN_URL = new RegExp(String.raw`"title"\s*:\s*${STRING}\s*,\s*"(?:url|link)"\s*:\s*${STRING}`, "g");
const URL_THEN_TITLE = new RegExp(String.raw`"(?:url|link)"\s*:\s*${STRING}\s*,\s*"title"\s*:\s*${STRING}`, "g");

function unescape(value: string): string | undefined {
  try {
    return JSON.parse(`"${value}"`) as string;
  } catch {
    return undefined;
  }
}

/** Search results whose title and http(s) URL are both complete in the preview. */
export function searchHits(preview: string | undefined): SearchHit[] {
  if (!preview) return [];
  const hits: SearchHit[] = [];
  const seen = new Set<string>();
  const add = (rawTitle: string, rawUrl: string) => {
    const title = unescape(rawTitle)?.trim();
    const url = unescape(rawUrl)?.trim();
    if (!title || !url || !/^https?:\/\//i.test(url) || seen.has(url)) return;
    seen.add(url);
    hits.push({ title, url });
  };
  for (const m of preview.matchAll(TITLE_THEN_URL)) add(m[1]!, m[2]!);
  for (const m of preview.matchAll(URL_THEN_TITLE)) add(m[2]!, m[1]!);
  return hits;
}

/** A preview that is structured data rather than readable text. */
export function looksLikeJson(preview: string | undefined): boolean {
  const start = preview?.trimStart()[0];
  return start === "{" || start === "[";
}
