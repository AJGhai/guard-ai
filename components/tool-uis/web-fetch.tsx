"use client";

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ToolCard } from "./tool-card";

const httpUrl = (value: unknown) => (typeof value === "string" && /^https?:\/\//i.test(value) ? value : undefined);

/** web_fetch: the page it reads as a link, then the generic result preview. */
export const WebFetchCard: ToolCallMessagePartComponent = (props) => {
  const url = httpUrl(props.args.url);
  return (
    <ToolCard
      {...props}
      details={
        url ? (
          <p className="min-w-0 truncate text-sm">
            Reading{" "}
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="text-link underline-offset-2 hover:underline"
            >
              {url}
            </a>
          </p>
        ) : undefined
      }
    />
  );
};
