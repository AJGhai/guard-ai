"use client";

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { searchHits } from "@/lib/chat/tool-preview";
import { ResultPreview, ToolCard, toolActivity } from "./tool-card";

const host = (url: string) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** web_search: the query, and the results whose title and URL are complete in the preview. */
export const WebSearchCard: ToolCallMessagePartComponent = (props) => {
  const tool = toolActivity(props);
  const query = typeof props.args.query === "string" ? props.args.query : undefined;
  const hits = searchHits(tool?.resultPreview);

  return (
    <ToolCard
      {...props}
      details={query ? <p className="text-sm">Searching for “{query}”</p> : undefined}
      preview={
        hits.length ? (
          <ol data-slot="web-search-hits" className="flex flex-col gap-1.5">
            {hits.map((hit) => (
              <li key={hit.url} className="min-w-0">
                <a
                  href={hit.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="text-link block truncate text-sm underline-offset-2 hover:underline"
                >
                  {hit.title}
                </a>
                <span className="text-muted-foreground block truncate text-xs">{host(hit.url)}</span>
              </li>
            ))}
          </ol>
        ) : (
          <ResultPreview text={tool?.resultPreview} />
        )
      }
    />
  );
};
