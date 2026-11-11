import { LockIcon } from "lucide-react";

import type { TicketMessage } from "@/lib/api/client";
import { initials } from "@/lib/roles";
import { formatDateTime, timeAgo } from "@/lib/tickets";
import { cn } from "@/lib/utils";

/** A ticket's messages, oldest first. Internal notes only ever reach staff. */
export function Conversation({
  messages,
  currentUserId,
  audience,
}: {
  messages: TicketMessage[];
  currentUserId: string;
  audience: "staff" | "customer";
}) {
  return (
    <ol className="space-y-4" aria-label="Conversation">
      {messages.map((message) => {
        const mine = message.author?.id === currentUserId;
        const name = mine ? "You" : (message.author?.full_name ?? "Former member");
        const fromCustomer = message.author_type === "customer";
        const tag = fromCustomer ? (audience === "staff" ? "Customer" : null) : "Support";
        return (
          <li key={message.id} className="flex gap-3">
            <span
              className={cn(
                "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                fromCustomer ? "bg-muted" : "bg-primary text-primary-foreground",
              )}
              aria-hidden
            >
              {initials(message.author?.full_name ?? "?")}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium">{name}</span>
                {tag ? <span className="text-xs text-muted-foreground">{tag}</span> : null}
                <time
                  className="text-xs text-muted-foreground"
                  dateTime={message.created_at}
                  title={formatDateTime(message.created_at)}
                >
                  {timeAgo(message.created_at)}
                </time>
              </div>
              <div
                className={cn(
                  "mt-1 rounded-lg border px-3 py-2 text-sm leading-relaxed break-words whitespace-pre-wrap",
                  message.is_internal
                    ? "border-dashed border-amber-400/70 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-950/30"
                    : fromCustomer
                      ? "bg-card"
                      : "border-primary/20 bg-primary/5",
                )}
              >
                {message.is_internal ? (
                  <div className="mb-1 flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400">
                    <LockIcon className="size-3" aria-hidden />
                    Internal note: only your team can see this
                  </div>
                ) : null}
                {message.body}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
