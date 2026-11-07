"use client";

import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { setAppNotice, useAppNotice } from "@/lib/chat/app-notice";
import { useChat } from "@/lib/chat/runtime-provider";

/**
 * Notices for the open conversation, shown above the composer: a failure
 * that kept an unsent message (with Restore), the last stream or action
 * error, progress of a first message or a stop, and the read-only state of
 * an archived conversation.
 */
export function ConversationNotices() {
  const { threadId, session, snapshot, controller, starting, restoreDraft } = useChat();
  const notice = useAppNotice(threadId);

  return (
    <div data-slot="conversation-notices" className="flex flex-col gap-2 empty:hidden">
      {notice && (
        <div role="alert" className="border-destructive/30 bg-destructive/10 flex items-center gap-3 rounded-md border px-3 py-2 text-sm">
          <p className="text-destructive min-w-0 flex-1">{notice.message}</p>
          {notice.unsentText && (
            <Button
              size="xs"
              variant="outline"
              onClick={() => {
                restoreDraft(notice.unsentText!);
                setAppNotice(threadId, null);
              }}
            >
              Restore message
            </Button>
          )}
          <Button size="icon-xs" variant="ghost" aria-label="Dismiss" onClick={() => setAppNotice(threadId, null)}>
            <XIcon />
          </Button>
        </div>
      )}
      {snapshot.notice && !(notice && notice.message === snapshot.notice) && (
        <div role="alert" className="border-destructive/30 bg-destructive/10 flex items-center gap-3 rounded-md border px-3 py-2 text-sm">
          <p className="text-destructive min-w-0 flex-1">{snapshot.notice}</p>
          <Button size="icon-xs" variant="ghost" aria-label="Dismiss" onClick={() => controller?.dismissNotice()}>
            <XIcon />
          </Button>
        </div>
      )}
      {starting && (
        <p role="status" className="text-muted-foreground shimmer px-1 text-xs">
          Starting conversation…
        </p>
      )}
      {snapshot.stopping && (
        <p role="status" className="text-muted-foreground px-1 text-xs">
          Stopping…
        </p>
      )}
      {session?.archived && (
        <p className="text-muted-foreground border-border bg-muted/50 rounded-md border px-3 py-2 text-sm">
          This conversation is archived. It stays readable but accepts no new messages. Start a new chat to continue.
        </p>
      )}
    </div>
  );
}
