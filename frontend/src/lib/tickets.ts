import type { TicketPriority, TicketStatus } from "@/lib/api/client";

export const STATUS_LABELS: Record<TicketStatus, string> = {
  open: "Open",
  assigned: "Assigned",
  in_progress: "In progress",
  waiting_for_customer: "Waiting on customer",
  resolved: "Resolved",
  closed: "Closed",
};

/** Customers see their tickets from their own point of view. */
export const CUSTOMER_STATUS_LABELS: Record<TicketStatus, string> = {
  ...STATUS_LABELS,
  open: "Received",
  assigned: "Received",
  waiting_for_customer: "Waiting on you",
};

export const ACTIVE_STATUSES: TicketStatus[] = [
  "open",
  "assigned",
  "in_progress",
  "waiting_for_customer",
];

export const PRIORITY_LABELS: Record<TicketPriority, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

export const PRIORITIES = Object.keys(PRIORITY_LABELS) as TicketPriority[];

// Status colors: a dot plus text, so meaning never relies on color alone.
export const STATUS_DOT: Record<TicketStatus, string> = {
  open: "bg-sky-500",
  assigned: "bg-indigo-500",
  in_progress: "bg-amber-500",
  waiting_for_customer: "bg-violet-500",
  resolved: "bg-emerald-500",
  closed: "bg-muted-foreground/50",
};

export const PRIORITY_STYLES: Record<TicketPriority, string> = {
  low: "text-muted-foreground",
  normal: "text-foreground",
  high: "text-orange-600 dark:text-orange-400",
  urgent: "font-semibold text-red-600 dark:text-red-400",
};

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "5 minutes ago", "yesterday", ... */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
