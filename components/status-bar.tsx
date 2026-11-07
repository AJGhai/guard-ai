"use client";

import { RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/chat/api";
import { useChat } from "@/lib/chat/runtime-provider";
import { activityOf, agentPill, initialCheck, recordPoll, statusPill, type StatusPoll, type Tone } from "@/lib/chat/status";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;
/** A failure that may pass is checked again this soon before it is shown. */
const RECHECK_MS = 3_000;

const DOT: Record<Tone, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  error: "bg-destructive",
  idle: "bg-muted-foreground/50",
};

/**
 * The open conversation's title, its stream connection, and a status pill:
 * Agent readiness from /api/status, or what the conversation is doing while
 * the Agent is ready (lib/chat/status.ts).
 */
export function StatusBar({ leading }: { leading?: ReactNode }) {
  const { session, snapshot, controller, starting } = useChat();
  const [check, setCheck] = useState(initialCheck);
  const checkRef = useRef(initialCheck);
  const recheck = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const refresh = useCallback(async () => {
    clearTimeout(recheck.current);
    let poll: StatusPoll;
    try {
      poll = { status: await api.status() };
    } catch (e) {
      poll = {
        error: e instanceof ApiError ? { code: e.code, message: e.message } : { code: "network_error", message: "Could not read the app status." },
      };
    }
    const { check: next, recheckSoon } = recordPoll(checkRef.current, poll);
    checkRef.current = next;
    setCheck(next);
    if (recheckSoon) recheck.current = setTimeout(() => void refresh(), RECHECK_MS);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(timer);
      clearTimeout(recheck.current);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  const agent = agentPill(check);
  const pill = statusPill(agent, activityOf(snapshot, starting));
  const reconnecting = controller && snapshot.connection === "reconnecting";

  return (
    <header data-slot="status-bar" className="border-border bg-background flex flex-col border-b">
      <div className="flex h-12 items-center gap-3 px-4">
        {leading}
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">
          {session?.title ?? "New chat"}
          {session?.archived && (
            <span className="text-muted-foreground border-border ms-2 rounded-sm border px-1.5 py-0.5 text-xs font-normal">
              Archived
            </span>
          )}
        </h2>
        {reconnecting && (
          <span role="status" className="text-muted-foreground shimmer text-xs">
            Reconnecting…
          </span>
        )}
        <span role="status" className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs">
          <span aria-hidden className={cn("size-2 rounded-full", DOT[pill.tone], pill.active && "animate-pulse")} />
          {pill.label}
        </span>
      </div>
      {agent.detail && (
        <div
          className={cn(
            "flex items-center gap-3 border-t px-4 py-2 text-sm",
            agent.tone === "error"
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-warning/30 bg-warning/10",
          )}
        >
          <p className="min-w-0 flex-1">{agent.detail}</p>
          <Button size="xs" variant="outline" onClick={() => void refresh()}>
            <RefreshCwIcon aria-hidden />
            Check again
          </Button>
        </div>
      )}
    </header>
  );
}
