"use client";

// Tool cards by exact tool name. Add a card for one of your tools here; any
// tool without one uses the generic ToolCard.

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { ToolCard } from "./tool-card";
import { WebFetchCard } from "./web-fetch";
import { WebSearchCard } from "./web-search";

export const TOOL_CARDS: Record<string, ToolCallMessagePartComponent> = {
  web_search: WebSearchCard,
  web_fetch: WebFetchCard,
};

export const ToolCallPart: ToolCallMessagePartComponent = (props) => {
  const Card = TOOL_CARDS[props.toolName] ?? ToolCard;
  return <Card {...props} />;
};
