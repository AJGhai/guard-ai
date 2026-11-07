"use client";

import { Button } from "@/components/ui/button";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";
import { appConfig } from "@/app.config";
import { initialOf } from "@/lib/app-config";
import { useChat } from "@/lib/chat/runtime-provider";

/** Product name, the conversation list (with New chat and Archived) and the footnote. */
export function Sidebar() {
  const { index, reloadIndex } = useChat();
  return (
    <div data-slot="sidebar" className="bg-sidebar text-sidebar-foreground flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        {appConfig.icon ? (
          // A local image from public/, set in app.config.ts.
          <img src={appConfig.icon} alt="" aria-hidden className="size-7 shrink-0 rounded-md object-contain" />
        ) : (
          <span aria-hidden className="bg-primary text-primary-foreground grid size-7 shrink-0 place-items-center rounded-md text-sm font-semibold">
            {initialOf(appConfig.name)}
          </span>
        )}
        <h1 className="min-w-0 truncate text-sm font-semibold">{appConfig.name}</h1>
      </div>
      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ThreadList />
        {index.error && (
          <div role="alert" className="text-destructive flex flex-col items-start gap-2 px-2.5 py-3 text-sm">
            <p>{index.error}</p>
            <Button size="xs" variant="outline" onClick={reloadIndex}>
              Try again
            </Button>
          </div>
        )}
      </nav>
      <p className="text-muted-foreground border-sidebar-border border-t px-4 py-3 text-xs leading-relaxed">
        {appConfig.footnote}
      </p>
    </div>
  );
}
