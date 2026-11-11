import { ArrowUpIcon, ChevronsUpIcon, MinusIcon, ArrowDownIcon } from "lucide-react";

import type { TicketPriority, TicketStatus } from "@/lib/api/client";
import {
  CUSTOMER_STATUS_LABELS,
  PRIORITY_LABELS,
  PRIORITY_STYLES,
  STATUS_DOT,
  STATUS_LABELS,
} from "@/lib/tickets";
import { cn } from "@/lib/utils";

export function StatusBadge({
  status,
  audience = "staff",
  className,
}: {
  status: TicketStatus;
  audience?: "staff" | "customer";
  className?: string;
}) {
  const label = (audience === "customer" ? CUSTOMER_STATUS_LABELS : STATUS_LABELS)[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        className,
      )}
    >
      <span className={cn("size-2 shrink-0 rounded-full", STATUS_DOT[status])} aria-hidden />
      {label}
    </span>
  );
}

const PRIORITY_ICONS = {
  low: ArrowDownIcon,
  normal: MinusIcon,
  high: ArrowUpIcon,
  urgent: ChevronsUpIcon,
} as const;

export function PriorityLabel({
  priority,
  className,
}: {
  priority: TicketPriority;
  className?: string;
}) {
  const Icon = PRIORITY_ICONS[priority];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs whitespace-nowrap",
        PRIORITY_STYLES[priority],
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {PRIORITY_LABELS[priority]}
    </span>
  );
}
