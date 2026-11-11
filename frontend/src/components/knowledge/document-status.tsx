import { CircleAlertIcon, CircleCheckIcon, ClockIcon, Loader2Icon } from "lucide-react";

import type { KnowledgeDocument } from "@/lib/api/client";
import { DOCUMENT_STATUS_LABELS } from "@/lib/documents";
import { cn } from "@/lib/utils";

const ICONS = {
  queued: ClockIcon,
  processing: Loader2Icon,
  indexed: CircleCheckIcon,
  failed: CircleAlertIcon,
} as const;

const STYLES = {
  queued: "text-muted-foreground",
  processing: "text-sky-700 dark:text-sky-400",
  indexed: "text-emerald-700 dark:text-emerald-400",
  failed: "text-destructive",
} as const;

export function DocumentStatusBadge({
  status,
  className,
}: {
  status: KnowledgeDocument["status"];
  className?: string;
}) {
  const Icon = ICONS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLES[status],
        className,
      )}
    >
      <Icon className={cn("size-3.5", status === "processing" && "animate-spin")} aria-hidden />
      {DOCUMENT_STATUS_LABELS[status]}
    </span>
  );
}
