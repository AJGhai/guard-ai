"use client";

import { ShieldQuestionIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChat } from "@/lib/chat/runtime-provider";
import type { Approval } from "@/lib/chat/reducer";

const RESOLUTION: Record<string, string> = {
  "allow-once": "Allowed once.",
  "allow-always": "Allowed for the rest of this conversation.",
  deny: "Denied.",
};

function resolutionText(approval: Approval): string {
  if (approval.state === "closed") return "The approval request ended without a decision.";
  return (approval.resolution && RESOLUTION[approval.resolution]) || "Resolved.";
}

const timeOf = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

/**
 * Allow / Deny for a tool call that waits for approval under the Agent's
 * tool policy. After a decision it shows the outcome the stream reported.
 */
export function ApprovalBar({ approvalId }: { approvalId: string }) {
  const { snapshot, controller, session } = useChat();
  const approval = snapshot.view.approvals[approvalId];
  if (!approval) return null;

  if (approval.state !== "pending") {
    return (
      <p data-slot="approval-outcome" className="text-muted-foreground text-xs">
        {resolutionText(approval)}
      </p>
    );
  }

  const deciding = snapshot.deciding[approvalId];
  const locked = !!deciding || !controller || !!session?.archived;
  const expires = approval.timeoutAt ? timeOf(approval.timeoutAt) : undefined;

  return (
    <div
      role="group"
      aria-label={`Approval needed for ${approval.toolName}`}
      data-slot="approval-bar"
      className="border-warning/40 bg-warning/10 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-3 py-2"
    >
      <ShieldQuestionIcon className="text-warning size-4 shrink-0" aria-hidden />
      <p className="min-w-0 flex-1 text-sm">
        The Agent wants to run <b className="font-medium">{approval.toolName}</b>.
        {expires && <span className="text-muted-foreground"> Waits until {expires}.</span>}
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={locked} onClick={() => void controller?.decide(approvalId, "allow-once")}>
          {deciding === "allow-once" ? "Allowing…" : "Allow"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={locked}
          onClick={() => void controller?.decide(approvalId, "deny")}
        >
          {deciding === "deny" ? "Denying…" : "Deny"}
        </Button>
      </div>
    </div>
  );
}
