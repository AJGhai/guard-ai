"use client";

import type { ToolCallMessagePartProps, ToolCallMessagePartStatus } from "@assistant-ui/react";
import { useEffect, useState, type ReactNode } from "react";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";
import type { ToolActivity } from "@/lib/chat/reducer";
import { blockedDetail, blockedLabel } from "@/lib/chat/tool-outcome";
import { looksLikeJson } from "@/lib/chat/tool-preview";
import { ApprovalBar } from "./approval-bar";

/** The reducer's state for this call, carried on the part as its artifact. */
export const toolActivity = (props: ToolCallMessagePartProps): ToolActivity | undefined =>
  props.artifact as ToolActivity | undefined;

function triggerStatus(tool: ToolActivity | undefined, status: ToolCallMessagePartStatus): ToolCallMessagePartStatus {
  if (tool?.phase === "blocked") return { type: "incomplete", reason: "cancelled" };
  if (tool?.phase === "failed") return { type: "incomplete", reason: "error" };
  return status;
}

/** A tool result preview. The Platform cuts previews at a fixed length, so JSON is shown raw. */
export function ResultPreview({ text }: { text: string | undefined }) {
  if (!text) return null;
  return (
    <div data-slot="tool-result-preview">
      <p className="text-muted-foreground text-xs font-medium">Result preview</p>
      <pre
        className={
          looksLikeJson(text)
            ? "bg-muted/60 text-foreground/90 mt-1 max-h-48 overflow-auto rounded-md p-2.5 font-mono text-xs break-words whitespace-pre-wrap"
            : "bg-muted/60 text-foreground/90 mt-1 max-h-48 overflow-auto rounded-md p-2.5 font-sans text-xs break-words whitespace-pre-wrap"
        }
      >
        {text}
      </pre>
    </div>
  );
}

export type ToolCardProps = ToolCallMessagePartProps & {
  /** Replaces the raw arguments. */
  details?: ReactNode;
  /** Replaces the raw result preview once the call succeeded. */
  preview?: ReactNode;
};

/**
 * The card for one tool call: name and state, arguments, the approval bar
 * when the call waits for a decision, and the result. Opens by itself while
 * an approval is pending.
 */
export function ToolCard(props: ToolCardProps) {
  const { toolName, argsText, status, details, preview } = props;
  const tool = toolActivity(props);
  const awaiting = tool?.phase === "awaiting_approval";
  const [open, setOpen] = useState(awaiting);
  useEffect(() => {
    if (awaiting) setOpen(true);
  }, [awaiting]);

  return (
    <ToolFallback.Root open={open} onOpenChange={setOpen}>
      <ToolFallback.Trigger
        toolName={toolName}
        status={triggerStatus(tool, status)}
        {...(tool?.phase === "blocked" ? { label: blockedLabel(tool.blockedReason) } : {})}
      />
      <ToolFallback.Content>
        {details ?? <ToolFallback.Args argsText={argsText} />}
        {tool?.approvalId && <ApprovalBar approvalId={tool.approvalId} />}
        {tool?.phase === "blocked" && (
          <p className="text-muted-foreground text-xs">{blockedDetail(tool.blockedReason)}</p>
        )}
        {tool?.phase === "failed" && <p className="text-destructive text-xs">The tool returned an error.</p>}
        {tool?.phase === "succeeded" && preview ? preview : null}
        {(tool?.phase === "failed" || (tool?.phase === "succeeded" && !preview)) && (
          <ResultPreview text={tool.resultPreview} />
        )}
      </ToolFallback.Content>
    </ToolFallback.Root>
  );
}
