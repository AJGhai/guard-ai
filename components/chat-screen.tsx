"use client";

import { PanelLeftIcon } from "lucide-react";
import { useState } from "react";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ConversationNotices } from "@/components/conversation-notices";
import { Sidebar } from "@/components/sidebar";
import { StatusBar } from "@/components/status-bar";
import { ToolCallPart } from "@/components/tool-uis";
import { Button } from "@/components/ui/button";
import { ChatRuntimeProvider, useChat } from "@/lib/chat/runtime-provider";
import { cn } from "@/lib/utils";

const THREAD_COMPONENTS = { ToolFallback: ToolCallPart, BeforeComposer: ConversationNotices };

/** The open conversation. It stays at the latest message while its history is still arriving. */
function ConversationThread() {
  const { snapshot, starting } = useChat();
  return (
    <Thread
      components={THREAD_COMPONENTS}
      autoScroll={snapshot.replaying}
      disabledPlaceholder={starting ? "Starting conversation…" : "This conversation is read-only."}
    />
  );
}

/** The page: conversation sidebar, status bar and the open conversation. */
export function ChatScreen() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <ChatRuntimeProvider>
      <div className="bg-background text-foreground flex h-dvh overflow-hidden">
        <aside
          className={cn(
            "border-sidebar-border w-72 shrink-0 border-e",
            "max-md:fixed max-md:inset-y-0 max-md:start-0 max-md:z-40 max-md:shadow-(--shadow-raised) max-md:transition-transform",
            sidebarOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full",
          )}
          onClick={(event) => {
            // Close the drawer after choosing a conversation on small screens.
            if ((event.target as HTMLElement).closest("[data-slot=aui_thread-list-item-trigger],[data-slot=aui_thread-list-new]")) {
              setSidebarOpen(false);
            }
          }}
        >
          <Sidebar />
        </aside>
        {sidebarOpen && (
          <div aria-hidden className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setSidebarOpen(false)} />
        )}
        <main className="flex min-w-0 flex-1 flex-col">
          <StatusBar
            leading={
              <Button
                size="icon-sm"
                variant="ghost"
                className="md:hidden"
                aria-label="Show conversations"
                onClick={() => setSidebarOpen(true)}
              >
                <PanelLeftIcon />
              </Button>
            }
          />
          <div className="min-h-0 flex-1">
            <ConversationThread />
          </div>
        </main>
      </div>
    </ChatRuntimeProvider>
  );
}
